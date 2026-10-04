"""Shared browser tabs keep participant and organizer authentication separate."""
import pytest

from app import app
from config import Config
from database import init_db
from event_manager import reset_event_data, start_event


@pytest.fixture
def browser():
    init_db(force_reset=True)
    app.config["TESTING"] = True
    return app.test_client()


def organizer_login(browser):
    response = browser.post("/admin/login", data={
        "username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD,
    })
    assert response.status_code == 302
    assert response.location.endswith("/admin/dashboard")
    cookie = browser.get_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"])
    assert cookie is not None and cookie.http_only and cookie.same_site == "Lax"


def participant_login(browser):
    response = browser.post("/register", data={
        "name": "Same browser team", "member1": "Ada", "member2": "Grace",
    })
    assert response.status_code == 302
    with browser.session_transaction() as participant:
        return participant["team_id"]


def assert_organizer_active(browser):
    dashboard = browser.get("/admin/dashboard")
    assert dashboard.status_code == 200
    assert b'id="admin-workspace"' in dashboard.data
    assert b'id="participant-gate"' not in dashboard.data
    assert browser.get("/api/admin/dashboard-data").status_code == 200
    assert browser.get("/api/admin/event-status").status_code == 200
    assert browser.get("/api/admin/leaderboard-data").status_code == 200


def test_participant_registration_login_and_logout_do_not_sign_out_organizer(browser):
    organizer_login(browser)
    admin_cookie = browser.get_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"]).value
    participant_login(browser)
    assert_organizer_active(browser)
    assert browser.get("/logout").status_code == 302
    assert_organizer_active(browser)
    assert browser.post("/register", data={
        "action": "login", "team_lookup": "Same browser team",
    }).status_code == 302
    assert_organizer_active(browser)
    assert browser.get_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"]).value == admin_cookie


def test_organizer_login_and_explicit_logout_preserve_participant_session(browser):
    team_id = participant_login(browser)
    organizer_login(browser)
    assert browser.get("/api/team-progress").status_code == 200
    with browser.session_transaction() as participant:
        assert participant["team_id"] == team_id
        assert "is_admin" not in participant
    assert browser.get("/admin/logout").status_code == 302
    assert browser.get("/admin/dashboard").status_code == 302
    assert browser.get("/api/admin/dashboard-data").status_code == 302
    assert browser.get("/api/team-progress").status_code == 200
    assert browser.get("/api/admin/event-status").status_code == 302
    assert browser.get("/api/admin/leaderboard-data").status_code == 302
    assert browser.get_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"]) is None


def test_old_participant_response_cannot_overwrite_admin_cookie(browser):
    participant_login(browser)
    stale_cookie = browser.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
    organizer_login(browser)
    browser.get("/logout")
    # A delayed response from another tab may restore an older participant
    # cookie, but its cookie name cannot replace organizer authentication.
    browser.set_cookie(app.config["SESSION_COOKIE_NAME"], stale_cookie)
    assert_organizer_active(browser)


def test_event_reset_and_stale_team_session_do_not_sign_out_organizer(browser):
    participant_login(browser)
    organizer_login(browser)
    assert reset_event_data("RESET EVENT")[0]
    assert browser.get("/api/team-progress").status_code == 403
    assert_organizer_active(browser)


def test_participant_focus_violations_never_affect_organizer_access(browser):
    participant_login(browser)
    organizer_login(browser)
    assert start_event()[0]
    for index in (1, 2):
        entry = f"entry-{index}"
        assert browser.post("/api/activity", json={
            "event_type": "fullscreen_enter", "event_id": entry,
        }).status_code == 200
        state = browser.post("/api/activity", json={
            "event_type": "window_blur", "event_id": f"blur-{index}",
            "activation_id": entry,
        }).get_json()
        assert state["violations"] == index
        assert_organizer_active(browser)
    assert state["blocked"]


def test_participant_cookie_cannot_be_reused_to_authorize_admin(browser):
    participant_login(browser)
    # Even a signed cookie from the old shared-session format is not accepted
    # as organizer authentication: the signatures are bound to separate roles.
    with browser.session_transaction() as participant:
        participant["is_admin"] = True
    cookie = browser.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
    browser.set_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"], cookie)
    assert browser.get("/api/admin/dashboard-data").status_code == 302
    assert browser.post("/api/admin/event-action", json={"action": "start"}).status_code == 302


def test_organizer_cookie_cannot_be_reused_as_participant_session(browser):
    organizer_login(browser)
    cookie = browser.get_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"]).value
    browser.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie)
    assert browser.get("/api/team-progress").status_code == 401
    assert_organizer_active(browser)


def test_unsigned_or_tampered_admin_cookie_is_rejected(browser):
    browser.set_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"], '{"is_admin": true}')
    assert browser.get("/api/admin/dashboard-data").status_code == 302
    organizer_login(browser)
    cookie = browser.get_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"]).value
    payload, timestamp, signature = cookie.rsplit(".", 2)
    signature = ("a" if signature[0] != "a" else "b") + signature[1:]
    browser.set_cookie(app.config["ADMIN_SESSION_COOKIE_NAME"], ".".join((payload, timestamp, signature)))
    assert browser.get("/api/admin/dashboard-data").status_code == 302
