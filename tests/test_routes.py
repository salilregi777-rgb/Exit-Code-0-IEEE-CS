import pytest
pytestmark = pytest.mark.usefixtures("ordered_bank")
import re
from app import app
from database import init_db, get_db_connection, register_team
from event_manager import reset_event_data, start_event, end_event
from scoring import process_submission
from config import Config

@pytest.fixture
def client():
    app.config["TESTING"] = True
    init_db(force_reset=True)
    with app.test_client() as client:
        yield client
    reset_event_data("RESET EVENT")

def test_landing_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"EXIT CODE" in res.data
    assert b"IEEE Computer Society" in res.data
    assert b"7 October 2026" in res.data
    assert "40 minutes" in re.sub(r"<[^>]+>", "", res.get_data(as_text=True)).lower()
    assert b"enter the arena" in res.data.lower()

def test_admin_login(client):
    # 1. Unauthenticated access to admin dashboard redirects to admin login
    res_unauth = client.get("/admin/dashboard", follow_redirects=True)
    assert b"username" in res_unauth.data.lower() and b"password" in res_unauth.data.lower()

    # 2. Invalid credentials fail
    res_bad = client.post("/admin/login", data={
        "username": "wronguser",
        "password": "wrongpassword"
    }, follow_redirects=True)
    assert b"Invalid admin security credentials" in res_bad.data

    # 3. Valid credentials succeed
    res_good = client.post("/admin/login", data={
        "username": Config.ADMIN_USERNAME,
        "password": Config.ADMIN_PASSWORD
    }, follow_redirects=True)
    assert res_good.status_code == 200
    assert b"event control" in res_good.data.lower()

def test_leaderboard(client):
    # Register two teams
    tid1, _ = register_team("AlphaSquad", "A1", "A2")
    tid2, _ = register_team("BetaSquad", "B1", "B2")

    res = client.get("/api/leaderboard-data")
    assert res.status_code == 200
    data = res.get_json()
    assert "leaderboard" in data
    assert len(data["leaderboard"]) >= 2
    team_names = [t["name"] for t in data["leaderboard"]]
    assert "AlphaSquad" in team_names
    assert "BetaSquad" in team_names

def test_tie_breaker(client):
    # Team 1 and Team 2 have equal scores, but Team 1 completed more questions
    tid1, _ = register_team("TeamTiedHigherCount", "A1", "A2")
    tid2, _ = register_team("TeamTiedLowerCount", "B1", "B2")

    conn = get_db_connection()
    cur = conn.cursor()
    # Team 1: score 20, 2 questions completed
    cur.execute("""
        UPDATE scores 
        SET score = 20.0, completed_count = 2, last_submission_time = '2026-10-07 15:10:00'
        WHERE team_id = ?
    """, (tid1,))
    # Team 2: score 20, 1 question completed
    cur.execute("""
        UPDATE scores 
        SET score = 20.0, completed_count = 1, last_submission_time = '2026-10-07 15:05:00'
        WHERE team_id = ?
    """, (tid2,))
    conn.commit()
    conn.close()

    res = client.get("/api/leaderboard-data")
    data = res.get_json()
    ranks = [t["id"] for t in data["leaderboard"] if t["id"] in (tid1, tid2)]
    # tid1 should be ranked higher due to completed_count
    assert ranks[0] == tid1
    assert ranks[1] == tid2

def test_result_page(client):
    tid, _ = register_team("VictorTeam", "Vic1", "Vic2")
    end_event()

    with client.session_transaction() as sess:
        sess["team_id"] = tid
        sess["team_name"] = "VictorTeam"

    res = client.get("/result")
    assert res.status_code == 200
    assert b"EXIT CODE 0" in res.data
    assert b"debugging complete" in res.data.lower()
    assert b"VictorTeam" in res.data
    assert b"process completed" in res.data.lower()
    assert b"winners" in res.data.lower() or b"verification" in res.data.lower()

def test_admin_score_override(client):
    tid, _ = register_team("OverrideTeam", "M1", "M2")
    
    # Create a submission
    sub = {
        "error_location": "Line 3",
        "error_type": "Logical Error",
        "expected_output": "10",
        "cause": "Off by one error in loop",
        "correction": "Use range(len(numbers))"
    }
    res_sub, _ = process_submission(tid, "Q01", sub)

    # Get submission ID
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, total_score FROM submissions WHERE team_id = ?", (tid,))
    row = cur.fetchone()
    sub_id = row["id"]
    conn.close()

    # Authenticate as admin
    with client.session_transaction() as sess:
        sess["is_admin"] = True

    # Override score to 20.0 with reason
    res_ov = client.post("/api/admin/override-score", json={
        "submission_id": sub_id,
        "new_score": 20.0,
        "reason": "Alternative root-cause explanation verified by IEEE judge"
    })
    assert res_ov.status_code == 200
    assert res_ov.get_json()["success"] is True

    # Verify score was updated in database
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT total_score, override_reason FROM submissions WHERE id = ?", (sub_id,))
    s_updated = cur.fetchone()
    assert s_updated["total_score"] == 20.0
    assert "Alternative" in s_updated["override_reason"]

    cur.execute("SELECT score FROM scores WHERE team_id = ?", (tid,))
    assert cur.fetchone()["score"] == 20.0
    conn.close()

def test_route_security(client):
    # Unauthenticated user accessing arena is redirected to register/login
    res_arena = client.get("/arena", follow_redirects=False)
    assert res_arena.status_code == 302
    assert "/register" in res_arena.headers["Location"]

    # Unauthenticated user accessing admin action is redirected to admin login
    res_admin_act = client.post("/api/admin/event-action", json={"action": "start"})
    assert res_admin_act.status_code == 302
    assert "/admin/login" in res_admin_act.headers["Location"]
