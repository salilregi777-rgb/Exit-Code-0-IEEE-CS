"""Behavioral coverage for server authority, live progress and the separate quiz."""
from datetime import datetime, timedelta, timezone

import pytest
pytestmark = pytest.mark.usefixtures("ordered_bank")

from app import app
from database import get_db_connection, get_client_question, init_db, register_team
from event_manager import end_event, get_event_state, reset_event_data, start_event
from scoring import process_submission, activate_rubber_duck, activate_git_revert, arm_double_commit
from quiz import QUESTIONS, QUESTION_SECONDS


@pytest.fixture
def client():
    app.config["TESTING"] = True
    init_db(force_reset=True)
    with app.test_client() as client:
        yield client


def join(client, name="Integrity Team"):
    team_id, error = register_team(name, "Ada", "Grace")
    assert error is None
    with client.session_transaction() as session:
        session.clear()
        session["team_id"] = team_id
    # These cases exercise participating teams; lobby fullscreen entries no longer arm access.
    assert start_event()[0]
    assert client.post("/api/activity", json={"event_type": "fullscreen_enter", "event_id": "test-login-" + team_id}).status_code == 200
    return team_id


def admin(client):
    with client.session_transaction(path="/admin") as session:
        session["is_admin"] = True


def answer(**extra):
    return dict(question_id="Q01", error_location="3", error_type="Logical Error", expected_output="10",
                correction="for i in range(len(numbers)):", **extra)


def test_locked_question_cannot_be_read_scored_or_targeted(client):
    team_id = join(client)
    start_event()
    assert get_client_question(team_id, "Q02") is None
    assert client.get("/api/question/Q02").status_code == 404
    assert process_submission(team_id, "Q02", answer())[1] is not None
    for powerup in (activate_rubber_duck, activate_git_revert, arm_double_commit):
        assert powerup(team_id, "Q02")[0] is False
    conn = get_db_connection()
    assert conn.execute("SELECT SUM(is_used) FROM powerups").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0
    conn.close()


def test_duplicate_network_retry_preserves_double_commit_and_timestamp(client):
    team_id = join(client)
    start_event()
    assert arm_double_commit(team_id, "Q01")[0]
    payload = answer(request_id="stable-network-attempt-1")
    first = client.post("/api/submit-bug-fix", json=payload).get_json()
    second = client.post("/api/submit-bug-fix", json=payload).get_json()
    assert first["success"] and second == first
    assert first["result"]["is_double_commit"] == 1
    assert first["result"]["total_score"] == 35
    assert first["result"]["powerup_adjustments"]["double_commit"] == 0
    assert first["result"]["double_commit_bonus"] == 15
    assert first["new_score"] == 35
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1
    conn.execute("UPDATE scores SET last_submission_time = '2026-10-07 10:00:00'")
    conn.commit()
    worse = answer()
    worse.update(cause="", correction="", expected_output="")
    assert "already been answered" in process_submission(team_id, "Q01", worse)[1]
    row = conn.execute("SELECT score, last_submission_time FROM scores").fetchone()
    assert row["score"] == first["new_score"]
    assert row["last_submission_time"] == "2026-10-07 10:00:00"
    conn.close()


