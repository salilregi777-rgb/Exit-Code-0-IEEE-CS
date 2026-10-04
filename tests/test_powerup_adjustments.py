"""Power-ups change team totals once, independently of answer credit."""
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app import app
from database import get_db_connection, init_db, register_team
from scoring import activate_git_revert, activate_rubber_duck, arm_double_commit, evaluate_submission, process_submission

pytestmark = pytest.mark.usefixtures("ordered_bank")


@pytest.fixture(autouse=True)
def clean_database():
    init_db(force_reset=True)
    app.config["TESTING"] = True


def team():
    team_id, error = register_team("Fixed power-up points", "Ada", "Grace")
    assert error is None
    return team_id


def question(question_id="Q01"):
    conn = get_db_connection()
    value = dict(conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone())
    conn.close()
    return value


def answer(item):
    return {"error_location": item["bug_location"], "error_type": item["error_type"],
            "expected_output": item["expected_output"], "correction": item["correction"]}


def score(team_id):
    conn = get_db_connection()
    value = dict(conn.execute("SELECT * FROM scores WHERE team_id = ?", (team_id,)).fetchone())
    conn.close()
    return value


@pytest.mark.parametrize("activate,powerup,adjustment", [
    (activate_rubber_duck, "RUBBER_DUCK", -5),
    (activate_git_revert, "GIT_REVERT", -7),
    (arm_double_commit, "DOUBLE_COMMIT", 15),
])
def test_activation_changes_total_immediately_and_only_once(activate, powerup, adjustment):
    team_id = team()
    assert activate(team_id, "Q01")[0]
    assert score(team_id)["score"] == adjustment
    assert score(team_id)["completed_count"] == 0
    assert score(team_id)["last_submission_time"] is None
    assert not activate(team_id, "Q01")[0]
    init_db()
    assert score(team_id)["score"] == adjustment
    conn = get_db_connection()
    saved = conn.execute("SELECT * FROM powerups WHERE team_id = ? AND powerup_type = ?", (team_id, powerup)).fetchone()
    assert saved["is_used"] == 1 and saved["is_armed"] == 0
    assert saved["score_adjustment"] == adjustment
    assert saved["used_at"]
    assert conn.execute("SELECT COUNT(*) FROM submissions WHERE team_id = ?", (team_id,)).fetchone()[0] == 0
    conn.close()


def test_all_tools_and_answer_retry_preserve_the_same_aggregate():
    team_id = team()
    assert activate_rubber_duck(team_id, "Q01")[0]
    assert score(team_id)["score"] == -5
    success, _, replacement = activate_git_revert(team_id, "Q01")
    assert success and score(team_id)["score"] == -12
    assert arm_double_commit(team_id, replacement)[0]
    assert score(team_id)["score"] == 3
    item = question(replacement)
    payload = dict(answer(item), request_id="fixed-adjustments-first-answer")
    saved, error = process_submission(team_id, replacement, payload)
    assert error is None
    assert saved["total_score"] == saved["raw_total"] == saved["max_score"] == item["points"]
    assert saved["powerup_adjustments"] == {"hint": -5, "swap": -7, "double_commit": 15}
    assert score(team_id)["score"] == item["points"] + 3
    original_time = score(team_id)["last_submission_time"]
    retried, error = process_submission(team_id, replacement, payload)
    assert error is None and retried == saved
    init_db()
    assert score(team_id)["score"] == item["points"] + 3
    assert score(team_id)["completed_count"] == 1
    assert score(team_id)["last_submission_time"] == original_time
    next_result, error = process_submission(team_id, "Q02", answer(question("Q02")))
    assert error is None
    assert next_result["powerup_adjustments"] == {"hint": 0, "swap": 0, "double_commit": 0}
    assert score(team_id)["score"] == item["points"] + question("Q02")["points"] + 3


@pytest.mark.parametrize("activate,adjustment", [
    (activate_rubber_duck, -5), (activate_git_revert, -7), (arm_double_commit, 15),
])
def test_concurrent_activation_has_one_effect(activate, adjustment):
    team_id = team()
    barrier = Barrier(5)

    def activate_once(_):
        barrier.wait(timeout=5)
        return activate(team_id, "Q01")

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(activate_once, range(5)))
    assert sum(bool(response[0]) for response in responses) == 1
    assert score(team_id)["score"] == adjustment
    conn = get_db_connection()
    assert conn.execute("SELECT SUM(score_adjustment) FROM powerups WHERE team_id = ?", (team_id,)).fetchone()[0] == adjustment
    conn.close()


def test_swap_without_an_alternative_does_not_consume_or_charge():
    team_id = team()
    conn = get_db_connection()
    conn.execute("UPDATE question_assignments SET is_unlocked = 1 WHERE team_id = ?", (team_id,))
    conn.commit()
    conn.close()
    success, error, replacement = activate_git_revert(team_id, "Q01")
    assert not success and "No alternative" in error and replacement is None
    assert score(team_id)["score"] == 0
    conn = get_db_connection()
    saved = conn.execute("SELECT * FROM powerups WHERE team_id = ? AND powerup_type = 'GIT_REVERT'", (team_id,)).fetchone()
    assert saved["is_used"] == saved["score_adjustment"] == 0
    assert conn.execute("SELECT is_abandoned FROM question_assignments WHERE team_id = ? AND question_id = 'Q01'", (team_id,)).fetchone()[0] == 0
    conn.close()


