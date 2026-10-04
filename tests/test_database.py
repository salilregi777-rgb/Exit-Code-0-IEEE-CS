import pytest
import sqlite3
import json
from database import init_db, get_db_connection, register_team, generate_team_id, get_team_assigned_questions, get_client_question
from event_manager import reset_event_data
from database import QUESTION_BANK_VERSION

@pytest.fixture(autouse=True)
def setup_clean_db():
    init_db(force_reset=True)
    yield
    reset_event_data("RESET EVENT")

def test_database_initialization():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Verify core 9 tables exist per Section 31
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r["name"] for r in cur.fetchall()}
    required_tables = {
        "teams", "participants", "questions", "question_assignments",
        "submissions", "scores", "powerups", "event_state", "admin_actions"
    }
    assert required_tables.issubset(tables)

    # Check 30 verified debugging questions seeded
    cur.execute("SELECT COUNT(*) as count FROM questions WHERE is_active = 1")
    count = cur.fetchone()["count"]
    assert count == 30

    # Check initial event_state
    cur.execute("SELECT * FROM event_state WHERE id = 1")
    st = cur.fetchone()
    assert st["event_status"] == "WAITING"
    assert st["duration_minutes"] == 40
    assert st["remaining_seconds"] == 2400

    conn.close()

def test_team_registration():
    team_id, err = register_team("ByteForce", "Alice Smith", "Bob Jones", "Charlie Brown", "byteforce@example.com")
    assert err is None
    assert team_id.startswith("EX0-")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
    team = cur.fetchone()
    assert team["name"] == "ByteForce"
    assert team["is_active"] == 1

    # Verify participants (2-3 members)
    cur.execute("SELECT name, role FROM participants WHERE team_id = ? ORDER BY id ASC", (team_id,))
    members = cur.fetchall()
    assert len(members) == 3
    assert members[0]["name"] == "Alice Smith"
    assert members[1]["name"] == "Bob Jones"
    assert members[2]["name"] == "Charlie Brown"

    # Verify score row initialized
    cur.execute("SELECT score, completed_count FROM scores WHERE team_id = ?", (team_id,))
    sc = cur.fetchone()
    assert sc["score"] == 0.0
    assert sc["completed_count"] == 0

    # Verify 3 powerups initialized (Rubber Duck, Git Revert, Double Commit)
    cur.execute("SELECT powerup_type, is_used FROM powerups WHERE team_id = ?", (team_id,))
    pu = {r["powerup_type"]: r["is_used"] for r in cur.fetchall()}
    assert pu["RUBBER_DUCK"] == 0
    assert pu["GIT_REVERT"] == 0
    assert pu["DOUBLE_COMMIT"] == 0

    conn.close()

def test_waiting_round_adopts_new_duration_without_losing_teams():
    team_id, error = register_team("Existing waiting team", "Dev A", "Dev B")
    assert error is None
    conn = get_db_connection()
    conn.execute("UPDATE event_state SET duration_minutes = 70, remaining_seconds = 4200 WHERE id = 1")
    conn.commit()
    conn.close()

    init_db()
    conn = get_db_connection()
    state = conn.execute("SELECT * FROM event_state WHERE id = 1").fetchone()
    assert state["duration_minutes"] == 40 and state["remaining_seconds"] == 2400
    assert conn.execute("SELECT COUNT(*) FROM question_assignments WHERE team_id = ?", (team_id,)).fetchone()[0] == 30
    conn.close()

@pytest.mark.parametrize("status", ["LIVE", "PAUSED", "COMPLETED"])
def test_duration_update_preserves_rounds_already_started(status):
    conn = get_db_connection()
    conn.execute("""UPDATE event_state SET event_status = ?, duration_minutes = 70,
        remaining_seconds = ?, is_paused = ?, event_start_time = '2026-10-07T09:20:00+00:00',
        event_end_time = '2026-10-07T10:30:00+00:00' WHERE id = 1""",
        (status, 0 if status == "COMPLETED" else 1800, int(status == "PAUSED")))
    conn.commit()
    before = dict(conn.execute("SELECT * FROM event_state WHERE id = 1").fetchone())
    conn.close()

    init_db()
    conn = get_db_connection()
    assert dict(conn.execute("SELECT * FROM event_state WHERE id = 1").fetchone()) == before
    conn.close()

def test_duplicate_team_name():
    team1_id, err1 = register_team("NullPointers", "Member 1", "Member 2")
    assert err1 is None
    assert team1_id is not None

    # Case-insensitive duplicate rejection
    team2_id, err2 = register_team("nullpointers", "Member 3", "Member 4")
    assert team2_id is None
    assert "already registered" in err2.lower()

def test_team_size_validation():
    # Exactly 2 members -> Valid
    t2, err2 = register_team("DuoSquad", "Lead A", "Member B")
    assert err2 is None
    assert t2 is not None

    # Exactly 3 members -> Valid
    t3, err3 = register_team("TrioSquad", "Lead A", "Member B", "Member C")
    assert err3 is None
    assert t3 is not None

    # Less than 2 members -> Invalid
    t1, err1 = register_team("SoloSquad", "Solo Lead", "")
    assert t1 is None
    assert "at least 2 members" in err1.lower()

    # Empty name -> Invalid
    t0, err0 = register_team("", "Lead A", "Member B")
    assert t0 is None
    assert "team name is required" in err0.lower()

