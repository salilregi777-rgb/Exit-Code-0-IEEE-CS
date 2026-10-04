"""Organizer restoration preserves work and starts a fresh enforcement interval."""
import pytest
pytestmark = pytest.mark.usefixtures("ordered_bank")

from app import app
from database import get_db_connection, init_db
from event_manager import start_event


@pytest.fixture
def team_clients():
    init_db(force_reset=True)
    app.config['TESTING'] = True
    participant = app.test_client()
    organizer = app.test_client()
    assert participant.post('/register', data={
        'name': 'Restore team', 'member1': 'Ada', 'member2': 'Grace'
    }).status_code == 302
    with participant.session_transaction() as sess:
        team_id = sess['team_id']
    with organizer.session_transaction() as sess:
        sess['is_admin'] = True
    start_event()
    return participant, organizer, team_id


def signal(client, event_type, event_id, activation_id=None, document_id='team-page'):
    response = client.post('/api/activity', json={
        'event_type': event_type, 'event_id': event_id,
        'activation_id': activation_id, 'document_id': document_id,
        'document_started_at': 100,
    })
    assert response.status_code == 200
    return response.get_json()


def block(client, prefix='old'):
    for index in (1, 2):
        activation = f'{prefix}-entry-{index}'
        signal(client, 'fullscreen_enter', activation)
        state = signal(client, 'window_blur', f'{prefix}-departure-{index}', activation)
    assert state['blocked'] and state['violations'] == 2


def test_unban_requires_organizer_and_valid_existing_team(team_clients):
    participant, organizer, team_id = team_clients
    assert participant.post('/api/admin/unban-team', json={'team_id': team_id}).status_code == 302
    assert organizer.post('/api/admin/unban-team', json={}).status_code == 400
    assert organizer.post('/api/admin/unban-team', json={'team_id': ['bad']}).status_code == 400
    assert organizer.post('/api/admin/unban-team', json={'team_id': 'missing'}).status_code == 404


def test_unban_preserves_work_and_audit_and_rejects_old_departures(team_clients):
    participant, organizer, team_id = team_clients
    signal(participant, 'fullscreen_enter', 'answer-entry')
    result = participant.post('/api/submit-bug-fix', json={
        'question_id': 'Q01', 'error_location': '3', 'error_type': 'Logical Error',
        'expected_output': '10', 'cause': 'Loop stops before the last element.',
        'correction': 'Use range(len(numbers)) instead.', 'request_id': 'saved-answer',
    })
    assert result.status_code == 200
    block(participant)
    conn = get_db_connection()
    before = {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table} WHERE team_id = ?', (team_id,))]
              for table in ('submissions', 'scores', 'question_assignments', 'fullscreen_events', 'team_activity')}
    conn.execute("""INSERT INTO fullscreen_sessions(id, team_id, is_fullscreen, active_event_id, document_id)
                 VALUES ('old-login', ?, 1, 'offline-entry', 'offline-page')""", (team_id,))
    conn.commit()
    conn.close()

    restored = organizer.post('/api/admin/unban-team', json={'team_id': team_id}).get_json()
    assert restored['restored'] and restored['is_active'] and restored['violations'] == 0
    assert not restored['blocked']
    state = participant.get('/api/fullscreen/status').get_json()
    assert not state['blocked'] and not state['is_fullscreen'] and state['violations'] == 0
    conn = get_db_connection()
    for table, expected in before.items():
        assert [dict(row) for row in conn.execute(f'SELECT * FROM {table} WHERE team_id = ?', (team_id,))] == expected
    assert conn.execute('SELECT COUNT(*) FROM fullscreen_sessions WHERE team_id = ? AND (is_fullscreen != 0 OR active_event_id IS NOT NULL OR document_id IS NOT NULL)', (team_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM admin_actions WHERE action = 'UNBAN_TEAM'").fetchone()[0] == 1
    conn.close()

    # Old accepted enters remain deduplicated, and every old login is invalidated.
    assert not signal(participant, 'fullscreen_enter', 'old-entry-1')['is_fullscreen']
    assert signal(participant, 'window_blur', 'delayed-old', 'offline-entry', 'offline-page')['violations'] == 0
    assert signal(participant, 'tab_hidden', 'delayed-current', 'old-entry-2')['violations'] == 0
    signal(participant, 'fullscreen_enter', 'fresh-entry-1')
    assert signal(participant, 'window_blur', 'stale-after-new', 'offline-entry', 'offline-page')['is_fullscreen']
    assert signal(participant, 'window_blur', 'fresh-departure-1', 'fresh-entry-1')['violations'] == 1
    # A retried successful request does not erase a new warning or disable the team.
    retry = organizer.post('/api/admin/unban-team', json={'team_id': team_id}).get_json()
    assert not retry['restored'] and retry['violations'] == 1 and retry['is_active']
    signal(participant, 'fullscreen_enter', 'fresh-entry-2')
    assert signal(participant, 'tab_hidden', 'fresh-departure-2', 'fresh-entry-2')['blocked']


def test_unban_during_status_poll_does_not_clear_participant_session(team_clients, monkeypatch):
    import importlib
    module = importlib.import_module('app')
    participant, organizer, team_id = team_clients
    block(participant)
    original = module.guard_snapshot
    restored = False

    def restore_before_snapshot(current_team):
        nonlocal restored
        if not restored:
            restored = True
            conn = get_db_connection()
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE teams SET is_active=1 WHERE id=?', (current_team,))
            conn.execute('UPDATE participant_security SET blocked=0, violations=0 WHERE team_id=?', (current_team,))
            conn.commit()
            conn.close()
        return original(current_team)

    monkeypatch.setattr(module, 'guard_snapshot', restore_before_snapshot)
    response = participant.get('/api/fullscreen/status')
    assert response.status_code == 200
    assert response.get_json()['team_active'] and not response.get_json()['blocked']
    with participant.session_transaction() as sess:
        assert sess['team_id'] == team_id


def test_unban_available_after_publication_and_visible_in_live_dashboard(team_clients):
    participant, organizer, team_id = team_clients
    block(participant)
    dashboard = organizer.get('/api/admin/dashboard-data').get_json()
    team = next(team for team in dashboard['security'] if team['team_id'] == team_id)
    assert team['blocked'] and not team['is_active']
    html = organizer.get('/admin/dashboard').data
    assert f'data-team-unban="{team_id}"'.encode() in html and b'Unban team' in html
    assert b'js/blocked.js' in participant.get('/blocked').data
    conn = get_db_connection()
    conn.execute('UPDATE competition_controls SET results_published = 1 WHERE id = 1')
    conn.commit()
    conn.close()
    assert organizer.post('/api/admin/unban-team', json={'team_id': team_id}).status_code == 200
    dashboard = organizer.get('/api/admin/dashboard-data').get_json()
    team = next(team for team in dashboard['security'] if team['team_id'] == team_id)
    assert not team['blocked'] and team['violations'] == 0 and team['is_active']
    assert dashboard['results_published']


def test_repeated_unban_does_not_release_current_fullscreen(team_clients):
    participant, organizer, team_id = team_clients
    block(participant)
    assert organizer.post('/api/admin/unban-team', json={'team_id': team_id}).get_json()['restored']
    signal(participant, 'fullscreen_enter', 'restored-entry')
    assert not organizer.post('/api/admin/unban-team', json={'team_id': team_id}).get_json()['restored']
    state = participant.get('/api/fullscreen/status').get_json()
    assert state['is_fullscreen'] and state['activation_id'] == 'restored-entry'
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) FROM admin_actions WHERE action = 'UNBAN_TEAM'").fetchone()[0] == 1
    conn.close()
