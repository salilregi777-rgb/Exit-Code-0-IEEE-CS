"""Persistent participant fullscreen enforcement and post-quiz feedback."""
import json
import uuid
from flask import session
from database import get_db_connection, record_activity

VIOLATION_LIMIT = 2
DEPARTURE_EVENTS = {"fullscreen_exit", "window_blur", "tab_hidden"}
FEEDBACK_QUESTIONS = [
    {"id": "clarity", "prompt": "How clear were the quiz questions?"},
    {"id": "difficulty", "prompt": "How suitable was the difficulty for your experience?"},
    {"id": "interface", "prompt": "How easy was the quiz interface to use?"},
    {"id": "pacing", "prompt": "How suitable was the time allowed for each question?"},
    {"id": "enjoyment", "prompt": "How much did you enjoy the quiz?"},
    {"id": "overall", "prompt": "How would you rate the quiz overall?"},
]


def _guard_required(conn):
    """Read the event state inside the transaction changing guard access."""
    event = conn.execute("SELECT event_status FROM event_state WHERE id = 1").fetchone()
    return bool(event and event["event_status"] != "WAITING")


def _release_waiting_intervals(conn, team_id):
    # An existing browser may still be armed by an earlier version of the site.
    # None of those pre-event intervals can carry into the debugging round.
    conn.execute("UPDATE fullscreen_sessions SET is_fullscreen = 0, active_event_id = NULL, document_id = NULL, document_started_at = 0 WHERE team_id = ?", (team_id,))


def guard_snapshot(team_id):
    guard_id = session.get("guard_id")
    if not guard_id:
        guard_id = session["guard_id"] = uuid.uuid4().hex
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO participant_security(team_id) VALUES (?)", (team_id,))
        conn.execute("INSERT OR IGNORE INTO fullscreen_sessions(id, team_id) VALUES (?, ?)", (guard_id, team_id))
        required = _guard_required(conn)
        if not required:
            _release_waiting_intervals(conn, team_id)
        state = dict(conn.execute("SELECT s.violations, s.blocked, t.is_active AS team_active FROM participant_security s JOIN teams t ON t.id = s.team_id WHERE s.team_id = ?", (team_id,)).fetchone())
        row = conn.execute("SELECT is_fullscreen, active_event_id, document_id FROM fullscreen_sessions WHERE id = ? AND team_id = ?", (guard_id, team_id)).fetchone()
        conn.commit()
        return {**state, "required": required, "blocked": bool(state["blocked"]), "team_active": bool(state["team_active"]), "is_fullscreen": bool(row and row[0]), "limit": VIOLATION_LIMIT, "activation_id": row["active_event_id"] if row else None}
    finally:
        conn.close()


