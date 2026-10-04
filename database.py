import sqlite3
import json
import os
import uuid
import random
from datetime import datetime, timezone
from pathlib import Path
from config import Config
from answer_feedback import answer_field_results

QUESTION_BANK_VERSION = "c-python-explicit-tasks-team-shuffle-v3"
PUBLIC_QUESTION_FIELDS = ("language", "title", "task", "difficulty", "points", "code")


def balanced_question_order(questions):
    """Create a fresh team order, keeping a difficult question inside each six."""
    rng = random.SystemRandom()
    regular = [q for q in questions if q["difficulty"] != "Difficult"]
    difficult = [q for q in questions if q["difficulty"] == "Difficult"]
    rng.shuffle(regular)
    rng.shuffle(difficult)
    ordered = []
    for start in range(0, len(regular), 5):
        block = regular[start:start + 5]
        if difficult:
            block.insert(min(len(block), rng.choice((2, 3))), difficult.pop())
        ordered.extend(block)
    # Organisers can deactivate questions; retain every remaining active item.
    ordered.extend(difficult)
    return ordered

def get_db_connection():
    db_path = Config.DATABASE_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
    except Exception:
        pass
    return conn

def init_db(force_reset=False):
    db_path = Config.DATABASE_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = get_db_connection()
    with open(Config.SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    
    if force_reset:
        cur_drop = conn.cursor()
        cur_drop.execute("PRAGMA foreign_keys = OFF;")
        cur_drop.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur_drop.fetchall() if not r[0].startswith("sqlite_")]
        for t in tables:
            cur_drop.execute(f"DROP TABLE IF EXISTS {t}")
        cur_drop.execute("PRAGMA foreign_keys = ON;")
        conn.commit()

    conn.executescript(schema_sql)

    powerup_columns = {r["name"] for r in conn.execute("PRAGMA table_info(powerups)")}
    if "score_adjustment" not in powerup_columns:
        # Existing uses retain their historical grading; never charge them again.
        conn.execute("ALTER TABLE powerups ADD COLUMN score_adjustment REAL NOT NULL DEFAULT 0")
    
    # Additive migrations preserve existing competition data.
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(submissions)")}
    for name in ("request_id", "response_json"):
        if name not in columns:
            conn.execute(f"ALTER TABLE submissions ADD COLUMN {name} TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_submission_request ON submissions(team_id, request_id) WHERE request_id IS NOT NULL")
    session_columns = {r["name"] for r in conn.execute("PRAGMA table_info(fullscreen_sessions)")}
    for name in ("active_event_id", "document_id"):
        if name not in session_columns:
            conn.execute(f"ALTER TABLE fullscreen_sessions ADD COLUMN {name} TEXT")
    if "document_started_at" not in session_columns:
        conn.execute("ALTER TABLE fullscreen_sessions ADD COLUMN document_started_at REAL NOT NULL DEFAULT 0")
    conn.execute("INSERT OR IGNORE INTO competition_controls (id, generation) VALUES (1, ?)", (uuid.uuid4().hex,))

    # Preserve activity collected by the original phase 1 implementation.
    conn.execute("""INSERT INTO team_activity (team_id, event_type, created_at)
        SELECT old.team_id,
               CASE old.event_type WHEN 'focus_lost' THEN 'window_blur'
                 WHEN 'focus_regained' THEN 'window_focus'
                 WHEN 'tab_visible' THEN 'window_visible' ELSE old.event_type END,
               old.created_at
        FROM activity_events old
        WHERE NOT EXISTS (SELECT 1 FROM team_activity current
          WHERE current.team_id = old.team_id AND current.created_at = old.created_at
          AND current.event_type = CASE old.event_type WHEN 'focus_lost' THEN 'window_blur'
            WHEN 'focus_regained' THEN 'window_focus'
            WHEN 'tab_visible' THEN 'window_visible' ELSE old.event_type END)""")

    # Initialize event_state row if missing
    cur = conn.cursor()
    cfg = Config.load_event_config()
    dur = cfg.get("duration_minutes", 40)
    cur.execute("SELECT id FROM event_state WHERE id = 1")
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO event_state (id, event_status, duration_minutes, remaining_seconds, is_paused)
            VALUES (1, 'WAITING', ?, ?, 0)
        """, (dur, dur * 60))
    # Apply configuration changes before a round starts, preserving existing deadlines and progress.
    cur.execute("""
        UPDATE event_state SET duration_minutes = ?, remaining_seconds = ?
        WHERE id = 1 AND event_status = 'WAITING' AND event_start_time IS NULL
    """, (dur, dur * 60))
    
    # Versioned, non-destructive bank migration: keep assignments, scores and active flags.
    question_columns = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    for name in ("cause_keywords", "correction_keywords"):
        if name not in question_columns:
            conn.execute(f"ALTER TABLE questions ADD COLUMN {name} TEXT")
    if "task" not in question_columns:
        conn.execute("ALTER TABLE questions ADD COLUMN task TEXT NOT NULL DEFAULT ''")
    bank_version = QUESTION_BANK_VERSION
    if not conn.execute("SELECT 1 FROM data_migrations WHERE name = ?", (bank_version,)).fetchone():
        # Preserve the source that existing submissions actually answered, as well
        # as historical maxima. Never put reference answers into this snapshot.
        previous_questions = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM questions")}
        for old in conn.execute("""SELECT s.*, q.points AS previous_points FROM submissions s
                JOIN questions q ON q.id = s.question_id""").fetchall():
            try:
                saved = json.loads(old["response_json"] or "{}")
            except (TypeError, ValueError):
                saved = {}
            if not isinstance(saved, dict):
                saved = {}
            saved.setdefault("question_snapshot", {key: previous_questions[old["question_id"]][key]
                for key in PUBLIC_QUESTION_FIELDS})
            saved.setdefault("base_points", old["previous_points"])
            penalties = saved.get("penalties") or {}
            if not isinstance(penalties, dict):
                penalties = {}
            penalty_total = penalties.get("total", penalties.get("hint", 0) + penalties.get("swap", 0))
            saved.setdefault("max_score", max(0, saved["base_points"] * (2 if old["is_double_commit"] else 1)
                             - penalty_total))
            raw = sum(old[key] for key in ("error_loc_score", "error_type_score", "cause_score", "output_score", "correction_score"))
            saved.setdefault("answer_status", "correct" if raw >= saved["base_points"] else "partial" if raw > 0 else "incorrect")
            conn.execute("UPDATE submissions SET response_json = ? WHERE id = ?", (json.dumps(saved), old["id"]))
        with open(Config.QUESTIONS_JSON_PATH, encoding="utf-8") as f:
            questions = json.load(f)
        for q in questions:
            cur.execute("""INSERT INTO questions
                (id, language, title, difficulty, code, error_type, bug_location, expected_output,
                 cause, correction, points, hint, cause_keywords, correction_keywords, task)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET language=excluded.language, title=excluded.title,
                difficulty=excluded.difficulty, code=excluded.code, error_type=excluded.error_type,
                bug_location=excluded.bug_location, expected_output=excluded.expected_output,
                cause=excluded.cause, correction=excluded.correction, points=excluded.points,
                hint=excluded.hint, cause_keywords=excluded.cause_keywords,
                correction_keywords=excluded.correction_keywords, task=excluded.task""",
                (q["id"], q["language"], q["title"], q.get("difficulty", "Medium"), q["code"],
                 q["error_type"], q["bug_location"], q["expected_output"], q["cause"], q["correction"],
                 q.get("points", q.get("base_points", 20)), q.get("hint", ""),
                 json.dumps(q.get("cause_keywords", [])), json.dumps(q.get("correction_keywords", [])), q.get("task", "")))
        waiting = conn.execute("""SELECT 1 FROM event_state WHERE id = 1
            AND event_status = 'WAITING' AND event_start_time IS NULL""").fetchone()
        if waiting:
            untouched = conn.execute("""SELECT id FROM teams t
                WHERE NOT EXISTS (SELECT 1 FROM submissions s WHERE s.team_id = t.id)
                AND NOT EXISTS (SELECT 1 FROM question_assignments qa WHERE qa.team_id = t.id
                  AND (qa.is_completed = 1 OR qa.is_abandoned = 1))
                AND NOT EXISTS (SELECT 1 FROM powerups p WHERE p.team_id = t.id AND p.is_used = 1)""").fetchall()
            for team in untouched:
                conn.execute("DELETE FROM question_assignments WHERE team_id = ?", (team["id"],))
                assign_initial_questions(team["id"], cur)
        conn.execute("INSERT INTO data_migrations(name) VALUES (?)", (bank_version,))

    # Withdraw only premature bonuses for unanswered questions. Completed
    # submissions keep their recorded scores; pending uses adopt the new wager.
    pending_bonuses = conn.execute("""SELECT p.id, p.team_id, p.target_question_id
        FROM powerups p WHERE p.powerup_type = 'DOUBLE_COMMIT'
          AND p.score_adjustment != 0
          AND NOT EXISTS (SELECT 1 FROM submissions s
            WHERE s.team_id = p.team_id AND s.question_id = p.target_question_id)""").fetchall()
    for powerup in pending_bonuses:
        replacement = conn.execute("""SELECT current.question_id FROM question_assignments old
            JOIN question_assignments current ON current.team_id = old.team_id
              AND current.question_order = old.question_order AND current.is_abandoned = 0
            WHERE old.team_id = ? AND old.question_id = ? AND old.is_abandoned = 1""",
            (powerup["team_id"], powerup["target_question_id"])).fetchone()
        target = replacement["question_id"] if replacement else powerup["target_question_id"]
        if conn.execute("SELECT 1 FROM submissions WHERE team_id = ? AND question_id = ?",
                        (powerup["team_id"], target)).fetchone():
            continue
        conn.execute("""UPDATE powerups SET score_adjustment = 0, is_used = 0,
            is_armed = 1, used_at = NULL, target_question_id = ? WHERE id = ?""", (target, powerup["id"]))
        recalculate_team_score(conn.cursor(), powerup["team_id"])

    conn.commit()
    conn.close()

def recalculate_team_score(cur, team_id, submission=False):
    """One total for the arena, judging, exports and leaderboard, including tools."""
    stats = cur.execute("""SELECT COALESCE(SUM(points), 0) AS points, COUNT(*) AS completed
        FROM (SELECT MAX(total_score) AS points FROM submissions
          WHERE team_id = ? AND is_accepted = 1 GROUP BY question_id)""", (team_id,)).fetchone()
    adjustments = cur.execute("SELECT COALESCE(SUM(score_adjustment), 0) FROM powerups WHERE team_id = ?", (team_id,)).fetchone()[0]
    total = round(stats["points"] + adjustments, 2)
    cur.execute("""UPDATE scores SET
        last_submission_time = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_submission_time END,
        score = ?, completed_count = ?, last_updated = CURRENT_TIMESTAMP WHERE team_id = ?""",
        (int(submission), total, stats["completed"], team_id))
    return total


def get_team_score(team_id):
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT score FROM scores WHERE team_id = ?", (team_id,)).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def generate_team_id(conn):
    cur = conn.cursor()
    cur.execute("SELECT id FROM teams ORDER BY id DESC")
    rows = cur.fetchall()
    max_num = 0
    for r in rows:
        tid = r["id"]
        if tid.startswith("EX0-"):
            try:
                num = int(tid.split("-")[1])
                if num > max_num:
                    max_num = num
            except ValueError:
                pass
    next_num = max_num + 1
    return f"EX0-{next_num:03d}"

def register_team(team_name, member1, member2, member3=None, email=None, password=None):
    """
    Registers a new team of 2–3 members.
    Enforces non-empty team name and 2-3 members.
    Prevents duplicate team names.
    """
    name = (team_name or "").strip()
    m1 = (member1 or "").strip()
    m2 = (member2 or "").strip()
    m3 = (member3 or "").strip() if member3 else ""
    em = (email or "").strip() if email else ""

    if not name:
        return None, "Team name is required."
    if any(len(value) > 80 for value in (name, m1, m2, m3)) or len(em) > 254:
        return None, "Team and member names must be 80 characters or fewer."
    if not m1 or not m2:
        return None, "A team must have at least 2 members (Member 1 and Member 2 are required)."

    # Count valid members
    members = [m1, m2]
    if m3:
        members.append(m3)

    if len(members) < 2 or len(members) > 3:
        return None, "Team size must be strictly between 2 and 3 members."

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if cur.execute("SELECT results_published FROM competition_controls WHERE id = 1").fetchone()[0]:
            return None, "Registration is closed because final results have been published."
        # Share the write lock used by start_event: a form opened before the
        # start cannot create a team after the organiser has started the round.
        state = cur.execute("SELECT event_status FROM event_state WHERE id = 1").fetchone()
        if not state or state["event_status"] != "WAITING":
            return None, "Registration is closed because the event has started. Registered teams can still sign in."
        # Check duplicate name
        cur.execute("SELECT id FROM teams WHERE UPPER(name) = UPPER(?)", (name,))
        if cur.fetchone():
            return None, f"Team name '{name}' is already registered. Please choose a unique name."

        team_id = generate_team_id(conn)
        cur.execute("INSERT INTO teams (id, name) VALUES (?, ?)", (team_id, name))

        # Insert participants
        cur.execute("INSERT INTO participants (team_id, name, role, email) VALUES (?, ?, 'Lead', ?)", (team_id, m1, em))
        cur.execute("INSERT INTO participants (team_id, name, role, email) VALUES (?, ?, 'Member 2', ?)", (team_id, m2, em))
        if m3:
            cur.execute("INSERT INTO participants (team_id, name, role, email) VALUES (?, ?, 'Member 3', ?)", (team_id, m3, em))

        # Initialize score row
        cur.execute("""
            INSERT INTO scores (team_id, score, bonus_score, completed_count)
            VALUES (?, 0, 0, 0)
        """, (team_id,))

        # Initialize power-ups (1 each per team)
        for pu in ["RUBBER_DUCK", "GIT_REVERT", "DOUBLE_COMMIT"]:
            cur.execute("INSERT INTO powerups (team_id, powerup_type, is_used) VALUES (?, ?, 0)", (team_id, pu))

        # Assign initial progressive questions
        assign_initial_questions(team_id, cur)

        conn.commit()
        return team_id, None
    except sqlite3.IntegrityError as e:
        conn.rollback()
        return None, "Registration could not be saved. Please try a different team name."
    finally:
        conn.close()

def assign_initial_questions(team_id, cur=None):
    """
    Persist a fresh difficulty-balanced shuffle for this team exactly once.
    The first assigned question unlocks immediately; submissions unlock the next.
    Existing assignments are retained across refreshes, restarts and retries.
    """
    close_conn = False
    if cur is None:
        conn = get_db_connection()
        cur = conn.cursor()
        close_conn = True

    try:
        cur.execute("SELECT COUNT(*) as c FROM question_assignments WHERE team_id = ?", (team_id,))
        if cur.fetchone()["c"] > 0:
            return

        cur.execute("SELECT id, difficulty FROM questions WHERE is_active = 1 ORDER BY id ASC")
        questions = balanced_question_order(cur.fetchall())

        for order, q in enumerate(questions, 1):
            is_unlocked = 1 if order == 1 else 0
            cur.execute("""
                INSERT OR IGNORE INTO question_assignments 
                (team_id, question_id, question_order, is_unlocked, is_completed, is_abandoned)
                VALUES (?, ?, ?, ?, 0, 0)
            """, (team_id, q["id"], order, is_unlocked))

        if close_conn:
            conn.commit()
    finally:
        if close_conn:
            conn.close()

def _answer_summary(conn, team_id, question_id, points, include_submission=False):
    row = conn.execute("SELECT * FROM submissions WHERE team_id = ? AND question_id = ? ORDER BY is_accepted DESC, total_score DESC, id ASC LIMIT 1", (team_id, question_id)).fetchone()
    result = {"is_answered": bool(row), "answer_status": None, "awarded_score": 0, "max_score": points}
    if row:
        evaluation = json.loads(row["response_json"] or "{}")
        raw = sum(row[key] for key in ("error_loc_score", "error_type_score", "cause_score", "output_score", "correction_score"))
        result.update(awarded_score=row["total_score"], max_score=evaluation.get("max_score", points * (2 if row["is_double_commit"] else 1)),
                      answer_status=evaluation.get("answer_status") or ("correct" if raw >= points else "partial" if raw > 0 else "incorrect"))
        if row["override_reason"]:
            result["answer_status"] = "correct" if row["total_score"] >= result["max_score"] else "partial" if row["total_score"] > 0 else "incorrect"
        # Saved component scores also support older submissions without a stored
        # field breakdown. Do not regrade or include reference/keyword content.
        result["field_results"] = answer_field_results(dict(row), evaluation.get("base_points", points))
        result["score_overridden"] = bool(row["override_reason"])
        result["penalties"] = evaluation.get("penalties", {})
        result["powerup_adjustments"] = evaluation.get("powerup_adjustments", {})
        for key in ("double_commit_rule", "double_commit_bonus"):
            if key in evaluation:
                result[key] = evaluation[key]
        for key in ("hint_used", "swap_used", "is_double_commit"):
            result[key] = evaluation.get(key, 0)
        if include_submission:
            result["submission"] = {key: row[key] for key in ("error_location", "error_type", "expected_output", "correction")}
            snapshot = evaluation.get("question_snapshot")
            if isinstance(snapshot, dict):
                result.update({key: snapshot[key] for key in PUBLIC_QUESTION_FIELDS if key in snapshot})
    return result


def get_team_assigned_questions(team_id):
    """Returns progression without revealing upcoming question languages or titles."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.id, q.difficulty, q.points,
               qa.question_order, qa.is_unlocked, qa.is_completed, qa.is_abandoned
        FROM question_assignments qa
        JOIN questions q ON qa.question_id = q.id
        WHERE qa.team_id = ? AND qa.is_abandoned = 0
        ORDER BY qa.question_order ASC
    """, (team_id,))
    rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        row.update(_answer_summary(conn, team_id, row["id"], row["points"]))
    conn.close()
    return rows

def get_client_question(team_id, question_id):
    """
    Returns sanitized question data for the client.
    Never exposes bug location, error type, expected output, cause, or correction!
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.id, q.language, q.title, q.task, q.difficulty, q.points, q.code,
               qa.question_order, qa.is_unlocked, qa.is_completed
        FROM question_assignments qa
        JOIN questions q ON qa.question_id = q.id
        WHERE qa.team_id = ? AND qa.question_id = ? AND qa.is_abandoned = 0 AND qa.is_unlocked = 1
    """, (team_id, question_id))
    q = cur.fetchone()
    result = dict(q) if q else None
    if result:
        result.update(_answer_summary(conn, team_id, question_id, result["points"], include_submission=True))
    conn.close()
    return result

def unlock_next_question(team_id, current_order, cur=None):
    """Unlocks the next sequential question for the team."""
    close_conn = False
    if cur is None:
        conn = get_db_connection()
        cur = conn.cursor()
        close_conn = True

    try:
        cur.execute("""
            UPDATE question_assignments 
            SET is_unlocked = 1 
            WHERE team_id = ? AND question_order = ? AND is_abandoned = 0
        """, (team_id, current_order + 1))
        if close_conn:
            conn.commit()
    finally:
        if close_conn:
            conn.close()

def log_admin_action(action, details=""):
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO admin_actions (action, details) VALUES (?, ?)", (action, details))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def record_activity(team_id, event_type, question_id=None, cur=None):
    """Only store organizer signals; browser signals never change scores."""
    owned = cur is None
    conn = get_db_connection() if owned else None
    cur = conn.cursor() if owned else cur
    try:
        cur.execute("INSERT INTO team_activity (team_id, event_type, question_id) VALUES (?, ?, ?)",
                    (team_id, event_type, question_id))
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def get_competition_controls():
    conn = get_db_connection()
    try:
        return dict(conn.execute("SELECT * FROM competition_controls WHERE id = 1").fetchone())
    finally:
        conn.close()
