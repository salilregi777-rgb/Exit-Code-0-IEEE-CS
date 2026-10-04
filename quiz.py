"""Server-scored rapid fire; reveal answers only after that team's attempt locks."""
from datetime import datetime, timedelta, timezone
from database import get_db_connection

QUIZ_MINUTES = 7
# Unanswered question keys stay out of static assets and participant HTML/JSON.
QUESTIONS = [
    ('R01', 'In Python, what does print(7 // 2) display?', ['3.5', '3', '4', '2'], 1),
    ('R02', 'In C, what is printed by printf("%d", 7 % 3)?', ['2', '3', '1', '0'], 2),
    ('R03', 'In Python, what does print("3" * 2) display?', ['6', '33', '32', 'An error'], 1),
    ('R04', 'In C, which operator tests whether a and b are equal?', ['a = b', 'a != b', 'a == b', 'a <= b'], 2),
    ('R05', 'In Python, how many times does for i in range(1, 4): run?', ['4', '3', '2', '5'], 1),
    ('R06', 'In C, int values[] = {4, 8, 12}; What is values[1]?', ['4', '8', '12', '1'], 1),
    ('R07', 'In Python, what does print(bool("False")) display?', ['True', 'False', 'None', 'An error'], 0),
    ('R08', 'In C, what does printf("%d", 2 + 3 * 4) display?', ['20', '24', '14', '9'], 2),
    ('R09', 'In Python, what does print(len([0, False, ""])) display?', ['0', '3', '2', '1'], 1),
    ('R10', 'In C, int x = 5; x += 2; What is x now?', ['2', '5', '7', '10'], 2),
]
QUESTION_SECONDS = QUIZ_MINUTES * 60 // len(QUESTIONS)


def _now():
    return datetime.now(timezone.utc)