def test_expiry_and_pause_close_mutations(client):
    team_id = join(client)
    start_event()
    conn = get_db_connection()
    conn.execute("UPDATE event_state SET event_end_time = ?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    conn.commit()
    conn.close()
    assert client.post("/api/submit-bug-fix", json=answer()).status_code == 403
    assert process_submission(team_id, "Q01", answer(), enforce_live=True)[1]
    assert client.post("/api/powerup/rubber-duck", json={"question_id": "Q01"}).status_code == 403
    assert get_event_state()["event_status"] == "COMPLETED"


def test_disabled_team_session_and_cross_site_writes_rejected(client):
    team_id = join(client)
    start_event()
    assert client.post("/api/submit-bug-fix", json=answer(), headers={"Origin": "https://another.example"}).status_code == 403
    conn = get_db_connection()
    conn.execute("UPDATE teams SET is_active = 0 WHERE id = ?", (team_id,))
    conn.commit()
    conn.close()
    assert client.post("/api/submit-bug-fix", json=answer()).status_code == 403
    assert client.get("/api/team-progress").status_code == 401


def test_safe_progress_and_malformed_json(client):
    join(client)
    progress = client.get("/api/team-progress").get_json()
    assert progress["total_questions"] == 30
    assert progress["questions"][0]["is_unlocked"] == 1
    forbidden = {"bug_location", "expected_output", "cause", "correction", "hint", "code", "error_type", "language", "title"}
    assert not any(forbidden.intersection(question) for question in progress["questions"])
    for value in ([], "text", None):
        res = client.post("/api/submit-bug-fix", json=value)
        assert res.status_code == 400 and res.is_json
    assert client.get("/api/no-such-route").is_json


def test_quiz_answers_are_private_and_never_affect_debug_ranking(client):
    team_id = join(client)
    process_submission(team_id, "Q01", answer())
    before = client.get("/api/leaderboard-data").get_json()["leaderboard"]
    assert client.post("/api/quiz/start", json={}).status_code == 403
    end_event()
    admin(client)
    assert client.post("/api/admin/quiz-action", json={"action": "open"}).status_code == 200
    started = client.post("/api/quiz/start", json={}).get_json()["quiz"]
    assert started["current_question"]["id"] == "R01"
    assert set(started["current_question"]) == {"id", "prompt", "options", "number"}
    for qid, _, _, key in QUESTIONS:
        result = client.post("/api/quiz/answer", json={"question_id": qid, "answer_index": key})
        assert result.status_code == 200
    final = result.get_json()["quiz"]
    assert final["completed"] and final["quiz_score"] == 10
    assert final["current_question"] is None
    assert client.get("/api/leaderboard-data").get_json()["leaderboard"] == before
    duplicate = client.post("/api/quiz/answer", json={"question_id": "R01", "answer_index": 0}).get_json()["quiz"]
    assert duplicate["quiz_score"] == 10
    assert client.get("/api/leaderboard-data").get_json()["leaderboard_state"] == "FROZEN"
    assert client.post("/api/admin/publish-results", json={}).status_code == 200
    assert client.get("/api/leaderboard-data").get_json()["leaderboard_state"] == "FINAL"


def test_quiz_timeout_survives_refresh_and_answer_change(client):
    team_id = join(client)
    end_event()
    admin(client)
    client.post("/api/admin/quiz-action", json={"action": "open"})
    client.post("/api/quiz/start", json={})
    conn = get_db_connection()
    conn.execute("UPDATE quiz_sessions SET question_started_at = ? WHERE team_id = ?",
                 ((datetime.now(timezone.utc) - timedelta(seconds=QUESTION_SECONDS + 5)).isoformat(), team_id))
    conn.commit()
    status = client.get("/api/quiz/status").get_json()["quiz"]
    assert status["current_question"]["id"] == "R02"
    assert status["quiz_score"] == 0 and status["answered_count"] == 1
    # Repeated start never grants another timer or resets missed questions.
    restarted = client.post("/api/quiz/start", json={}).get_json()["quiz"]
    assert restarted["current_question"]["id"] == "R02"
    late = client.post("/api/quiz/answer", json={"question_id": "R01", "answer_index": 1}).get_json()["quiz"]
    assert late["quiz_score"] == 0
    conn.execute("UPDATE quiz_sessions SET ends_at = ?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    conn.commit()
    assert client.get("/api/quiz/status").get_json()["quiz"]["completed"]
    conn.close()


def test_fullscreen_signals_are_idempotent_and_reset_clears_rounds(client):
    join(client)
    generation = client.get("/api/team-progress").get_json()["generation"]
    start_event()
    first = client.post("/api/activity", json={"event_type": "fullscreen_exit", "event_id": "same-exit"}).get_json()
    second = client.post("/api/activity", json={"event_type": "fullscreen_exit", "event_id": "same-exit"}).get_json()
    assert first["recorded"] and not second["recorded"]
    admin(client)
    data = client.get("/api/admin/dashboard-data").get_json()
    assert data["stats"]["active_teams"] == 1
    assert data["security"][0]["fullscreen_exits"] == 1
    assert data["stats"]["average_score"] == 0
    assert not start_event()[0]  # A second start cannot silently reset the clock.
    assert not reset_event_data("RESET")[0]
    assert reset_event_data("RESET EVENT")[0]
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) FROM team_activity").fetchone()[0] == 0
    assert conn.execute("SELECT generation FROM competition_controls").fetchone()[0] != generation
    conn.close()


def test_quiz_seven_minute_deadline_and_all_ten_question_timeouts(client, monkeypatch):
    join(client)
    end_event()
    admin(client)
    client.post("/api/admin/quiz-action", json={"action": "open"})
    start = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("quiz._now", lambda: start)
    quiz = client.post("/api/quiz/start", json={}).get_json()["quiz"]
    assert quiz["duration_minutes"] == 7 and quiz["quiz_total"] == 10
    assert quiz["remaining_seconds"] == 420 and quiz["question_remaining_seconds"] == 42

    monkeypatch.setattr("quiz._now", lambda: start + timedelta(seconds=41))
    quiz = client.post("/api/quiz/start", json={}).get_json()["quiz"]
    assert quiz["current_question"]["number"] == 1 and quiz["question_remaining_seconds"] == 1
    assert quiz["remaining_seconds"] == 379  # Restarting cannot renew either deadline.

    monkeypatch.setattr("quiz._now", lambda: start + timedelta(seconds=42))
    quiz = client.get("/api/quiz/status").get_json()["quiz"]
    assert quiz["current_question"]["number"] == 2 and quiz["question_remaining_seconds"] == 42
    assert quiz["answers"][0]["status"] == "unanswered"

    monkeypatch.setattr("quiz._now", lambda: start + timedelta(seconds=419))
    quiz = client.get("/api/quiz/status").get_json()["quiz"]
    assert quiz["current_question"]["number"] == 10 and quiz["question_remaining_seconds"] == 1
    monkeypatch.setattr("quiz._now", lambda: start + timedelta(seconds=420))
    quiz = client.post("/api/quiz/answer", json={"question_id": "R10", "answer_index": 2}).get_json()["quiz"]
    assert quiz["completed"] and quiz["answered_count"] == 10 and quiz["quiz_score"] == 0
    assert quiz["current_question"] is None and quiz["remaining_seconds"] == 0


def test_quiz_early_answer_keeps_seven_minute_session_deadline(client, monkeypatch):
    join(client)
    end_event()
    admin(client)
    client.post("/api/admin/quiz-action", json={"action": "open"})
    start = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("quiz._now", lambda: start)
    client.post("/api/quiz/start", json={})
    monkeypatch.setattr("quiz._now", lambda: start + timedelta(seconds=20))
    quiz = client.post("/api/quiz/answer", json={"question_id": "R01", "answer_index": 1}).get_json()["quiz"]
    assert quiz["current_question"]["number"] == 2 and quiz["question_remaining_seconds"] == 42
    assert quiz["remaining_seconds"] == 400 and quiz["quiz_score"] == 1


def test_override_validation_keeps_scores_finite_and_bounded(client):
    team_id = join(client)
    process_submission(team_id, "Q01", answer())
    admin(client)
    conn = get_db_connection()
    sub_id = conn.execute("SELECT id FROM submissions").fetchone()[0]
    conn.close()
    for value in ("NaN", "inf", -1, 99999, []):
        result = client.post("/api/admin/override-score", json={"submission_id": sub_id, "new_score": value, "reason": "review"})
        assert result.status_code == 400


def test_init_db_is_additive_and_preserves_existing_data(client):
    team_id = join(client)
    process_submission(team_id, "Q01", answer())
    init_db()
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) FROM teams WHERE id = ?", (team_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1
    conn.close()


def test_multiline_correction_does_not_consume_the_single_attempt(client):
    team_id = join(client)
    payload = answer()
    payload["correction"] = "for i in range(len(numbers)):\n    total += numbers[i]"
    rejected = client.post("/api/submit-bug-fix", json=payload)
    assert rejected.status_code == 400
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) FROM submissions WHERE team_id = ?", (team_id,)).fetchone()[0] == 0
    conn.close()
    saved = client.post("/api/submit-bug-fix", json=answer(cause="Retired field is ignored")).get_json()
    assert saved["success"] and saved["result"]["total_score"] == 20
    assert saved["result"]["cause_score"] == 0
    assert len(saved["result"]["field_results"]) == 4
    reviewed = client.get("/api/question/Q01").get_json()["question"]
    assert "cause" not in reviewed["submission"]
