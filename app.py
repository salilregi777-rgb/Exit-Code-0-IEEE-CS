import os
import io
import csv
import math
from urllib.parse import urlsplit
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response

from config import Config
from role_sessions import RoleSessionInterface
from database import (
    init_db, get_db_connection, register_team,
    log_admin_action, get_team_assigned_questions, get_client_question,
    record_activity, get_competition_controls, recalculate_team_score, get_team_score
)
from scoring import (
    process_submission, activate_rubber_duck, activate_git_revert,
    arm_double_commit, submission_score_limit
)
from event_manager import (
    get_event_state, start_event, pause_event, resume_event,
    end_event, reset_event_data
)

from participant_policy import guard_snapshot, fullscreen_signal, feedback_snapshot, save_feedback, FEEDBACK_QUESTIONS

from quiz import quiz_snapshot, start_quiz, answer_quiz, QUESTIONS as QUIZ_QUESTIONS, QUIZ_MINUTES

app = Flask(__name__)
app.config.update(SECRET_KEY=Config.SECRET_KEY, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=64 * 1024,
                  ADMIN_SESSION_COOKIE_NAME="exitcode_admin_session")
app.session_interface = RoleSessionInterface()

# Initialize database schema and seeds
init_db()

# --- Security Decorators & Context Processors ---

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return decorated_function

def team_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        team_id = session.get("team_id")
        if not team_id:
            if request.path.startswith("/api/"):
                return jsonify(success=False, error="Sign in to your team to continue."), 401
            return redirect(url_for("register", next=request.path))
        conn = get_db_connection()
        team = conn.execute("SELECT is_active FROM teams WHERE id = ?", (team_id,)).fetchone()
        generation = conn.execute("SELECT generation FROM competition_controls WHERE id = 1").fetchone()[0]
        conn.close()
        security = guard_snapshot(team_id) if team else None
        blocked_status_check = request.path == "/api/fullscreen/status" and security and security["blocked"]
        if security and security["blocked"] and not blocked_status_check:
            if request.path.startswith("/api/"):
                return jsonify(success=False, blocked=True, error="Account blocked after two fullscreen or focus violations. Contact an organizer.", **{k: security[k] for k in ("violations", "limit")}), 403
            return redirect(url_for("participant_blocked"))
        # Active and blocked must come from the same transactional snapshot. An
        # unban between the first lookup and guard_snapshot can change both.
        if not team or (not security["team_active"] and not blocked_status_check) or session.get("generation", generation) != generation:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify(success=False, error="Your team session is no longer active. Contact an organizer."), 403
            return redirect(url_for("register"))
        protected = request.path == "/api/submit-bug-fix" or request.path.startswith("/api/powerup/") or request.path in ("/api/quiz/start", "/api/quiz/answer", "/api/quiz/feedback")
        if protected and security["required"] and not security["is_fullscreen"]:
            return jsonify(success=False, fullscreen_required=True, error="Enter fullscreen to continue."), 403
        return f(*args, **kwargs)
    return decorated_function


@app.before_request
def validate_request():
    team_id = session.get("team_id")
    if team_id and not request.path.startswith(("/static/", "/admin", "/api/admin")) and request.path not in ("/logout", "/blocked", "/register", "/api/fullscreen/status"):
        conn = get_db_connection()
        blocked = conn.execute("SELECT blocked FROM participant_security WHERE team_id = ?", (team_id,)).fetchone()
        conn.close()
        if blocked and blocked[0]:
            if request.path.startswith("/api/"):
                return jsonify(success=False, blocked=True, violations=2, limit=2, error="Account blocked after two fullscreen or focus violations. Contact an organizer."), 403
            return redirect(url_for("participant_blocked"))
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and urlsplit(origin).netloc != request.host):
            return jsonify(success=False, error="This request must come from the competition site."), 403
        if request.path.startswith("/api/"):
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(success=False, error="Please send a valid JSON object."), 400