def test_question_assignment():
    team_id, _ = register_team("CodeBreakers", "Dev 1", "Dev 2")
    assigned = get_team_assigned_questions(team_id)
    assert len(assigned) == 30

    # Q01 is unlocked immediately, subsequent questions locked
    assert assigned[0]["is_unlocked"] == 1
    assert assigned[0]["question_order"] == 1
    assert assigned[1]["is_unlocked"] == 0

    # Client question does NOT expose answers
    client_q = get_client_question(team_id, assigned[0]["id"])
    assert "code" in client_q
    assert "title" in client_q
    assert "points" in client_q
    assert "cause" not in client_q
    assert "correction" not in client_q
    assert "expected_output" not in client_q
    assert "bug_location" not in client_q

def test_question_persistence():
    team_id, _ = register_team("PersistentTeam", "Dev A", "Dev B")
    first_fetch = get_team_assigned_questions(team_id)
    # Refresh/re-query
    second_fetch = get_team_assigned_questions(team_id)
    assert [q["id"] for q in first_fetch] == [q["id"] for q in second_fetch]
    assert [q["is_unlocked"] for q in first_fetch] == [q["is_unlocked"] for q in second_fetch]


def test_shared_shuffled_order_has_one_difficult_question_inside_each_six():
    first, _ = register_team("First shared order", "One", "Two")
    second, _ = register_team("Second shared order", "One", "Two")
    questions = get_team_assigned_questions(first)
    assert [q["id"] for q in questions] == [q["id"] for q in get_team_assigned_questions(second)]
    assert [q["id"] for q in questions] != sorted(q["id"] for q in questions)
    assert len({q["id"] for q in questions}) == 30
    assert questions[0]["id"] == "Q01"
    for start in range(0, 30, 6):
        block = questions[start:start + 6]
        difficult_positions = [i for i, q in enumerate(block) if q["difficulty"] == "Difficult"]
        assert difficult_positions in ([2], [3])
        assert all(q["difficulty"] in ("Easy", "Medium") for i, q in enumerate(block) if i not in difficult_positions)
    assert {q["difficulty"] for q in questions} == {"Easy", "Medium", "Difficult"}
    assert all(q["points"] == {"Easy": 20, "Medium": 25, "Difficult": 35}[q["difficulty"]] for q in questions)


@pytest.mark.parametrize("status", ["WAITING", "LIVE", "PAUSED", "COMPLETED"])
def test_bank_order_migration_only_reorders_untouched_waiting_teams(status):
    team_id, _ = register_team("Existing sequential order", "One", "Two")
    conn = get_db_connection()
    for order in range(1, 31):
        conn.execute("UPDATE question_assignments SET question_order = ?, is_unlocked = ? WHERE team_id = ? AND question_id = ?",
                     (order, int(order == 1), team_id, f"Q{order:02d}"))
    conn.execute("UPDATE event_state SET event_status = ? WHERE id = 1", (status,))
    conn.execute("DELETE FROM data_migrations WHERE name = ?", (QUESTION_BANK_VERSION,))
    conn.commit()
    conn.close()
    init_db()
    order = [q["id"] for q in get_team_assigned_questions(team_id)]
    sequential = [f"Q{i:02d}" for i in range(1, 31)]
    assert (order != sequential) if status == "WAITING" else (order == sequential)
    assert sum(q["is_unlocked"] for q in get_team_assigned_questions(team_id)) == 1


def test_bank_migration_preserves_attempted_order_and_original_review_points():
    team_id, _ = register_team("Historical score", "One", "Two")
    conn = get_db_connection()
    conn.execute("UPDATE questions SET points = 20 WHERE id = 'Q09'")
    conn.execute("""INSERT INTO submissions(team_id, question_id, error_loc_score,
        error_type_score, cause_score, output_score, correction_score, total_score)
        VALUES (?, 'Q09', 2, 3, 5, 4, 6, 20)""", (team_id,))
    conn.execute("UPDATE scores SET score = 20, completed_count = 1 WHERE team_id = ?", (team_id,))
    before = [tuple(r) for r in conn.execute("SELECT * FROM question_assignments WHERE team_id = ? ORDER BY id", (team_id,))]
    conn.execute("DELETE FROM data_migrations WHERE name = ?", (QUESTION_BANK_VERSION,))
    conn.commit()
    conn.close()
    init_db()
    conn = get_db_connection()
    assert [tuple(r) for r in conn.execute("SELECT * FROM question_assignments WHERE team_id = ? ORDER BY id", (team_id,))] == before
    saved = conn.execute("SELECT * FROM submissions WHERE team_id = ?", (team_id,)).fetchone()
    assert saved["total_score"] == 20
    assert conn.execute("SELECT score FROM scores WHERE team_id = ?", (team_id,)).fetchone()[0] == 20
    assert conn.execute("SELECT points FROM questions WHERE id = 'Q09'").fetchone()[0] == 35
    snapshot = json.loads(saved["response_json"])
    assert snapshot["base_points"] == snapshot["max_score"] == 20
    assert snapshot["answer_status"] == "correct"
    conn.close()


def test_order_uses_only_active_questions_without_duplicates():
    conn = get_db_connection()
    conn.execute("UPDATE questions SET is_active = 0 WHERE id IN ('Q14', 'Q27')")
    conn.commit()
    conn.close()
    team_id, _ = register_team("Organiser bank changes", "One", "Two")
    ids = [q["id"] for q in get_team_assigned_questions(team_id)]
    assert len(ids) == len(set(ids)) == 28
    assert "Q14" not in ids and "Q27" not in ids
