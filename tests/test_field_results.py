"""Individual feedback stays tied to locked submissions, not answer references."""
import json

import pytest
pytestmark = pytest.mark.usefixtures("ordered_bank")

from app import app
from database import init_db, register_team, get_db_connection, get_client_question, get_team_assigned_questions
from event_manager import start_event
from scoring import evaluate_submission, process_submission, activate_rubber_duck


@pytest.fixture(autouse=True)
def clean_database():
    init_db(force_reset=True)


def question():
    return {"points": 20, "error_type": "Logical Error", "bug_location": "Line 3",
            "expected_output": "10", "language": "Python",
            "code": "def calculate_sum(numbers):\n    total = 0\n    for i in range(len(numbers) - 1):\n        total += numbers[i]\n    return total\nprint(calculate_sum([1, 2, 3, 4]))",
            "correction": "    for i in range(len(numbers)):"}


def reference_answer(item):
    return {"error_location": item["bug_location"], "error_type": item["error_type"],
            "expected_output": item["expected_output"],
            "correction": item["correction"]}


def saved_question(qid="Q01"):
    conn = get_db_connection()
    item = dict(conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone())
    conn.close()
    return item


def fields_by_key(result):
    return {field["key"]: field for field in result["field_results"]}


@pytest.mark.parametrize("double,hint,swap", [(False, False, False), (False, True, True), (True, True, True)])
def test_full_credit_reports_four_fields_before_modifiers(double, hint, swap):
    item = question()
    result = evaluate_submission(item, reference_answer(item), double, hint, swap)
    rows = result["field_results"]
    assert [row["key"] for row in rows] == ["error_location", "error_type", "expected_output", "correction"]
    assert [row["max_score"] for row in rows] == [2, 3, 4, 11]
    assert all(row["status"] == "correct" and row["score"] == row["max_score"] for row in rows)
    assert all(set(row) == {"key", "label", "status", "score", "max_score"} for row in rows)
    assert sum(row["score"] for row in rows) == result["raw_total"] == 20
    assert result["total_score"] == (40 if double else 20) - (2 if hint else 0) - (2 if swap else 0)


def test_mixed_fields_identify_wrong_output_and_invalid_code_individually():
    response = reference_answer(question())
    response.update(expected_output="wrong", cause="loop last unrelated thought quartz zebra", correction="range range range")
    result = fields_by_key(evaluate_submission(question(), response))
    assert result["error_location"]["status"] == result["error_type"]["status"] == "correct"
    assert result["expected_output"]["status"] == result["correction"]["status"] == "incorrect"
    assert "cause" not in result and len(result) == 4
    assert result["correction"]["max_score"] == 11


def test_zero_final_score_does_not_hide_correct_component():
    evaluation = evaluate_submission(question(), {"error_type": "Logical Error"}, hint_used=True, swap_used=True)
    assert evaluation["total_score"] == 0
    assert fields_by_key(evaluation)["error_type"]["status"] == "correct"
    assert sum(row["status"] == "incorrect" for row in evaluation["field_results"]) == 3


def test_submission_feedback_survives_retry_reload_and_question_navigation():
    team_id, error = register_team("Feedback team", "One", "Two")
    assert error is None
    assert activate_rubber_duck(team_id, "Q01")[0]
    response = reference_answer(saved_question())
    response.update(request_id="field-results-retry", expected_output="my wrong output")
    result, error = process_submission(team_id, "Q01", response)
    assert error is None
    retried, error = process_submission(team_id, "Q01", response)
    assert error is None and retried["field_results"] == result["field_results"]
    reviewed = get_client_question(team_id, "Q01")
    assert reviewed["submission"]["expected_output"] == "my wrong output"
    assert reviewed["field_results"] == result["field_results"]
    assert reviewed["awarded_score"] == result["total_score"]
    assigned = get_team_assigned_questions(team_id)
    assert assigned[0]["field_results"] == result["field_results"]
    assert all("field_results" not in item and "submission" not in item for item in assigned[1:])
    assert "field_results" not in get_client_question(team_id, assigned[1]["id"])
    changed, error = process_submission(team_id, "Q01", reference_answer(saved_question()))
    assert changed is None and "already been answered" in error