@app.after_request
def secure_response(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if request.path.startswith("/api/") or session.get("team_id") or session.get("is_admin"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.context_processor
def inject_global_data():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as count FROM teams WHERE is_active = 1")
    teams_count = cur.fetchone()["count"]
    conn.close()
    
    ev_state = get_event_state()
    cfg = Config.load_event_config()
    return {
        "event_state": ev_state,
        "connected_teams": teams_count,
        "config": cfg,
        "results_published": bool(get_competition_controls()["results_published"])
    }

# --- Error Handlers ---

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify(success=False, error="This endpoint was not found."), 404
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    app.logger.error(f"Internal Server Error: {str(e)}")
    if request.path.startswith("/api/"):
        return jsonify(success=False, error="The server could not complete this request. Please try again."), 500
    return render_template("500.html"), 500

# --- Public & Participant Views ---

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        action = request.form.get("action", "register")

        if action == "login":
            # Existing team sign in
            lookup = request.form.get("team_lookup", "").strip()
            if not lookup:
                return render_template("register.html", error="Please enter your Team ID or registered Team Name.")

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM teams 
                WHERE (UPPER(id) = UPPER(?) OR UPPER(name) = UPPER(?))
            """, (lookup, lookup))
            team = cur.fetchone()
            conn.close()

            if team and not team["is_active"]:
                return render_template("register.html", error="This account is blocked or disabled. Contact an organizer before signing in again."), 403
            if not team:
                return render_template("register.html", error="Team not found. Please verify your Team ID or register your team.")

            session.clear()
            session["team_id"] = team["id"]
            session["team_name"] = team["name"]
            session["generation"] = get_competition_controls()["generation"]

            st = get_event_state()
            if st["event_status"] == "LIVE":
                return redirect(url_for("arena"))
            elif st["event_status"] == "COMPLETED":
                return redirect(url_for("result"))
            return redirect(url_for("waiting"))

        # Registration Flow: Exactly 2 to 3 members
        if get_event_state()["event_status"] != "WAITING" or get_competition_controls()["results_published"]:
            return render_template("register.html", error="Registration is closed because the event has started. Registered teams can still sign in."), 403
        name = request.form.get("name", "").strip()
        member1 = request.form.get("member1", "").strip()
        member2 = request.form.get("member2", "").strip()
        member3 = request.form.get("member3", "").strip()
        email = request.form.get("email", "").strip()

        if not name:
            return render_template("register.html", error="Team Name is strictly required.")
        if not member1 or not member2:
            return render_template("register.html", error="Member 1 and Member 2 are required (teams must be 2 to 3 members).")

        team_id, err = register_team(name, member1, member2, member3, email)
        if err:
            closed = get_event_state()["event_status"] != "WAITING" or get_competition_controls()["results_published"]
            return render_template("register.html", error=err), 403 if closed else 200

        session.clear()
        session["team_id"] = team_id
        session["team_name"] = name
        session["generation"] = get_competition_controls()["generation"]
        session["just_registered"] = True

        st = get_event_state()
        if st["event_status"] == "LIVE":
            return redirect(url_for("arena"))
        return redirect(url_for("waiting"))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    return redirect(url_for("register"))

@app.route("/logout")
def logout():
    # Intervals hold only enforcement tokens; keep them to reconcile an offline
    # departure after a later sign-in. The signed-in cookie is still cleared.
    session.clear()
    return redirect(url_for("index"))

@app.route("/waiting")
@app.route("/waiting-room")
@team_required
def waiting():
    team_id = session.get("team_id")
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
    team = cur.fetchone()
    
    cur.execute("SELECT * FROM participants WHERE team_id = ? ORDER BY id ASC", (team_id,))
    members = cur.fetchall()

    cur.execute("SELECT COUNT(*) as count FROM teams WHERE is_active = 1")
    teams_count = cur.fetchone()["count"]
    conn.close()

    st = get_event_state()
    if st["event_status"] == "LIVE":
        return redirect(url_for("arena"))
    elif st["event_status"] == "COMPLETED":
        return redirect(url_for("result"))

    return render_template("waiting.html", team=team, members=members, teams_count=teams_count,
                           just_registered=session.pop("just_registered", False), selected_language="Mixed language")

@app.route("/arena")
@app.route("/debug-arena")
@team_required
def arena():
    team_id = session.get("team_id")
    st = get_event_state()

    # Route gate: if event is waiting or completed
    if st["event_status"] == "WAITING":
        return redirect(url_for("waiting"))
    elif st["event_status"] == "COMPLETED":
        return redirect(url_for("result"))

    conn = get_db_connection()
    cur = conn.cursor()

    # Team & Score details
    cur.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
    team = cur.fetchone()

    cur.execute("SELECT * FROM scores WHERE team_id = ?", (team_id,))
    score = cur.fetchone()

    # Team assigned questions progression
    cur.execute("""
        SELECT q.id, q.language, q.title, q.difficulty, q.points,
               qa.question_order, qa.is_unlocked, qa.is_completed, qa.is_abandoned
        FROM question_assignments qa
        JOIN questions q ON qa.question_id = q.id
        WHERE qa.team_id = ? AND qa.is_abandoned = 0
        ORDER BY qa.question_order ASC
    """, (team_id,))
    assigned_questions = get_team_assigned_questions(team_id)

    # Select active question: by ?q=Qxx or first unlocked uncompleted
    req_qid = request.args.get("q")
    current_question = None

    if req_qid:
        for q in assigned_questions:
            if q["id"] == req_qid and q["is_unlocked"]:
                current_question = q
                break

    if not current_question:
        # Default to first unlocked uncompleted, or first unlocked
        uncompleted = [q for q in assigned_questions if q["is_unlocked"] and not q["is_completed"]]
        if uncompleted:
            current_question = uncompleted[0]
        else:
            unlocked = [q for q in assigned_questions if q["is_unlocked"]]
            current_question = unlocked[0] if unlocked else None

    # Fetch sanitized client code and details for current question
    client_q = None
    if current_question:
        cur.execute("SELECT id, language, title, difficulty, points, code FROM questions WHERE id = ?", (current_question["id"],))
        client_q = get_client_question(team_id, current_question["id"])

    # Fetch power-ups status
    cur.execute("SELECT * FROM powerups WHERE team_id = ?", (team_id,))
    powerups = {p["powerup_type"]: dict(p) for p in cur.fetchall()}

    conn.close()

    return render_template(
        "arena.html",
        team=team,
        score=score,
        assigned_questions=assigned_questions,
        current_question=current_question,
        client_q=client_q,
        powerups=powerups
    )

@app.route("/leaderboard")
def leaderboard():
    return render_template("leaderboard.html")

@app.route("/result")
@app.route("/final-result")
def result():
    team_id = session.get("team_id")
    controls = get_competition_controls()
    if team_id and session.get("generation", controls["generation"]) != controls["generation"]:
        session.clear()
        team_id = None
    conn = get_db_connection()
    cur = conn.cursor()

    team = None
    score = None
    tool_adjustments = []
    if team_id:
        cur.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
        team = cur.fetchone()
        cur.execute("SELECT * FROM scores WHERE team_id = ?", (team_id,))
        score = cur.fetchone()
        tool_adjustments = cur.execute("SELECT powerup_type, score_adjustment FROM powerups WHERE team_id = ? AND score_adjustment != 0 ORDER BY id", (team_id,)).fetchall()

    # Calculate Top 3 Winners (Ranked by Score DESC, Completed Count DESC, Time ASC)
    cur.execute("""
        SELECT 
            t.id, t.name,
            COALESCE(s.score, 0) as total_score,
            COALESCE(s.completed_count, 0) as completed_count,
            MIN(s.last_submission_time) as finish_time
        FROM teams t
        LEFT JOIN scores s ON t.id = s.team_id
        WHERE t.is_active = 1
        GROUP BY t.id
        ORDER BY total_score DESC, completed_count DESC, finish_time ASC
        LIMIT 3
    """)
    winners = cur.fetchall()

    conn.close()
    controls = get_competition_controls()
    published = bool(controls["results_published"])
    ranking = _leaderboard_rows()
    final_rank = next((i for i, row in enumerate(ranking, 1) if row["id"] == team_id), None) if published else None
    quiz = quiz_snapshot(team_id) if team else {"quiz_score": 0, "quiz_total": len(QUIZ_QUESTIONS)}
    assigned = get_team_assigned_questions(team_id) if team else []
    total_questions = len(assigned)
    debug_review = [get_client_question(team_id, question["id"]) for question in assigned if question["is_answered"]]
    debug_review = [question for question in debug_review if question]
    return render_template("result.html", team=team, score=score, winners=winners if published else [],
                           final_rank=final_rank, total_questions=total_questions, quiz=quiz, debug_review=debug_review, tool_adjustments=tool_adjustments,
                           quiz_score=quiz["quiz_score"], quiz_total=quiz["quiz_total"], results_published=published)

# --- JSON API Endpoints ---

@app.route("/api/event-status")
def api_event_status():
    st = get_event_state()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as count FROM teams WHERE is_active = 1")
    teams_count = cur.fetchone()["count"]
    conn.close()
    
    controls = get_competition_controls()
    st.update(connected_teams=teams_count, generation=controls["generation"],
              quiz_status=controls["quiz_status"], results_published=bool(controls["results_published"]))
    return jsonify(st)

@app.route("/api/question/<qid>")
@team_required
def api_get_question(qid):
    if get_event_state()["event_status"] not in ("LIVE", "PAUSED"):
        return jsonify(success=False, error="Questions are available during the debugging round."), 403
    team_id = session.get("team_id")
    q = get_client_question(team_id, qid)
    if not q:
        return jsonify({"success": False, "error": "Question not found or not unlocked for your team."}), 404
    return jsonify({"success": True, "question": q})

@app.route("/api/submit-bug-fix", methods=["POST"])
@team_required
def api_submit_bug_fix():
    team_id = session.get("team_id")
    st = get_event_state()

    # Reject submission if event is not live
    if st["event_status"] != "LIVE" or st["remaining_seconds"] <= 0:
        return jsonify({
            "success": False, 
            "error": "The competition is not currently active. Submissions are closed."
        }), 403

    data = request.get_json() or {}
    question_id = data.get("question_id")
    if not isinstance(question_id, str) or not question_id or len(question_id) > 40:
        return jsonify({"success": False, "error": "Question ID required."}), 400

    for field in ("error_location", "error_type", "expected_output", "correction"):
        if not isinstance(data.get(field, ""), str) or len(data.get(field, "")) > 6000:
            return jsonify(success=False, error="Response fields must be text, up to 6,000 characters each."), 400
    if "\n" in data.get("correction", "") or "\r" in data.get("correction", ""):
        return jsonify(success=False, error="Enter the corrected line as a single line of code."), 400
    data.pop("cause", None)
    request_id = data.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 80):
        return jsonify(success=False, error="Invalid submission request identifier."), 400
    result, err = process_submission(team_id, question_id, data, enforce_live=True)
    if err:
        return jsonify({"success": False, "error": err}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT score, completed_count FROM scores WHERE team_id = ?", (team_id,))
    sc = cur.fetchone()
    conn.close()

    return jsonify({
        "success": True,
        "result": result,
        "new_score": round(sc["score"], 1) if sc else 0.0,
        "completed_count": sc["completed_count"] if sc else 0
    })

@app.route("/api/powerup/rubber-duck", methods=["POST"])
@team_required
def api_powerup_rubber_duck():
    team_id = session.get("team_id")
    st = get_event_state()
    if st["event_status"] != "LIVE" or st["remaining_seconds"] <= 0:
        return jsonify({"success": False, "error": "Competition is not active."}), 403

    data = request.get_json() or {}
    question_id = data.get("question_id")
    if not isinstance(question_id, str) or not question_id or len(question_id) > 40:
        return jsonify({"success": False, "error": "Question ID required."}), 400

    success, msg, hint = activate_rubber_duck(team_id, question_id, enforce_live=True)
    if not success:
        return jsonify({"success": False, "error": msg}), 400

    return jsonify({"success": True, "message": msg, "hint": hint, "new_score": get_team_score(team_id)})

@app.route("/api/powerup/git-revert", methods=["POST"])
@team_required
def api_powerup_git_revert():
    team_id = session.get("team_id")
    st = get_event_state()
    if st["event_status"] != "LIVE" or st["remaining_seconds"] <= 0:
        return jsonify({"success": False, "error": "Competition is not active."}), 403

    data = request.get_json() or {}
    question_id = data.get("question_id")
    if not isinstance(question_id, str) or not question_id or len(question_id) > 40:
        return jsonify({"success": False, "error": "Question ID required."}), 400

    success, msg, new_qid = activate_git_revert(team_id, question_id, enforce_live=True)
    if not success:
        return jsonify({"success": False, "error": msg}), 400

    return jsonify({"success": True, "message": msg, "new_question_id": new_qid, "new_score": get_team_score(team_id)})

@app.route("/api/powerup/double-commit", methods=["POST"])
@team_required
def api_powerup_double_commit():
    team_id = session.get("team_id")
    st = get_event_state()
    if st["event_status"] != "LIVE" or st["remaining_seconds"] <= 0:
        return jsonify({"success": False, "error": "Competition is not active."}), 403

    data = request.get_json() or {}
    question_id = data.get("question_id")
    if not isinstance(question_id, str) or not question_id or len(question_id) > 40:
        return jsonify({"success": False, "error": "Question ID required."}), 400

    success, msg = arm_double_commit(team_id, question_id, enforce_live=True)
    if not success:
        return jsonify({"success": False, "error": msg}), 400

    return jsonify({"success": True, "message": msg, "new_score": get_team_score(team_id)})

def _leaderboard_rows():
    conn = get_db_connection()
    try:
        rows = [dict(r) for r in conn.execute("""
            SELECT t.id, t.name, COALESCE(s.score, 0) AS score,
                   COALESCE(s.completed_count, 0) AS completed_count,
                   s.last_submission_time AS finish_time, t.is_active
            FROM teams t LEFT JOIN scores s ON t.id = s.team_id
            WHERE t.is_active = 1
            ORDER BY score DESC, completed_count DESC, finish_time ASC, t.id ASC
        """).fetchall()]
        for rank, row in enumerate(rows, 1):
            row.update(rank=rank, debug_score=row["score"])
        return rows
    finally:
        conn.close()


@app.route("/api/leaderboard-data")
def api_leaderboard_data():
    state = get_event_state()
    published = bool(get_competition_controls()["results_published"])
    mode = "FINAL" if published else ("FROZEN" if state["event_status"] in ("PAUSED", "COMPLETED") else "LIVE")
    return jsonify(leaderboard=_leaderboard_rows(), current_team_id=session.get("team_id"),
                   event_status=state["event_status"], leaderboard_state=mode,
                   remaining_seconds=state["remaining_seconds"], results_published=published)

# --- Admin Portal Routes & Actions ---

@app.route("/api/admin/event-status")
@admin_required
def api_admin_event_status():
    return api_event_status()


@app.route("/api/admin/leaderboard-data")
@admin_required
def api_admin_leaderboard_data():
    return api_leaderboard_data()


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        pwd = request.form.get("password", "").strip()

        if user == Config.ADMIN_USERNAME and pwd == Config.ADMIN_PASSWORD:
            session.clear()
            session["is_admin"] = True
            log_admin_action("LOGIN", f"Admin logged in from {request.remote_addr}")
            return redirect(url_for("admin_dashboard"))

        return render_template("admin_login.html", error="Invalid admin security credentials.")

    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/admin")
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM teams")
    team_count = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) as c FROM submissions")
    submission_count = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) as c FROM questions WHERE is_active = 1")
    question_count = cur.fetchone()["c"]

    # All registered teams with member list & scores
    cur.execute("""
        SELECT t.id, t.name, t.created_at, t.is_active,
               COALESCE(ps.blocked, 0) AS blocked,
               COALESCE(ps.violations, 0) AS violations,
               COALESCE(s.score, 0) as score,
               COALESCE(s.completed_count, 0) as completed_count,
               (
                 SELECT GROUP_CONCAT(name, ', ') 
                 FROM participants 
                 WHERE team_id = t.id
               ) as members
        FROM teams t
        LEFT JOIN scores s ON t.id = s.team_id
        LEFT JOIN participant_security ps ON ps.team_id = t.id
        ORDER BY t.created_at DESC
    """)
    teams = cur.fetchall()

    # All 30 questions with answer keys
    cur.execute("SELECT * FROM questions ORDER BY id ASC")
    questions = cur.fetchall()

    # Recent submissions stream
    cur.execute("""
        SELECT s.*, t.name as team_name, q.title as question_title, q.points as points
        FROM submissions s
        JOIN teams t ON s.team_id = t.id
        JOIN questions q ON s.question_id = q.id
        ORDER BY s.id DESC
        LIMIT 50
    """)
    submissions = [{**dict(row), "max_score": submission_score_limit(row)} for row in cur.fetchall()]

    conn.close()
    return render_template(
        "admin_dashboard.html",
        team_count=team_count,
        submission_count=submission_count,
        question_count=question_count,
        teams=teams,
        questions=questions,
        submissions=submissions,
        quiz={"status": get_competition_controls()["quiz_status"], "question_count": len(QUIZ_QUESTIONS), "duration_minutes": QUIZ_MINUTES}
    )

@app.route("/api/admin/event-action", methods=["POST"])
@admin_required
def api_admin_event_action():
    action = (request.get_json() or {}).get("action")
    if action == "start":
        ok, msg = start_event()
    elif action == "pause":
        ok, msg = pause_event()
    elif action == "resume":
        ok, msg = resume_event()
    elif action == "end" or action == "end_event":
        ok, msg = end_event()
    else:
        return jsonify({"success": False, "error": f"Unknown event action: {action}"}), 400

    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "message": msg})

@app.route("/api/admin/override-score", methods=["POST"])
@admin_required
def api_admin_override_score():
    if get_competition_controls()["results_published"]:
        return jsonify(success=False, error="Results are final. Reset the event before changing competition data."), 409
    data = request.get_json() or {}
    sub_id = data.get("submission_id")
    new_score = data.get("new_score")
    reason = data.get("reason", "Admin manual scoring override")

    if type(sub_id) is not int or sub_id < 1 or new_score is None:
        return jsonify({"success": False, "error": "Submission ID and new score are required."}), 400

    try:
        new_sc = float(new_score)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid score value."}), 400
    if not math.isfinite(new_sc) or new_sc < 0:
        return jsonify(success=False, error="Score must be a finite non-negative number."), 400
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        return jsonify(success=False, error="Enter an override reason of up to 1,000 characters."), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    cur.execute("SELECT s.team_id, s.is_double_commit, s.response_json, q.points FROM submissions s JOIN questions q ON q.id = s.question_id WHERE s.id = ?", (sub_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Submission not found."}), 404

    if new_sc > submission_score_limit(row):
        conn.close()
        return jsonify(success=False, error="Score exceeds the maximum available for this submission."), 400
    team_id = row["team_id"]

    cur.execute("""
        UPDATE submissions 
        SET total_score = ?, override_reason = ? 
        WHERE id = ?
    """, (new_sc, reason, sub_id))

    recalculate_team_score(cur, team_id)

    conn.commit()
    conn.close()
    log_admin_action("OVERRIDE_SCORE", f"Set submission #{sub_id} score to {new_sc}. Reason: {reason}")
    return jsonify({"success": True, "message": f"Submission #{sub_id} updated to {new_sc} points."})

def _restore_team_access(conn, team_id):
    """Clear enforcement without deleting submitted work or the audit trail."""
    conn.execute("UPDATE teams SET is_active = 1 WHERE id = ?", (team_id,))
    conn.execute("UPDATE participant_security SET blocked = 0, violations = 0 WHERE team_id = ?", (team_id,))
    # Delayed departures from any old login must not affect a restored team.
    conn.execute("""UPDATE fullscreen_sessions SET is_fullscreen = 0,
                 active_event_id = NULL, document_id = NULL, document_started_at = 0
                 WHERE team_id = ?""", (team_id,))


@app.route("/api/admin/unban-team", methods=["POST"])
@admin_required
def api_admin_unban_team():
    team_id = (request.get_json() or {}).get("team_id")
    if not isinstance(team_id, str) or not team_id or len(team_id) > 40:
        return jsonify(success=False, error="Team ID required."), 400
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        team = conn.execute("""SELECT t.name, t.is_active, COALESCE(ps.blocked, 0) AS blocked,
                                   COALESCE(ps.violations, 0) AS violations
                            FROM teams t LEFT JOIN participant_security ps ON ps.team_id = t.id
                            WHERE t.id = ?""", (team_id,)).fetchone()
        if not team:
            return jsonify(success=False, error="Team not found."), 404
        restored = bool(team["blocked"] or not team["is_active"])
        if restored:
            _restore_team_access(conn, team_id)
            conn.execute("INSERT INTO admin_actions(action, details) VALUES (?, ?)",
                         ("UNBAN_TEAM", f"Restored team {team_id}; violation count reset to 0/2. Saved work retained."))
        conn.commit()
    finally:
        conn.close()
    return jsonify(success=True, team_id=team_id, blocked=False, is_active=True,
                   violations=0 if restored else team["violations"],
                   restored=restored,
                   message=f"{team['name']} unbanned. Violations reset to 0/2; saved answers and scores retained."
                   if restored else f"{team['name']} already has access.")


@app.route("/api/admin/toggle-team", methods=["POST"])
@admin_required
def api_admin_toggle_team():
    if get_competition_controls()["results_published"]:
        return jsonify(success=False, error="Results are final. Reset the event before changing competition data."), 409
    team_id = (request.get_json() or {}).get("team_id")
    if not isinstance(team_id, str) or not team_id or len(team_id) > 40:
        return jsonify({"success": False, "error": "Team ID required."}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE teams SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?", (team_id,))
    changed = cur.rowcount
    active = cur.execute("SELECT is_active FROM teams WHERE id = ?", (team_id,)).fetchone()
    if active and active[0]:
        _restore_team_access(conn, team_id)
    if changed == 0:
        conn.close()
        return jsonify(success=False, error="Team not found."), 404
    conn.commit()
    conn.close()
    log_admin_action("TOGGLE_TEAM", f"Toggled active state for team {team_id}.")
    return jsonify({"success": True, "message": f"Team {team_id} status toggled."})

@app.route("/api/admin/toggle-question", methods=["POST"])
@admin_required
def api_admin_toggle_question():
    if get_competition_controls()["results_published"]:
        return jsonify(success=False, error="Results are final. Reset the event before changing competition data."), 409
    question_id = (request.get_json() or {}).get("question_id")
    if not isinstance(question_id, str) or not question_id or len(question_id) > 40:
        return jsonify({"success": False, "error": "Question ID required."}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE questions SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?", (question_id,))
    if cur.rowcount == 0:
        conn.close()
        return jsonify(success=False, error="Question not found."), 404
    conn.commit()
    conn.close()
    log_admin_action("TOGGLE_QUESTION", f"Toggled active state for question {question_id}.")
    return jsonify({"success": True, "message": f"Question {question_id} status toggled."})

@app.route("/api/admin/reset-event", methods=["POST"])
@admin_required
def api_admin_reset_event():
    conf = (request.get_json() or {}).get("confirmation", "")
    ok, msg = reset_event_data(conf)
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "message": msg})

# --- CSV Export Endpoints ---

@app.route("/admin/export/teams.csv")
@admin_required
def export_teams_csv():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.name, t.created_at,
               (SELECT GROUP_CONCAT(name, ' / ') FROM participants WHERE team_id = t.id) as roster,
               t.is_active
        FROM teams t ORDER BY t.id ASC
    """)
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Team ID", "Team Name", "Team Members Roster", "Registration Time", "Status"])
    for r in rows:
        writer.writerow([r["id"], r["name"], r["roster"], r["created_at"], "ACTIVE" if r["is_active"] else "DISABLED"])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=teams_roster.csv"}
    )

