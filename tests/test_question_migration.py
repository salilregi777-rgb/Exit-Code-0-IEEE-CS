"""Question updates must preserve completed work and private future prompts."""
import json

from database import (init_db, register_team, get_db_connection, get_client_question,
                      get_team_assigned_questions, QUESTION_BANK_VERSION)
from event_manager import start_event
from scoring import process_submission


def test_migration_preserves_old_answer_source_scores_and_live_order():
    init_db(force_reset=True)
    team, error = register_team('Historical source', 'One', 'Two')
    assert error is None
    start_event()
    before = get_team_assigned_questions(team)
    qid = before[0]['id']
    conn = get_db_connection()
    conn.execute("UPDATE questions SET title='Old prompt', task='Old intended behavior', code='old source' WHERE id=?", (qid,))
    conn.commit()
    conn.close()
    result, error = process_submission(team, qid, {'error_type': 'Logical Error', 'correction': 'my old answer'})
    assert error is None
    conn = get_db_connection()
    # Simulate a pre-snapshot submission before the new bank migration.
    saved = json.loads(conn.execute('SELECT response_json FROM submissions WHERE team_id=?', (team,)).fetchone()[0])
    saved.pop('question_snapshot')
    conn.execute('UPDATE submissions SET response_json=? WHERE team_id=?', (json.dumps(saved), team))
    conn.execute('DELETE FROM data_migrations WHERE name=?', (QUESTION_BANK_VERSION,))
    conn.commit()
    conn.close()
    init_db()
    review = get_client_question(team, qid)
    assert (review['title'], review['task'], review['code']) == ('Old prompt', 'Old intended behavior', 'old source')
    assert review['awarded_score'] == result['total_score']
    assert review['submission']['correction'] == 'my old answer'
    assert [q['id'] for q in get_team_assigned_questions(team)] == [q['id'] for q in before]
    conn = get_db_connection()
    assert conn.execute('SELECT code FROM questions WHERE id=?', (qid,)).fetchone()[0] != 'old source'
    conn.close()
    init_db()
    assert get_client_question(team, qid) == review


def test_tasks_are_public_only_for_unlocked_questions_and_snapshots_are_sanitized():
    init_db(force_reset=True)
    team, _ = register_team('Private prompts', 'One', 'Two')
    questions = get_team_assigned_questions(team)
    first = get_client_question(team, questions[0]['id'])
    assert first['task'] and first['code']
    assert get_client_question(team, questions[1]['id']) is None
    for question in questions:
        assert not {'title', 'task', 'code', 'language', 'correction', 'expected_output'} & question.keys()
    assert not {'bug_location', 'correction', 'expected_output', 'hint', 'cause'} & first.keys()
    result, error = process_submission(team, first['id'], {'correction': 'my answer'})
    assert error is None
    assert set(result['question_snapshot']) == {'language', 'title', 'task', 'code', 'points', 'difficulty'}
    conn = get_db_connection()
    conn.execute("UPDATE questions SET code='changed source', title='new title' WHERE id=?", (first['id'],))
    conn.commit()
    conn.close()
    assert get_client_question(team, first['id'])['code'] == first['code']