def fullscreen_signal(team_id, event_type, event_id, activation_id=None, document_id=None, document_started_at=0):
    """Count one departure per acknowledged, focused fullscreen interval.

    A switch can emit blur, visibilitychange and fullscreenchange in any order.
    Clearing is_fullscreen inside the same transaction deduplicates those signals;
    activation/document IDs keep delayed old-page reports from closing a new interval.
    """
    guard_snapshot(team_id)
    conn = get_db_connection()
    counted = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        required = _guard_required(conn)
        if not required:
            _release_waiting_intervals(conn, team_id)
        state = conn.execute("SELECT * FROM participant_security WHERE team_id = ?", (team_id,)).fetchone()
        current = conn.execute("SELECT * FROM fullscreen_sessions WHERE id = ? AND team_id = ?", (session["guard_id"], team_id)).fetchone()
        # Retain an old signed-in interval long enough to reconcile a queued
        # departure after logout/login. It cannot grant access to the new cookie.
        if event_type in DEPARTURE_EVENTS and activation_id and document_id:
            previous = conn.execute("SELECT * FROM fullscreen_sessions WHERE team_id = ? AND active_event_id = ? AND document_id = ?", (team_id, activation_id, document_id)).fetchone()
            if previous:
                current = previous
        duplicate = conn.execute("SELECT 1 FROM fullscreen_events WHERE team_id = ? AND event_id = ?", (team_id, event_id)).fetchone()
        recorded = not duplicate and not state["blocked"]
        if recorded:
            # Remember ignored pre-event IDs too: a network retry arriving after
            # the organizer starts must not activate an old fullscreen interval.
            conn.execute("INSERT INTO fullscreen_events(team_id, event_id, event_type) VALUES (?, ?, ?)", (team_id, event_id, event_type))
        if recorded and required:
            matching = (not activation_id or activation_id == current["active_event_id"]) and (not document_id or document_id == current["document_id"])
            if event_type == "fullscreen_enter":
                already_released = conn.execute("SELECT 1 FROM fullscreen_events WHERE team_id = ? AND event_id = ?", (team_id, "release-" + event_id)).fetchone()
                newer_document = document_started_at and current["document_started_at"] > document_started_at
                if not already_released and not newer_document:
                    conn.execute("UPDATE fullscreen_sessions SET is_fullscreen = 1, active_event_id = ?, document_id = ?, document_started_at = ? WHERE id = ?", (event_id, document_id, document_started_at, session["guard_id"]))
            elif matching:
                conn.execute("UPDATE fullscreen_sessions SET is_fullscreen = 0 WHERE id = ?", (current["id"],))
                counted = event_type in DEPARTURE_EVENTS and bool(current["is_fullscreen"])
            if counted:
                conn.execute("UPDATE participant_security SET violations = MIN(violations + 1, ?) WHERE team_id = ?", (VIOLATION_LIMIT, team_id))
                conn.execute("UPDATE participant_security SET blocked = 1 WHERE team_id = ? AND violations >= ?", (team_id, VIOLATION_LIMIT))
                blocked = conn.execute("SELECT blocked FROM participant_security WHERE team_id = ?", (team_id,)).fetchone()[0]
                if blocked:
                    conn.execute("UPDATE teams SET is_active = 0 WHERE id = ?", (team_id,))
                    conn.execute("UPDATE fullscreen_sessions SET is_fullscreen = 0 WHERE team_id = ?", (team_id,))
                record_activity(team_id, "session_violation", cur=conn.cursor())
            if event_type != "fullscreen_leave":
                record_activity(team_id, event_type, cur=conn.cursor())
        conn.commit()
    finally:
        conn.close()
    return {**guard_snapshot(team_id), "recorded": recorded, "counted": counted}


def feedback_snapshot(team_id):
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT ratings_json, note FROM quiz_feedback WHERE team_id = ?", (team_id,)).fetchone()
        return {"submitted": bool(row), "ratings": json.loads(row[0]) if row else {}, "note": row[1] if row else ""}
    finally:
        conn.close()


def save_feedback(team_id, data):
    ratings, note = data.get("ratings"), data.get("note", "")
    if not isinstance(ratings, dict) or set(ratings) != {q["id"] for q in FEEDBACK_QUESTIONS}:
        return None, "Please rate all six questions."
    if any(type(value) is not int or not 1 <= value <= 5 for value in ratings.values()):
        return None, "Each rating must be a whole number from 1 to 5."
    if not isinstance(note, str) or len(note) > 2000:
        return None, "Keep your feedback note within 2,000 characters."
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        completed = conn.execute("SELECT completed_at FROM quiz_sessions WHERE team_id = ?", (team_id,)).fetchone()
        if not completed or not completed[0]:
            return None, "Complete the quiz before sending feedback."
        # An identical network retry is safe; a later request cannot replace a submitted form.
        conn.execute("INSERT OR IGNORE INTO quiz_feedback(team_id, ratings_json, note) VALUES (?, ?, ?)", (team_id, json.dumps(ratings), note.strip()))
        conn.commit()
    finally:
        conn.close()
    return feedback_snapshot(team_id), None
