"""Phase 1 migration and concurrent-team regression coverage."""
from concurrent.futures import ThreadPoolExecutor

from app import app
from database import init_db, register_team, get_db_connection, get_competition_controls
from event_manager import start_event, reset_event_data


def test_twenty_teams_submit_independently_and_network_retries_are_idempotent():
    init_db(force_reset=True)
    teams = [register_team(f"Concurrent {i}", "Member A", "Member B")[0] for i in range(20)]
    start_event()
    generation = get_competition_controls()["generation"]

    def submit(team_id):
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["team_id"] = team_id
                session["generation"] = generation
            entered = client.post('/api/activity', json={
                'event_type': 'fullscreen_enter', 'event_id': f'enter-{team_id}',
            })
            assert entered.status_code == 200
            payload = dict(question_id="Q01", request_id="same-request-per-team",
                           error_location="3", error_type="Logical Error", expected_output="10",
                           correction="for i in range(len(numbers)):")
            first = client.post('/api/submit-bug-fix', json=payload)
            retry = client.post('/api/submit-bug-fix', json=payload)
            return first.status_code, first.get_json(), retry.get_json()

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(submit, teams))
    assert all(status == 200 and first == retry for status, first, retry in responses)
    conn = get_db_connection()
    assert conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 20
    assert conn.execute('SELECT COUNT(*) FROM scores WHERE completed_count = 1 AND score > 0').fetchone()[0] == 20
    conn.close()


def test_phase1_activity_migration_preserves_records_without_duplication():
    init_db(force_reset=True)
    team_id, _ = register_team('Legacy activity', 'One', 'Two')
    conn = get_db_connection()
    conn.execute("INSERT INTO activity_events (team_id, event_type) VALUES (?, 'focus_lost')", (team_id,))
    conn.commit()
    conn.close()
    init_db()
    init_db()
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) FROM team_activity WHERE team_id = ? AND event_type = 'window_blur'", (team_id,)).fetchone()[0] == 1
    conn.close()
    assert reset_event_data('RESET EVENT')[0]
    init_db()
    conn = get_db_connection()
    assert conn.execute('SELECT COUNT(*) FROM team_activity').fetchone()[0] == 0
    conn.close()


def test_reset_does_not_let_old_session_access_reused_team_id():
    init_db(force_reset=True)
    with app.test_client() as client:
        client.post('/register', data={'name': 'Before reset', 'member1': 'One', 'member2': 'Two'})
        with client.session_transaction() as session:
            old_team = session['team_id']
        assert reset_event_data('RESET EVENT')[0]
        new_team, _ = register_team('After reset', 'Three', 'Four')
        assert new_team == old_team
        assert client.get('/api/team-progress').status_code == 403
        result = client.get('/result')
        assert b'After reset' not in result.data