@pytest.mark.parametrize("old_response", [None, "{}"])
def test_legacy_saved_scores_support_review_without_regrading(old_response):
    team_id, _ = register_team("Legacy feedback", "One", "Two")
    result, error = process_submission(team_id, "Q01", reference_answer(saved_question()))
    assert error is None
    conn = get_db_connection()
    conn.execute("UPDATE submissions SET response_json = ?, cause_score = 3, correction_score = 0, total_score = 12 WHERE team_id = ?", (old_response, team_id))
    conn.execute("UPDATE questions SET cause = 'New answer unavailable to client', correction = 'Another new answer' WHERE id = 'Q01'")
    conn.commit(); conn.close()
    reviewed = get_client_question(team_id, "Q01")
    rows = fields_by_key(reviewed)
    assert "cause" not in rows and len(rows) == 4
    assert rows["correction"]["score"] == 0 and rows["correction"]["status"] == "incorrect"
    assert rows["correction"]["max_score"] == 6  # Preserve the historical 30% rubric.
    assert reviewed["awarded_score"] == 12
    assert "New answer" not in json.dumps(reviewed) and "Another new answer" not in json.dumps(reviewed)


def test_organizer_total_override_leaves_automated_field_results_intact():
    team_id, _ = register_team("Override feedback", "One", "Two")
    result, error = process_submission(team_id, "Q01", {"error_type": "Logical Error"})
    assert error is None
    conn = get_db_connection()
    conn.execute("UPDATE submissions SET total_score = 20, override_reason = 'Reviewed by organizer' WHERE team_id = ?", (team_id,))
    conn.commit(); conn.close()
    reviewed = get_client_question(team_id, "Q01")
    assert reviewed["answer_status"] == "correct" and reviewed["awarded_score"] == 20
    assert reviewed["score_overridden"]
    assert reviewed["field_results"] == result["field_results"]


def test_participant_apis_only_return_own_locked_feedback():
    team_id, _ = register_team("API feedback", "One", "Two")
    other_id, _ = register_team("Other feedback", "Three", "Four")
    start_event()
    result, error = process_submission(team_id, "Q01", {"correction": "Participant's own code"})
    assert error is None
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["team_id"] = team_id
        answered = client.get("/api/question/Q01").get_json()["question"]
        progress = client.get("/api/team-progress").get_json()["questions"]
        assert answered["field_results"] == progress[0]["field_results"] == result["field_results"]
        assert not {"bug_location", "error_type", "expected_output", "cause", "correction", "cause_keywords", "correction_keywords"} & answered.keys()
        assert answered["submission"]["correction"] == "Participant's own code"
        assert "cause" not in answered["submission"]
        assert all("field_results" not in item for item in progress[1:])
        with client.session_transaction() as session:
            session["team_id"] = other_id
        assert "field_results" not in client.get("/api/question/Q01").get_json()["question"]
        assert client.get("/api/question/Q02").status_code == 404


def test_result_page_renders_own_individual_verdicts_without_reference_answers():
    team_id, _ = register_team("Result field review", "One", "Two")
    other_id, _ = register_team("Separate result review", "Three", "Four")
    item = saved_question()
    response = reference_answer(item)
    response.update(expected_output="<script>badOutput()</script>", cause="Only my own explanation", correction="Only my own fix")
    result, error = process_submission(team_id, "Q01", response)
    assert error is None
    assert process_submission(other_id, "Q01", {"cause": "Another participant private note"})[1] is None
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["team_id"] = team_id
        page = client.get("/result")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'data-question-id="Q01"' in html and 'data-question-id="Q02"' not in html
        for field in result["field_results"]:
            assert f'data-field="{field["key"]}" data-answer-status="{field["status"]}"' in html
        assert "Only my own explanation" not in html and "Only my own fix" in html
        assert "Another participant private note" not in html
        assert "&lt;script&gt;badOutput()&lt;/script&gt;" in html
        assert "<script>badOutput()" not in html
        assert item["cause"] not in html and item["correction"] not in html
        assert "automated points before hint or swap deductions" in html
        with client.session_transaction() as session:
            session.clear()
        assert 'data-question-id="Q01"' not in client.get("/result").get_data(as_text=True)


def test_result_review_distinguishes_organizer_total_from_individual_checks():
    team_id, _ = register_team("Result override review", "One", "Two")
    assert activate_rubber_duck(team_id, "Q01")[0]
    result, error = process_submission(team_id, "Q01", {"cause": "Unrelated words"})
    assert error is None
    conn = get_db_connection()
    conn.execute("UPDATE submissions SET total_score = 20, override_reason = 'Reviewed' WHERE team_id = ?", (team_id,))
    conn.commit(); conn.close()
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["team_id"] = team_id
        html = client.get("/result").get_data(as_text=True)
        assert "Organizer adjusted the total; individual checks remain automated." in html
        assert "Hint used · −2 points" in html
        assert 'data-field="correction" data-answer-status="incorrect"' in html
        assert 'data-field="cause"' not in html
        assert "POINTS AWARDED" in html
