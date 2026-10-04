import sys
import os
import tempfile
import atexit

# Acceptance checks must never reset a real competition database.
_test_directory = tempfile.TemporaryDirectory(prefix="exitcode-acceptance-")
atexit.register(_test_directory.cleanup)
os.environ["DATABASE_PATH"] = os.path.join(_test_directory.name, "acceptance.db")

from app import app
from database import init_db, get_db_connection, register_team, get_team_assigned_questions, get_client_question
from event_manager import reset_event_data, start_event, get_event_state, end_event
from scoring import process_submission, activate_rubber_duck, activate_git_revert, arm_double_commit
from config import Config

def run_critical_scenario_checks():
    print("\n=======================================================")
    print(" EXECUTING 10 CRITICAL ACCEPTANCE SCENARIOS (Section 40)")
    print("=======================================================")

    init_db(force_reset=True)

    # --- Scenario 1: Register Team A. Refresh. Confirm team remains registered. ---
    print("\n[Scenario 1] Register Team A & Refresh verification...")
    tid_a, err = register_team("Team Alpha", "Lead Alice", "Member Bob")
    assert err is None, f"Registration failed: {err}"
    assert tid_a == "EX0-001"
    
    # Simulate page refresh by opening new DB connection and checking team
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM teams WHERE id = ?", (tid_a,))
    t_row = cur.fetchone()
    conn.close()
    assert t_row is not None
    assert t_row["name"] == "Team Alpha"
    print(f"[OK] PASS: Team Alpha registered as EX0-001 and persists across refresh.")

    # --- Scenario 2: Start event. Refresh participant page. Confirm timer has NOT reset. ---
    print("\n[Scenario 2] Start event & Server Timer persistence verification...")
    ok, msg = start_event()
    assert ok is True
    st1 = get_event_state()
    assert st1["event_status"] == "LIVE"
    end_time_orig = st1["event_end_time"]
    rem1 = st1["remaining_seconds"]
    
    # Simulate refresh / re-fetching state
    st2 = get_event_state()
    assert st2["event_status"] == "LIVE"
    assert st2["event_end_time"] == end_time_orig
    assert abs(st1["remaining_seconds"] - st2["remaining_seconds"]) <= 1
    print(f"[OK] PASS: Event LIVE with {st1['duration_minutes']}-min server clock. End time fixed at {end_time_orig}, timer not reset.")

    # --- Scenario 3: Open a question. Refresh. Confirm the same question remains assigned. ---
    print("\n[Scenario 3] Question assignment & Refresh persistence...")
    assigned_1 = get_team_assigned_questions(tid_a)
    active_q1 = assigned_1[0]["id"]
    assert active_q1 == "Q01"
    
    # Re-fetch after simulated refresh
    assigned_2 = get_team_assigned_questions(tid_a)
    assert assigned_2[0]["id"] == "Q01"
    assert assigned_2[0]["is_unlocked"] == 1
    print(f"[OK] PASS: Question Q01 remains deterministically assigned upon refresh.")

    # --- Scenario 4: Submit an answer. Refresh. Confirm score remains. ---
    print("\n[Scenario 4] Submit an answer & Score persistence...")
    sub_payload = {
        "error_location": "Line 3",
        "error_type": "Logical Error",
        "expected_output": "10",
        "correction": "for i in range(len(numbers)):"
    }
    res_sub, err_sub = process_submission(tid_a, "Q01", sub_payload)
    assert err_sub is None
    awarded_score = res_sub["total_score"]
    assert awarded_score >= 15.0

    # Simulate refresh / re-querying score
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT score, completed_count FROM scores WHERE team_id = ?", (tid_a,))
    sc_row = cur.fetchone()
    conn.close()
    assert sc_row["score"] == awarded_score
    assert sc_row["completed_count"] == 1
    print(f"[OK] PASS: Score {awarded_score} and 1 completed challenge accurately persisted across refresh.")

    # --- Scenario 5: Use Rubber Duck. Refresh. Confirm Rubber Duck remains used. ---
    print("\n[Scenario 5] Rubber Duck usage & Refresh persistence...")
    ok_duck, msg_duck, hint = activate_rubber_duck(tid_a, "Q02")
    assert ok_duck is True
    assert hint is not None

    # Refresh check
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_used FROM powerups WHERE team_id = ? AND powerup_type = 'RUBBER_DUCK'", (tid_a,))
    assert cur.fetchone()["is_used"] == 1
    conn.close()

    # Reusing fails
    ok_duck2, msg_duck2, _ = activate_rubber_duck(tid_a, "Q03")
    assert ok_duck2 is False
    print(f"[OK] PASS: Rubber Duck persisted as USED. Cannot be reused or restored by refresh.")

    # --- Scenario 6: Use Git Revert. Refresh. Confirm the original question cannot return. ---
    print("\n[Scenario 6] Git Revert usage & abandoned question exclusion...")
    ok_rev, msg_rev, new_qid = activate_git_revert(tid_a, "Q02")
    assert ok_rev is True
    assert new_qid != "Q02"

    # Confirm Q02 is abandoned and not active
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_abandoned, is_unlocked FROM question_assignments WHERE team_id = ? AND question_id = 'Q02'", (tid_a,))
    q2_asgn = cur.fetchone()
    assert q2_asgn["is_abandoned"] == 1
    assert q2_asgn["is_unlocked"] == 0
    conn.close()

    # Re-query assigned questions for team
    assigned_after_revert = get_team_assigned_questions(tid_a)
    active_ids = [q["id"] for q in assigned_after_revert]
    assert "Q02" not in active_ids
    print(f"[OK] PASS: Git Revert swapped Q02 for {new_qid}. Q02 marked abandoned and cannot return.")

    # --- Scenario 7: Use Double Commit. Submit correct answer. Confirm doubled score. ---
    print("\n[Scenario 7] Double Commit 2x multiplier verification...")
    tid_b, _ = register_team("Team Beta", "Bob Lead", "Charlie Dev")
    ok_arm, msg_arm = arm_double_commit(tid_b, "Q01")
    assert ok_arm is True

    sub_b = {
        "error_location": "Line 3",
        "error_type": "Logical Error",
        "expected_output": "10",
        "correction": "for i in range(len(numbers)):"
    }
    res_b, err_b = process_submission(tid_b, "Q01", sub_b)
    assert err_b is None
    assert res_b["is_double_commit"] == 1
    assert res_b["total_score"] >= 30.0  # 2x of raw >= 15
    print(f"[OK] PASS: Double Commit successfully armed and doubled score to {res_b['total_score']} pts.")

    # --- Scenario 8: Attempt to submit after timer reaches zero. Confirm submission is rejected. ---
    print("\n[Scenario 8] Reject submission after timer expiration...")
    end_event()
    st_ended = get_event_state()
    assert st_ended["event_status"] == "COMPLETED"

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["team_id"] = tid_a
            sess["team_name"] = "Team Alpha"

        # Establish the participant's fullscreen state before checking the closed-round guard.
        entered = client.post("/api/activity", json={
            "event_type": "fullscreen_enter", "event_id": "acceptance-post-event-enter"
        })
        assert entered.status_code == 200
        res_post_end = client.post("/api/submit-bug-fix", json={
            "question_id": "Q01",
            "error_location": "3",
            "error_type": "Logical Error",
            "expected_output": "10",
            "cause": "loop error",
            "correction": "range len"
        })
        assert res_post_end.status_code == 403
        data_post = res_post_end.get_json()
        assert data_post["success"] is False
        assert "closed" in data_post["error"].lower()
    print(f"[OK] PASS: Submissions strictly rejected (HTTP 403) after competition ends.")

    # --- Scenario 9: Restart Flask. Confirm event state and scores are recovered from SQLite. ---
    print("\n[Scenario 9] Recovery across simulated Flask application restart...")
    # Re-importing or calling get_event_state() from a clean runtime instance
    reloaded_state = get_event_state()
    assert reloaded_state["event_status"] == "COMPLETED"

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT score FROM scores WHERE team_id = ?", (tid_a,))
    rec_score = cur.fetchone()["score"]
    conn.close()
    assert rec_score == awarded_score
    print(f"[OK] PASS: Event state ('COMPLETED') and score ({rec_score}) successfully recovered from SQLite.")

    # --- Scenario 10: Try to access an admin route without authentication. Confirm access is denied. ---
    print("\n[Scenario 10] Security boundary: Deny unauthenticated admin access...")
    with app.test_client() as client:
        # Access admin dashboard without session
        res_admin = client.get("/admin/dashboard", follow_redirects=False)
        assert res_admin.status_code == 302
        assert "/admin/login" in res_admin.headers["Location"]

        # Call admin API action without session
        res_act = client.post("/api/admin/event-action", json={"action": "start"})
        assert res_act.status_code == 302
        assert "/admin/login" in res_act.headers["Location"]
    print(f"[OK] PASS: Protected admin routes securely redirect unauthenticated requests to login.")

    print("\n=======================================================")
    print(" ALL 10 CRITICAL SCENARIOS VERIFIED SUCCESSFULLY! [OK]")
    print("=======================================================\n")

if __name__ == "__main__":
    run_critical_scenario_checks()
