"""Validate the beginner C/Python bank and the corrected programs' output."""
import contextlib
from collections import Counter
import io
import json
import re
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BANK = json.loads((ROOT / "data/questions.json").read_text())


def test_bank_languages_ids_and_descriptive_keywords():
    assert [q["id"] for q in BANK] == [f"Q{i:02d}" for i in range(1, 31)]
    assert Counter(q["language"] for q in BANK) == {"C": 15, "Python": 15}
    assert sum(q["difficulty"] == "Difficult" for q in BANK) == 5
    for question in BANK:
        assert question["task"].strip(), f'{question["id"]} needs a visible intended-behavior statement'
        assert question["points"] == {"Easy": 20, "Medium": 25, "Difficult": 35}[question["difficulty"]]
        assert len(set(question["cause_keywords"])) >= 2
        assert len(set(question["correction_keywords"])) >= 2


def _program_output(question, source_code, tmp_path):
    """Execute only these repository-owned sample programs, never participant code."""
    if question["language"] == "Python":
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(source_code, question["id"], "exec"), {})
        return output.getvalue().strip()
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("C compiler unavailable")
    source = tmp_path / "question.c"
    program = tmp_path / "question"
    source.write_text(source_code)
    subprocess.run([compiler, "-std=c11", "-pedantic-errors", str(source), "-o", str(program)], check=True, capture_output=True, timeout=10)
    return subprocess.run([str(program)], check=True, capture_output=True, text=True, timeout=2).stdout.strip()


def _corrected_program(question):
    lines = question["code"].splitlines()
    location = int(re.search(r"\d+", question["bug_location"]).group()) - 1
    correction = question["correction"]
    assert correction.strip() and "\n" not in correction and "\r" not in correction
    assert 0 <= location < len(lines)
    assert lines[location] != correction
    lines[location] = correction
    return "\n".join(lines)


@pytest.mark.parametrize("question", BANK, ids=lambda q: q["id"])
def test_corrected_program_matches_answer_key(question, tmp_path):
    assert _program_output(question, _corrected_program(question), tmp_path) == question["expected_output"].strip()


@pytest.mark.parametrize("question", [q for q in BANK if q["error_type"] == "Logical Error"], ids=lambda q: q["id"])
def test_logical_bug_really_changes_required_output(question, tmp_path):
    assert _program_output(question, question["code"], tmp_path) != question["expected_output"].strip()


@pytest.mark.parametrize("question_id,old,new,expected", [
    ("Q14", "print_status(0)", "print_status(1)", "ERROR"),
    ("Q14", "print_status(0)", "print_status(2)", "DONE"),
    ("Q23", "number = 123", "number = 907", "Digit sum: 16"),
    ("Q24", "[30, 10, 20]", "[4, -1, 4, 0]", "[-1, 0, 4, 4]"),
    ("Q26", "larger(3, 8)", "larger(8, 3)", "Larger: 8"),
    ("Q26", "larger(3, 8)", "larger(-8, -3)", "Larger: -3"),
])
def test_replacement_fixes_handle_other_inputs(question_id, old, new, expected, tmp_path):
    question = next(q for q in BANK if q["id"] == question_id)
    corrected = _corrected_program(question)
    assert old in corrected
    assert _program_output(question, corrected.replace(old, new), tmp_path) == expected


@pytest.mark.parametrize("question", BANK, ids=lambda q: q["id"])
def test_reference_code_line_receives_full_credit(question):
    from scoring import evaluate_submission
    answer = {field: question[field] for field in ("error_type", "expected_output", "cause", "correction")}
    answer["error_location"] = question["bug_location"]
    evaluation = evaluate_submission(question, answer)
    assert evaluation["raw_score"] == question["points"]
    assert len(evaluation["field_results"]) == 4
    assert all(field["status"] == "correct" for field in evaluation["field_results"])


def test_bank_migration_preserves_competition_data_and_organizer_edits():
    from database import init_db, register_team, get_db_connection, QUESTION_BANK_VERSION
    from scoring import process_submission
    init_db(force_reset=True)
    team_id, error = register_team("Migration preservation", "One", "Two")
    assert error is None
    from database import get_team_assigned_questions
    first_id = get_team_assigned_questions(team_id)[0]["id"]
    q = next(question for question in BANK if question["id"] == first_id)
    answer = {field: q[field] for field in ("error_type", "expected_output", "cause", "correction")}
    answer["error_location"] = q["bug_location"]
    scored, error = process_submission(team_id, q["id"], answer)
    assert error is None
    conn = get_db_connection()
    conn.execute("UPDATE questions SET language='Java', code='old code', is_active=0 WHERE id='Q03'")
    conn.execute("DELETE FROM data_migrations WHERE name = ?", (QUESTION_BANK_VERSION,))
    conn.commit()
    conn.close()
    init_db()
    conn = get_db_connection()
    replacement = dict(conn.execute("SELECT * FROM questions WHERE id='Q03'").fetchone())
    assert replacement["language"] in ("C", "Python") and replacement["code"] == BANK[2]["code"]
    assert replacement["is_active"] == 0 and len(json.loads(replacement["cause_keywords"])) >= 2
    assert conn.execute("SELECT COUNT(*) FROM question_assignments WHERE team_id=?", (team_id,)).fetchone()[0] == 30
    assert conn.execute("SELECT COUNT(*) FROM submissions WHERE team_id=?", (team_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT score FROM scores WHERE team_id=?", (team_id,)).fetchone()[0] == scored["total_score"]
    conn.execute("UPDATE questions SET title='Organizer title' WHERE id='Q03'")
    conn.commit()
    conn.close()
    init_db()
    conn = get_db_connection()
    assert conn.execute("SELECT title FROM questions WHERE id='Q03'").fetchone()[0] == "Organizer title"
    conn.close()
