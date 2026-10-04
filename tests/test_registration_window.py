"""The organiser's start is the atomic cut-off for new team registrations."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import database
from app import app
from database import get_db_connection, init_db, register_team
from event_manager import end_event, pause_event, start_event


@pytest.fixture
def client():
    init_db(force_reset=True)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def team_count():
    conn = get_db_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    finally:
        conn.close()


def advance_round(status):
    assert start_event()[0]
    if status == "PAUSED":
        assert pause_event()[0]
    elif status == "COMPLETED":
        assert end_event()[0]


def test_waiting_room_accepts_registration(client):
    response = client.post("/register", data={
        "name": "Before the start", "member1": "Lead", "member2": "Member",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/waiting-room")
    assert team_count() == 1


@pytest.mark.parametrize("status", ["LIVE", "PAUSED", "COMPLETED"])
def test_new_teams_rejected_after_start_without_partial_rows(client, status):
    advance_round(status)
    team_id, error = register_team("Too late", "Lead", "Member")
    assert team_id is None
    assert "closed" in error.lower()
    conn = get_db_connection()
    try:
        for table in ("teams", "participants", "scores", "powerups", "question_assignments"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["LIVE", "PAUSED", "COMPLETED"])
def test_stale_registration_form_cannot_post_after_start(client, status):
    before = client.get("/register").get_data(as_text=True)
    assert 'data-registration-open="true"' in before
    advance_round(status)
    response = client.post("/register", data={
        "name": "Stale browser form", "member1": "Lead", "member2": "Member",
    })
    assert response.status_code == 403
    assert "Registration is closed" in response.get_data(as_text=True)
    assert 'data-registration-open="false"' in response.get_data(as_text=True)
    assert team_count() == 0


@pytest.mark.parametrize("status", ["LIVE", "PAUSED", "COMPLETED"])
def test_existing_teams_can_still_login_after_registration_closes(client, status):
    team_id, error = register_team("Existing team", "Lead", "Member")
    assert not error
    advance_round(status)
    response = client.post("/register", data={"action": "login", "team_lookup": team_id})
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["team_id"] == team_id
    assert team_count() == 1


def test_published_results_keep_registration_closed_even_if_state_waiting(client):
    conn = get_db_connection()
    conn.execute("UPDATE competition_controls SET results_published = 1 WHERE id = 1")
    conn.commit()
    conn.close()
    team_id, error = register_team("After publication", "Lead", "Member")
    assert team_id is None and "closed" in error.lower()
    page = client.get("/register").get_data(as_text=True)
    assert 'data-registration-open="false"' in page
    assert 'id="login-tab" role="tab" aria-selected="true"' in page
    assert team_count() == 0


def test_registration_checks_status_after_acquiring_start_transaction_lock(client, monkeypatch):
    """A competing start wins the lock; registration must see its committed state."""
    organiser_conn = get_db_connection()
    organiser_conn.execute("BEGIN IMMEDIATE")
    organiser_conn.execute("UPDATE event_state SET event_status = 'LIVE' WHERE id = 1")
    attempted_write_lock = Event()
    original_connect = database.get_db_connection

    def registration_connection():
        conn = original_connect()
        conn.set_trace_callback(lambda sql: attempted_write_lock.set() if sql == "BEGIN IMMEDIATE" else None)
        return conn

    monkeypatch.setattr(database, "get_db_connection", registration_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(register_team, "Concurrent signup", "Lead", "Member")
        try:
            assert attempted_write_lock.wait(timeout=5), "Registration never attempted its atomic write lock"
        finally:
            organiser_conn.commit()
            organiser_conn.close()
        team_id, error = pending.result(timeout=5)
    assert team_id is None
    assert "closed" in error.lower()
    assert team_count() == 0