@app.route("/admin/export/results.csv")
@admin_required
def export_results_csv():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            t.id, t.name,
            (SELECT GROUP_CONCAT(name, ' / ') FROM participants WHERE team_id = t.id) as roster,
            COALESCE(s.completed_count, 0) as completed_count,
            COALESCE(s.score, 0) as score,
            MIN(s.last_submission_time) as finish_time
        FROM teams t
        LEFT JOIN scores s ON t.id = s.team_id
        WHERE t.is_active = 1
        GROUP BY t.id
        ORDER BY score DESC, completed_count DESC, finish_time ASC
    """)
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Rank", "Team ID", "Team Name", "Roster", "Questions Completed", "Final Score", "Last Submission"])
    for idx, r in enumerate(rows, 1):
        writer.writerow([
            idx, r["id"], r["name"], r["roster"],
            r["completed_count"], f"{r['score']:.1f}", r["finish_time"] or "N/A"
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=final_leaderboard.csv"}
    )

@app.route("/admin/export/feedback.csv")
@admin_required
def export_feedback_csv():
    import json
    conn = get_db_connection()
    try:
        rows = conn.execute("""SELECT f.*, t.name AS team_name
            FROM quiz_feedback f JOIN teams t ON t.id = f.team_id
            ORDER BY f.submitted_at, f.team_id""").fetchall()
    finally:
        conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Team ID", "Team Name", "Clarity", "Difficulty", "Interface",
                     "Pacing", "Enjoyment", "Overall", "Feedback Note", "Submitted At"])
    def csv_text(value):
        text = str(value or "")
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r")) else text
    for row in rows:
        ratings = json.loads(row["ratings_json"])
        writer.writerow([row["team_id"], csv_text(row["team_name"]),
                         *[ratings.get(question["id"], "") for question in FEEDBACK_QUESTIONS],
                         csv_text(row["note"]), row["submitted_at"]])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=participant_feedback.csv"})


# --- Live participant and organizer APIs ---

@app.errorhandler(413)
def request_too_large(error):
    if request.path.startswith("/api/"):
        return jsonify(success=False, error="This request is too large. Shorten your response and try again."), 413
    return "This request is too large.", 413


@app.route("/api/team-name-available")
def api_team_name_available():
    name = request.args.get("name", "").strip()
    if not name or len(name) > 80:
        return jsonify(available=False, message="Enter a team name of 1–80 characters.")
    conn = get_db_connection()
    exists = conn.execute("SELECT 1 FROM teams WHERE UPPER(name) = UPPER(?)", (name,)).fetchone()
    conn.close()
    return jsonify(available=not bool(exists), message="Team name already exists" if exists else "Team name available")


@app.route("/api/team-progress")
@team_required
def api_team_progress():
    team_id = session["team_id"]
    state = get_event_state()
    conn = get_db_connection()
    try:
        sc = dict(conn.execute("SELECT score, completed_count FROM scores WHERE team_id = ?", (team_id,)).fetchone())
        powerups = {r["powerup_type"]: dict(r) for r in conn.execute("SELECT * FROM powerups WHERE team_id = ?", (team_id,))}
        latest = conn.execute("SELECT 1 FROM team_activity WHERE team_id = ? AND created_at >= datetime('now', '-60 seconds') LIMIT 1", (team_id,)).fetchone()
        if not latest:
            record_activity(team_id, "heartbeat", cur=conn.cursor())
            conn.commit()
    finally:
        conn.close()
    questions = get_team_assigned_questions(team_id)
    return jsonify(success=True, score=sc["score"], debug_score=sc["score"], completed_count=sc["completed_count"],
                   total_questions=len(questions), questions=questions, powerups=powerups,
                   generation=get_competition_controls()["generation"],
                   event_status=state["event_status"], remaining_seconds=state["remaining_seconds"])


@app.route("/blocked")
def participant_blocked():
    if not session.get("team_id"):
        return redirect(url_for("register"))
    return render_template("blocked.html")


@app.route("/api/fullscreen/status")
@team_required
def api_fullscreen_status():
    return jsonify(success=True, **guard_snapshot(session["team_id"]))


@app.route("/api/activity", methods=["POST"])
@team_required
def api_activity():
    data = request.get_json()
    event_type = data.get("event_type", data.get("type"))
    event_type = {"focus_lost": "window_blur", "focus_regained": "window_focus", "tab_visible": "window_visible"}.get(event_type, event_type) if isinstance(event_type, str) else event_type
    allowed = {"fullscreen_exit", "fullscreen_enter", "fullscreen_leave", "tab_hidden", "window_blur", "window_focus", "window_visible"}
    if not isinstance(event_type, str) or event_type not in allowed:
        return jsonify(success=False, error="Unrecognized activity signal."), 400
    if event_type.startswith("fullscreen_") or event_type in {"window_blur", "tab_hidden"}:
        event_id = data.get("event_id")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 80:
            return jsonify(success=False, error="Competition activity requires a unique event identifier."), 400
        activation_id, document_id = data.get("activation_id"), data.get("document_id")
        if any(value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 80) for value in (activation_id, document_id)):
            return jsonify(success=False, error="Invalid competition session identifier."), 400
        document_started_at = data.get("document_started_at", 0)
        if type(document_started_at) not in (int, float) or not math.isfinite(document_started_at) or document_started_at < 0:
            return jsonify(success=False, error="Invalid document start time."), 400
        return jsonify(success=True, **fullscreen_signal(session["team_id"], event_type, event_id, activation_id, document_id, document_started_at))
    conn = get_db_connection()
    try:
        recent = conn.execute("SELECT 1 FROM team_activity WHERE team_id = ? AND event_type = ? AND created_at >= datetime('now', '-2 seconds') LIMIT 1", (session["team_id"], event_type)).fetchone()
        if not recent:
            record_activity(session["team_id"], event_type, cur=conn.cursor())
        conn.commit()
    finally:
        conn.close()
    return jsonify(success=True, recorded=not bool(recent))


@app.route("/quiz")
@team_required
def quiz():
    state = get_event_state()
    if state["event_status"] != "COMPLETED":
        return redirect(url_for("waiting" if state["event_status"] == "WAITING" else "arena"))
    conn = get_db_connection()
    team = conn.execute("SELECT id, name FROM teams WHERE id = ?", (session["team_id"],)).fetchone()
    conn.close()
    return render_template("quiz.html", team=team, quiz=quiz_snapshot(session["team_id"]))


def _quiz_response(snapshot):
    snapshot["feedback"] = feedback_snapshot(session["team_id"])
    snapshot["feedback_questions"] = FEEDBACK_QUESTIONS
    return jsonify(success=True, quiz=snapshot, event_status=get_event_state()["event_status"],
                   results_published=bool(get_competition_controls()["results_published"]))


@app.route("/api/quiz/status")
@team_required
def api_quiz_status():
    return _quiz_response(quiz_snapshot(session["team_id"]))


@app.route("/api/quiz/start", methods=["POST"])
@team_required
def api_quiz_start():
    get_event_state()  # Materialize a timer expiry before opening the fun round.
    snapshot, error = start_quiz(session["team_id"])
    if error:
        return jsonify(success=False, error=error), 403
    return _quiz_response(snapshot)


@app.route("/api/quiz/answer", methods=["POST"])
@team_required
def api_quiz_answer():
    data = request.get_json()
    snapshot, error = answer_quiz(session["team_id"], data.get("question_id"), data.get("answer_index"))
    if error:
        return jsonify(success=False, error=error), 400
    return _quiz_response(snapshot)


@app.route("/api/quiz/feedback", methods=["POST"])
@team_required
def api_quiz_feedback():
    quiz_snapshot(session["team_id"])  # Persist any final question timeout.
    feedback, error = save_feedback(session["team_id"], request.get_json())
    if error:
        return jsonify(success=False, error=error), 400
    return jsonify(success=True, feedback=feedback)


@app.route("/api/admin/feedback")
@admin_required
def api_admin_feedback():
    conn = get_db_connection()
    rows = [dict(row) for row in conn.execute("SELECT f.*, t.name AS team_name FROM quiz_feedback f JOIN teams t ON t.id = f.team_id ORDER BY submitted_at DESC")]
    conn.close()
    import json
    for row in rows:
        row["ratings"] = json.loads(row.pop("ratings_json"))
    return jsonify(success=True, questions=FEEDBACK_QUESTIONS, responses=rows)


@app.route("/api/admin/quiz-action", methods=["POST"])
@admin_required
def api_admin_quiz_action():
    action = request.get_json().get("action")
    if action not in ("open", "close"):
        return jsonify(success=False, error="Choose open or close for the quiz."), 400
    if get_event_state()["event_status"] != "COMPLETED":
        return jsonify(success=False, error="Complete debugging before opening the quiz."), 400
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT quiz_status FROM competition_controls WHERE id = 1").fetchone()[0]
        if action == "open" and current == "CLOSED":
            return jsonify(success=False, error="This quiz has closed. Reset the event to begin a new quiz."), 400
        if action == "close" and current != "OPEN":
            return jsonify(success=False, error="The quiz is not open."), 400
        conn.execute("UPDATE competition_controls SET quiz_status = ? WHERE id = 1", ("OPEN" if action == "open" else "CLOSED",))
        conn.commit()
    finally:
        conn.close()
    log_admin_action("QUIZ_" + action.upper(), "Fun round only; debugging scores unchanged.")
    return jsonify(success=True, message="Quiz opened." if action == "open" else "Quiz closed.")


@app.route("/api/admin/publish-results", methods=["POST"])
@admin_required
def api_admin_publish_results():
    if get_event_state()["event_status"] != "COMPLETED":
        return jsonify(success=False, error="Complete debugging before publishing winners."), 400
    conn = get_db_connection()
    try:
        conn.execute("UPDATE competition_controls SET results_published = 1 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()
    log_admin_action("PUBLISH_RESULTS", "Verified debugging rankings published. Quiz scores excluded.")
    return jsonify(success=True, message="Verified debugging results published.")


@app.route("/api/admin/dashboard-data")
@admin_required
def api_admin_dashboard_data():
    state = get_event_state()
    conn = get_db_connection()
    try:
        stats = {
            "registered_teams": conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
            "active_teams": conn.execute("SELECT COUNT(DISTINCT a.team_id) FROM team_activity a JOIN teams t ON t.id = a.team_id WHERE t.is_active = 1 AND a.created_at >= datetime('now', '-5 minutes')").fetchone()[0],
            "total_submissions": conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
            "average_score": round(conn.execute("SELECT COALESCE(AVG(s.score), 0) FROM teams t LEFT JOIN scores s ON s.team_id = t.id WHERE t.is_active = 1").fetchone()[0], 1),
            "questions_solved": conn.execute("SELECT COALESCE(SUM(completed_count), 0) FROM scores").fetchone()[0],
        }
        activity = [dict(r) for r in conn.execute("""SELECT a.event_type, a.question_id, a.created_at, t.name AS team_name
            FROM team_activity a JOIN teams t ON a.team_id = t.id WHERE a.event_type != 'heartbeat'
            ORDER BY a.id DESC LIMIT 30""")]
        security = [dict(r) for r in conn.execute("""SELECT t.id AS team_id, t.name AS team_name, t.is_active,
            SUM(CASE WHEN a.event_type = 'fullscreen_exit' THEN 1 ELSE 0 END) AS fullscreen_exits,
            SUM(CASE WHEN a.event_type = 'tab_hidden' THEN 1 ELSE 0 END) AS tab_switches,
            SUM(CASE WHEN a.event_type = 'window_blur' THEN 1 ELSE 0 END) AS focus_losses,
            MAX(a.created_at) AS last_seen, COALESCE(ps.blocked, 0) AS blocked,
            COALESCE(ps.violations, 0) AS violations
            FROM teams t LEFT JOIN team_activity a ON a.team_id = t.id
            LEFT JOIN participant_security ps ON ps.team_id = t.id GROUP BY t.id ORDER BY t.name""")]
        controls = dict(conn.execute("SELECT * FROM competition_controls WHERE id = 1").fetchone())
        quiz_data = {"status": controls["quiz_status"], "question_count": len(QUIZ_QUESTIONS), "duration_minutes": QUIZ_MINUTES,
                     "participants": conn.execute("SELECT COUNT(*) FROM quiz_sessions").fetchone()[0],
                     "completed": conn.execute("SELECT COUNT(*) FROM quiz_sessions WHERE completed_at IS NOT NULL").fetchone()[0]}
    finally:
        conn.close()
    return jsonify(success=True, stats=stats, activity=activity, security=security, event_state=state,
                   quiz=quiz_data, results_published=bool(controls["results_published"]))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n==========================================")
    print(f" EXIT CODE 0 -- IEEE COMPUTER SOCIETY")
    print(f" {Config.load_event_config()['duration_minutes']}-Minute Competitive Debugging Arena")
    print(f" Access URL: http://127.0.0.1:{port}")
    print(f" Admin URL:  http://127.0.0.1:{port}/admin/login")
    print(f"==========================================\n")
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