def _parse(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _advance_expired(cur, team_id, session, now, closed=False):
    """Persist timeout transitions so a refresh cannot reset a question clock."""
    if session is None or session["completed_at"]:
        return
    count = cur.execute("SELECT COUNT(*) FROM quiz_answers WHERE team_id = ?", (team_id,)).fetchone()[0]
    question_start = _parse(session["question_started_at"])
    ended = now >= _parse(session["ends_at"]) or closed
    while count < len(QUESTIONS) and (ended or now >= question_start + timedelta(seconds=QUESTION_SECONDS)):
        cur.execute("INSERT OR IGNORE INTO quiz_answers (team_id, question_id, answer_index, points) VALUES (?, ?, NULL, 0)",
                    (team_id, QUESTIONS[count][0]))
        count += 1
        question_start += timedelta(seconds=QUESTION_SECONDS)
    cur.execute("UPDATE quiz_sessions SET question_started_at = ? WHERE team_id = ?", (question_start.isoformat(), team_id))
    if count == len(QUESTIONS):
        cur.execute("UPDATE quiz_sessions SET completed_at = ? WHERE team_id = ?", (now.isoformat(), team_id))


def _snapshot(cur, team_id, now):
    controls = cur.execute("SELECT quiz_status FROM competition_controls WHERE id = 1").fetchone()
    status = controls["quiz_status"]
    session = cur.execute("SELECT * FROM quiz_sessions WHERE team_id = ?", (team_id,)).fetchone()
    _advance_expired(cur, team_id, session, now, status == "CLOSED")
    session = cur.execute("SELECT * FROM quiz_sessions WHERE team_id = ?", (team_id,)).fetchone()
    answers = cur.execute("SELECT COUNT(*) AS count, COALESCE(SUM(points), 0) AS score FROM quiz_answers WHERE team_id = ?", (team_id,)).fetchone()
    count, score = answers["count"], answers["score"]
    completed = bool(session and session["completed_at"])
    question = None
    remaining = question_remaining = 0
    if session and not completed and status == "OPEN":
        qid, prompt, choices, _ = QUESTIONS[count]
        question = {"id": qid, "prompt": prompt, "options": choices, "number": count + 1}
        remaining = max(0, int((_parse(session["ends_at"]) - now).total_seconds()))
        question_remaining = max(0, min(remaining, int((_parse(session["question_started_at"]) + timedelta(seconds=QUESTION_SECONDS) - now).total_seconds())))
    saved = {row["question_id"]: row for row in cur.execute("SELECT question_id, answer_index, points FROM quiz_answers WHERE team_id = ?", (team_id,))}
    review = []
    for number, (qid, prompt, options, correct_index) in enumerate(QUESTIONS, 1):
        if qid in saved:
            answer = saved[qid]
            review.append({"question_id": qid, "number": number, "prompt": prompt,
                           "selected_answer": options[answer["answer_index"]] if answer["answer_index"] is not None else None,
                           "selected_index": answer["answer_index"], "options": options,
                           "correct_index": correct_index, "correct_answer": options[correct_index],
                           "status": "unanswered" if answer["answer_index"] is None else "correct" if answer["points"] else "incorrect",
                           "points": answer["points"]})
    return {"status": status, "started": session is not None, "completed": completed, "answers": review,
            "quiz_score": score, "quiz_total": len(QUESTIONS), "answered_count": count,
            "duration_minutes": QUIZ_MINUTES, "question_seconds": QUESTION_SECONDS,
            "remaining_seconds": remaining, "question_remaining_seconds": question_remaining,
            "current_question": question}


def quiz_snapshot(team_id):
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        snapshot = _snapshot(conn.cursor(), team_id, _now())
        conn.commit()
        return snapshot
    finally:
        conn.close()


def start_quiz(team_id):
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        if cur.execute("SELECT event_status FROM event_state WHERE id = 1").fetchone()[0] != "COMPLETED":
            return None, "The quiz opens after debugging is complete."
        if cur.execute("SELECT quiz_status FROM competition_controls WHERE id = 1").fetchone()[0] != "OPEN":
            return None, "The organizers have not opened the quiz."
        now = _now()
        cur.execute("INSERT OR IGNORE INTO quiz_sessions (team_id, started_at, ends_at, question_started_at) VALUES (?, ?, ?, ?)",
                    (team_id, now.isoformat(), (now + timedelta(minutes=QUIZ_MINUTES)).isoformat(), now.isoformat()))
        snapshot = _snapshot(cur, team_id, now)
        conn.commit()
        return snapshot, None
    finally:
        conn.close()


def answer_quiz(team_id, question_id, answer_index):
    if not isinstance(question_id, str) or type(answer_index) is not int or not 0 <= answer_index < 4:
        return None, "Select one answer before locking it."
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        now = _now()
        snapshot = _snapshot(cur, team_id, now)
        # Retry is harmless, including a lost response followed by a refresh.
        if cur.execute("SELECT 1 FROM quiz_answers WHERE team_id = ? AND question_id = ?", (team_id, question_id)).fetchone():
            conn.commit()
            return snapshot, None
        question = snapshot["current_question"]
        if snapshot["status"] != "OPEN" or question is None:
            conn.commit()
            return None, "The quiz is not accepting answers."
        if question["id"] != question_id:
            conn.commit()
            return None, "This question is no longer active. Continue with the current question."
        key = QUESTIONS[question["number"] - 1][3]
        cur.execute("INSERT INTO quiz_answers (team_id, question_id, answer_index, points) VALUES (?, ?, ?, ?)",
                    (team_id, question_id, answer_index, int(answer_index == key)))
        cur.execute("UPDATE quiz_sessions SET question_started_at = ? WHERE team_id = ?", (now.isoformat(), team_id))
        if question["number"] == len(QUESTIONS):
            cur.execute("UPDATE quiz_sessions SET completed_at = ? WHERE team_id = ?", (now.isoformat(), team_id))
        snapshot = _snapshot(cur, team_id, now)
        conn.commit()
        return snapshot, None
    finally:
        conn.close()
