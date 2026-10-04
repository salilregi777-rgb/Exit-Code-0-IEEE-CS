"""Organizer feedback export keeps ratings and participant notes intact."""
import csv
import io
import json
import pytest

from app import app
from database import init_db, get_db_connection, register_team


@pytest.fixture
def client():
    init_db(force_reset=True)
    app.config["TESTING"] = True
    return app.test_client()


def test_feedback_export_requires_organizer_and_empty_export_has_header(client):
    assert client.get("/admin/export/feedback.csv").status_code == 302
    with client.session_transaction(path='/admin') as session:
        session["is_admin"] = True
    response = client.get("/admin/export/feedback.csv")
    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "participant_feedback.csv" in response.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert rows == [["Team ID", "Team Name", "Clarity", "Difficulty", "Interface",
                     "Pacing", "Enjoyment", "Overall", "Feedback Note", "Submitted At"]]
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "/admin/export/feedback.csv" in html
    assert "View website" not in html and ">Root cause<" not in html


def test_feedback_export_preserves_all_ratings_notes_and_blocked_teams(client):
    team_id, error = register_team("=FORMULA()", "Ada", "Grace")
    assert error is None
    ratings = dict(zip(("clarity", "difficulty", "interface", "pacing", "enjoyment", "overall"), (1, 2, 3, 4, 5, 4)))
    note = '=SUM(1,2)\nA note with "quotes", commas, and café'
    conn = get_db_connection()
    conn.execute("INSERT INTO quiz_feedback(team_id, ratings_json, note) VALUES (?, ?, ?)",
                 (team_id, json.dumps(ratings), note))
    conn.execute("UPDATE teams SET is_active = 0 WHERE id = ?", (team_id,))
    conn.commit()
    conn.close()
    with client.session_transaction(path='/admin') as session:
        session["is_admin"] = True
    response = client.get("/admin/export/feedback.csv")
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert len(rows) == 1
    assert rows[0]["Team ID"] == team_id and rows[0]["Team Name"] == "'=FORMULA()"
    assert [rows[0][column] for column in ("Clarity", "Difficulty", "Interface", "Pacing", "Enjoyment", "Overall")] == ["1", "2", "3", "4", "5", "4"]
    assert rows[0]["Feedback Note"] == "'" + note
    assert rows[0]["Submitted At"]
    assert client.get("/admin/dashboard").status_code == 200