def test_activation_does_not_change_last_submission_tiebreak():
    team_id = team()
    result, error = process_submission(team_id, "Q01", answer(question()))
    assert error is None
    conn = get_db_connection()
    conn.execute("UPDATE scores SET last_submission_time = '2026-10-01 09:00:00' WHERE team_id = ?", (team_id,))
    conn.commit()
    conn.close()
    assert activate_rubber_duck(team_id, "Q02")[0]
    updated = score(team_id)
    assert updated["score"] == result["total_score"] - 5
    assert updated["completed_count"] == 1
    assert updated["last_submission_time"] == "2026-10-01 09:00:00"


def test_incorrect_answer_keeps_the_fixed_bonus_and_does_not_double():
    team_id = team()
    assert arm_double_commit(team_id, "Q01")[0]
    result, error = process_submission(team_id, "Q01", {})
    assert error is None
    assert result["total_score"] == 0 and result["answer_status"] == "incorrect"
    assert result["powerup_adjustments"]["double_commit"] == 15
    assert score(team_id)["score"] == 15


def test_schema_upgrade_does_not_backcharge_legacy_uses_or_regrade_answers():
    team_id = team()
    legacy = evaluate_submission(question(), answer(question()), hint_used=True)
    submitted, error = process_submission(team_id, "Q01", answer(question()))
    assert error is None
    conn = get_db_connection()
    conn.execute("UPDATE submissions SET total_score = ?, response_json = ? WHERE team_id = ?", (legacy["total_score"], json.dumps(legacy), team_id))
    conn.execute("UPDATE scores SET score = ? WHERE team_id = ?", (legacy["total_score"], team_id))
    conn.execute("UPDATE powerups SET is_used = 1, target_question_id = 'Q01' WHERE team_id = ? AND powerup_type = 'RUBBER_DUCK'", (team_id,))
    conn.execute("ALTER TABLE powerups DROP COLUMN score_adjustment")
    conn.commit()
    before = dict(conn.execute("SELECT * FROM submissions WHERE team_id = ?", (team_id,)).fetchone())
    conn.close()
    init_db()
    assert score(team_id)["score"] == 18
    conn = get_db_connection()
    assert conn.execute("SELECT SUM(score_adjustment) FROM powerups WHERE team_id = ?", (team_id,)).fetchone()[0] == 0
    after = dict(conn.execute("SELECT * FROM submissions WHERE team_id = ?", (team_id,)).fetchone())
    assert after == before
    conn.close()
    assert not activate_rubber_duck(team_id, "Q02")[0]
    second, error = process_submission(team_id, "Q02", answer(question("Q02")))
    assert error is None
    assert score(team_id)["score"] == 18 + second["total_score"]


def test_legacy_pending_hint_keeps_its_original_cost():
    team_id = team()
    conn = get_db_connection()
    conn.execute("UPDATE powerups SET is_used = 1, target_question_id = 'Q01', score_adjustment = 0 WHERE team_id = ? AND powerup_type = 'RUBBER_DUCK'", (team_id,))
    conn.commit()
    conn.close()
    result, error = process_submission(team_id, "Q01", answer(question()))
    assert error is None and result["total_score"] == 18
    assert result["penalties"]["hint"] == 2
    assert score(team_id)["score"] == 18


def test_organizer_override_and_export_keep_team_adjustments():
    team_id = team()
    assert activate_rubber_duck(team_id, "Q01")[0]
    assert arm_double_commit(team_id, "Q01")[0]
    result, error = process_submission(team_id, "Q01", answer(question()))
    assert error is None and score(team_id)["score"] == 30
    conn = get_db_connection()
    submission_id = conn.execute("SELECT id FROM submissions WHERE team_id = ?", (team_id,)).fetchone()[0]
    conn.close()
    with app.test_client() as client:
        with client.session_transaction(path="/admin") as session:
            session["is_admin"] = True
        response = client.post("/api/admin/override-score", json={"submission_id": submission_id, "new_score": 12, "reason": "Reviewed the correction"})
        assert response.status_code == 200, response.get_json()
        assert score(team_id)["score"] == 22
        exported = client.get("/admin/export/results.csv")
        assert exported.status_code == 200
        rows = list(csv.DictReader(io.StringIO(exported.get_data(as_text=True))))
        assert rows[0]["Final Score"] == "22.0"
        # The bonus belongs to the team and must not increase an answer override's limit.
        invalid = client.post("/api/admin/override-score", json={"submission_id": submission_id, "new_score": 21, "reason": "Too many answer points"})
        assert invalid.status_code == 400
        assert score(team_id)["score"] == 22
    second, error = process_submission(team_id, "Q02", answer(question("Q02")))
    assert error is None
    assert score(team_id)["score"] == 22 + second["total_score"]
