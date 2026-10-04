import ast
import re
import json
from datetime import datetime, timezone
from rapidfuzz import fuzz
from answer_feedback import answer_field_results
from database import get_db_connection, unlock_next_question, record_activity, recalculate_team_score

def normalize_text(text):
    if not text:
        return ""
    text = str(text).strip().lower()
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_line_numbers(text):
    if not text:
        return []
    matches = re.findall(r'(?:line\s*|l)?(\d+)', str(text).lower())
    return [int(m) for m in matches if m.isdigit()]

# Percentage rules remain only for pending tools activated before the update.
HINT_PENALTY_RATE = 0.10
SWAP_PENALTY_RATE = 0.10
HINT_COST = 5
SWAP_COST = 7
DOUBLE_COMMIT_BONUS = 15
SCORING_VERSION = "single-line-v1"
_C_TOKEN = re.compile(r'''(?:u8|u|U|L)?(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|[A-Za-z_][A-Za-z_0-9]*|(?:0[xX][0-9A-Fa-f]+(?:\.[0-9A-Fa-f]*)?(?:[pP][+-]?\d+)?|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)[uUlLfF]*|>>=|<<=|\.\.\.|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||\+=|-=|\*=|/=|%=|&=|\^=|\|=|##|[{}\[\]();:?~!%^&*+=|<>.,/#-]''')


def _single_code_line(value):
    return isinstance(value, str) and bool(value.strip()) and len(value.splitlines()) == 1 and "\n" not in value and "\r" not in value


def _c_tokens(line):
    """Keep literals and operators intact while ignoring formatting/comments."""
    tokens, offset = [], 0
    while offset < len(line):
        if line[offset].isspace():
            offset += 1
            continue
        if line.startswith("//", offset):
            break
        if line.startswith("/*", offset):
            end = line.find("*/", offset + 2)
            if end == -1:
                return None
            offset = end + 2
            continue
        token = _C_TOKEN.match(line, offset)
        if not token:
            return None
        tokens.append(token.group())
        offset = token.end()
    return tokens


def correction_matches(question, response):
    """Compare one replacement code line without executing participant code.

    Python uses the complete repaired program's syntax tree, so quotes and safe
    formatting can vary while case, values, operators and block structure matter.
    C compares lexical tokens, preserving strings, punctuation and operators.
    """
    reference = question.get("correction", "")
    if not _single_code_line(response) or not _single_code_line(reference):
        return False
    language = str(question.get("language", "")).casefold()
    if language == "c":
        expected, actual = _c_tokens(reference), _c_tokens(response)
        return bool(expected) and actual == expected
    if language != "python":
        return False
    locations = extract_line_numbers(question.get("bug_location", ""))
    source = str(question.get("code", "")).splitlines()
    if len(locations) != 1 or not 1 <= locations[0] <= len(source):
        return False
    index = locations[0] - 1
    original_indent = re.match(r"[ \t]*", source[index]).group()
    correct_indent = re.match(r"[ \t]*", reference).group()
    # Let an answer omit indentation when the question's indentation is already
    # correct. Indentation bugs must still supply the repaired block depth.
    if response == response.lstrip(" \t") and original_indent == correct_indent:
        response = original_indent + response
    expected, actual = list(source), list(source)
    expected[index], actual[index] = reference.rstrip(), response.rstrip()
    try:
        return ast.dump(ast.parse("\n".join(expected)), include_attributes=False) == ast.dump(ast.parse("\n".join(actual)), include_attributes=False)
    except (SyntaxError, ValueError, RecursionError):
        return False


def evaluate_submission(question, user_submission, is_double_commit=False, hint_used=False, swap_used=False):
    """Grade fields with legacy modifiers only for pre-update power-up uses.

    Root-cause prose is not scored. The correction must be one valid code line.
    Double Commit applies to the raw score first; hint and swap then each deduct
    10% of the question's base points, with the final score floored at zero.
    """
    base_points = max(0.0, float(question.get("points", question.get("base_points", 20))))

    ref_error_type = normalize_text(question.get("error_type", ""))
    user_error_type = normalize_text(user_submission.get("error_type", ""))
    # A bare "error" must not match every category.
    ref_type = re.sub(r"\b(?:error|exception)\b", "", ref_error_type).strip()
    user_type = re.sub(r"\b(?:error|exception)\b", "", user_error_type).strip()
    type_score = 0.15 * base_points if ref_type and user_type and (
        user_type == ref_type or fuzz.ratio(user_type, ref_type) >= 85
    ) else 0.0

    ref_loc_lines = set(extract_line_numbers(question.get("bug_location", "")))
    user_loc_lines = set(extract_line_numbers(user_submission.get("error_location", "")))
    loc_score = 0.0
    if ref_loc_lines:
        # Listing every line is not a valid location identification.
        if user_loc_lines and user_loc_lines.issubset(ref_loc_lines):
            loc_score = 0.10 * base_points
    else:
        ref_loc = normalize_text(question.get("bug_location", ""))
        user_loc = normalize_text(user_submission.get("error_location", ""))
        if ref_loc and user_loc == ref_loc:
            loc_score = 0.10 * base_points

    # Keep the legacy database component for historical records, always zero for
    # new submissions under the four-field rubric.
    cause_score = 0.0

    # Output is a short, concrete value: substring/fuzzy matching awarded points
    # for "1" against "10", and punctuation stripping lost negative signs.
    ref_output = " ".join(str(question.get("expected_output", "")).casefold().split())
    user_output = " ".join(str(user_submission.get("expected_output", "")).casefold().split())
    output_score = 0.20 * base_points if ref_output and user_output == ref_output else 0.0

    corr_score = 0.55 * base_points if correction_matches(question, user_submission.get("correction", "")) else 0.0

    raw_total = round(type_score + loc_score + cause_score + output_score + corr_score, 2)
    modified_total = raw_total
    if is_double_commit:
        modified_total = raw_total * 2.0 if raw_total >= 0.60 * base_points else 0.0
    hint_penalty = round(base_points * HINT_PENALTY_RATE, 2) if hint_used else 0.0
    swap_penalty = round(base_points * SWAP_PENALTY_RATE, 2) if swap_used else 0.0
    penalty_total = hint_penalty + swap_penalty
    final_total = max(0.0, modified_total - penalty_total)
    max_score = max(0.0, base_points * (2 if is_double_commit else 1) - penalty_total)
    answer_status = "correct" if base_points > 0 and raw_total >= base_points else "partial" if raw_total > 0 else "incorrect"

    result = {
        "scoring_version": SCORING_VERSION,
        "error_type_score": round(type_score, 2),
        "error_loc_score": round(loc_score, 2),
        "cause_score": round(cause_score, 2),
        "output_score": round(output_score, 2),
        "correction_score": round(corr_score, 2),
        "raw_total": raw_total,
        "raw_score": raw_total,
        "total_score": round(final_total, 2),
        "base_points": base_points,
        "max_score": round(max_score, 2),
        "answer_status": answer_status,
        "is_double_commit": int(bool(is_double_commit)),
        "hint_used": int(bool(hint_used)),
        "swap_used": int(bool(swap_used)),
        "penalties": {"hint": hint_penalty, "swap": swap_penalty, "total": round(penalty_total, 2),
                      "applied": round(min(modified_total, penalty_total), 2)},
        "percentage": round((raw_total / base_points) * 100, 1) if base_points > 0 else 0
    }
    result["field_results"] = answer_field_results(result, base_points)
    return result

def submission_score_limit(row):
    """Judge the saved question award separately from team-level tool changes."""
    row = dict(row)
    try:
        saved = json.loads(row.get("response_json") or "{}")
        if isinstance(saved, dict) and isinstance(saved.get("max_score"), (int, float)):
            return saved["max_score"]
    except (TypeError, ValueError):
        pass
    return row.get("points", 20) * (2 if row.get("is_double_commit") else 1)


def _live_error(cur):
    state = cur.execute("SELECT event_status, is_paused, event_end_time FROM event_state WHERE id = 1").fetchone()
    if not state or state["event_status"] != "LIVE" or state["is_paused"]:
        return "The competition is not currently active. Submissions are closed."
    if state["event_end_time"]:
        end = datetime.fromisoformat(state["event_end_time"])
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= end:
            return "Time has expired. Submissions are closed."
    return None


def _assignment_allowed(cur, team_id, question_id):
    return cur.execute("""SELECT 1 FROM question_assignments qa JOIN teams t ON t.id = qa.team_id
        WHERE qa.team_id = ? AND qa.question_id = ? AND qa.is_unlocked = 1
        AND qa.is_abandoned = 0 AND qa.is_completed = 0 AND t.is_active = 1
        AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.team_id = qa.team_id AND s.question_id = qa.question_id)
        """, (team_id, question_id)).fetchone() is not None


def process_submission(team_id, question_id, submission_data, enforce_live=False):
    """
    Validates, scores, persists submission and updates progression and team score.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if enforce_live:
            error = _live_error(cur)
            if error:
                return None, error
        request_id = submission_data.get("request_id")
        if request_id:
            existing = cur.execute("SELECT question_id, response_json FROM submissions WHERE team_id = ? AND request_id = ?", (team_id, request_id)).fetchone()
            if existing:
                if existing["question_id"] != question_id:
                    return None, "This request has already been used for another question."
                return json.loads(existing["response_json"]), None
        # BEGIN IMMEDIATE serializes competing answers, even with different request IDs.
        if cur.execute("SELECT 1 FROM submissions WHERE team_id = ? AND question_id = ?", (team_id, question_id)).fetchone():
            return None, "This question has already been answered. Your first submission is final."
        if not _assignment_allowed(cur, team_id, question_id):
            return None, "This question is not unlocked for your team."
        # Check if question exists
        cur.execute("SELECT * FROM questions WHERE id = ?", (question_id,))
        question = cur.fetchone()
        if not question:
            return None, "Question not found."

        # Check question assignment
        cur.execute("""
            SELECT * FROM question_assignments
            WHERE team_id = ? AND question_id = ? AND is_abandoned = 0
        """, (team_id, question_id))
        assignment = cur.fetchone()
        if not assignment:
            return None, "This question is not currently assigned to your team."

        # New tools adjust the team total at activation. Existing pending uses
        # without an adjustment retain their original rules until submitted.
        cur.execute("""
            SELECT * FROM powerups
            WHERE team_id = ? AND powerup_type = 'DOUBLE_COMMIT'
              AND (is_armed = 1 OR is_used = 1) AND target_question_id = ?
        """, (team_id, question_id))
        double_armed = cur.fetchone()
        is_double = bool(double_armed)

        # Replacement retains the original slot's hint cost as well as its swap cost.
        # This uses the recorded abandoned assignment, including the locked-question
        # fallback, so refreshes and retries cannot lose either modifier.
        swapped = cur.execute("""
            SELECT old.question_id, p.score_adjustment FROM question_assignments old
            JOIN powerups p ON p.team_id = old.team_id AND p.target_question_id = old.question_id
            WHERE old.team_id = ? AND old.question_order = ? AND old.is_abandoned = 1
              AND p.powerup_type = 'GIT_REVERT' AND p.is_used = 1
        """, (team_id, assignment["question_order"])).fetchone()
        source_qid = swapped["question_id"] if swapped else question_id
        hint = cur.execute("""
            SELECT score_adjustment FROM powerups WHERE team_id = ? AND powerup_type = 'RUBBER_DUCK'
              AND is_used = 1 AND target_question_id IN (?, ?)
        """, (team_id, question_id, source_qid)).fetchone()

        eval_result = evaluate_submission(
            dict(question), submission_data,
            is_double_commit=is_double and double_armed["score_adjustment"] == 0,
            hint_used=bool(hint) and hint["score_adjustment"] == 0,
            swap_used=bool(swapped) and swapped["score_adjustment"] == 0
        )
        eval_result.update(hint_used=int(bool(hint)), swap_used=int(bool(swapped)), is_double_commit=int(is_double))
        eval_result["powerup_adjustments"] = {
            "hint": hint["score_adjustment"] if hint else 0,
            "swap": swapped["score_adjustment"] if swapped else 0,
            "double_commit": double_armed["score_adjustment"] if double_armed else 0,
        }
        # Keep completed reviews tied to the exact public prompt answered, even
        # when organisers update the question bank later.
        from database import PUBLIC_QUESTION_FIELDS
        eval_result["question_snapshot"] = {key: question[key] for key in PUBLIC_QUESTION_FIELDS}

        # Record submission in SQLite
        cur.execute("""
            INSERT INTO submissions (
                team_id, question_id, error_location, error_type, expected_output, cause, correction,
                error_loc_score, error_type_score, cause_score, output_score, correction_score,
                total_score, is_double_commit, is_accepted, submitted_at, request_id, response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, ?, ?)
        """, (
            team_id, question_id,
            submission_data.get("error_location", ""),
            submission_data.get("error_type", ""),
            submission_data.get("expected_output", ""),
            "",
            submission_data.get("correction", ""),
            eval_result["error_loc_score"],
            eval_result["error_type_score"],
            eval_result["cause_score"],
            eval_result["output_score"],
            eval_result["correction_score"],
            eval_result["total_score"],
            eval_result["is_double_commit"],
            request_id, json.dumps(eval_result)
        ))

        # Mark question as completed
        cur.execute("""
            UPDATE question_assignments
            SET is_completed = 1
            WHERE team_id = ? AND question_id = ?
        """, (team_id, question_id))

        # Unlock next question in sequence
        unlock_next_question(team_id, assignment["question_order"], cur)

        # Consume Double Commit if armed
        if is_double:
            cur.execute("""
                UPDATE powerups
                SET is_used = 1, is_armed = 0, used_at = CURRENT_TIMESTAMP
                WHERE team_id = ? AND powerup_type = 'DOUBLE_COMMIT'
            """, (team_id,))

        recalculate_team_score(cur, team_id, submission=True)

        record_activity(team_id, "submission", question_id, cur)
        conn.commit()
        return eval_result, None
    except Exception as e:
        conn.rollback()
        return None, "Your submission could not be saved. Please try again."
    finally:
        conn.close()

def activate_rubber_duck(team_id, question_id, enforce_live=False):
    """Activates Rubber Duck hint for target question."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if enforce_live:
            error = _live_error(cur)
            if error:
                return False, error, None
        cur.execute("SELECT is_used FROM powerups WHERE team_id = ? AND powerup_type = 'RUBBER_DUCK'", (team_id,))
        row = cur.fetchone()
        if not row:
            return False, "Power-up not found.", None
        if row["is_used"]:
            return False, "Rubber Duck has already been used by your team.", None

        if not _assignment_allowed(cur, team_id, question_id):
            return False, "Question is not unlocked for your team.", None

        cur.execute("SELECT hint FROM questions WHERE id = ?", (question_id,))
        q = cur.fetchone()
        hint = q["hint"] if q and q["hint"] else "Review the line boundaries and logic conditions."

        cur.execute("""
            UPDATE powerups
            SET is_used = 1, used_at = CURRENT_TIMESTAMP, target_question_id = ?, score_adjustment = ?
            WHERE team_id = ? AND powerup_type = 'RUBBER_DUCK'
        """, (question_id, -HINT_COST, team_id))
        recalculate_team_score(cur, team_id)
        conn.commit()
        return True, "Hint revealed. 5 points deducted from your team score.", hint
    except Exception as e:
        conn.rollback()
        return False, "Power-up could not be saved. Please try again.", None
    finally:
        conn.close()

def activate_git_revert(team_id, question_id, enforce_live=False):
    """
    Abandons current question and swaps it for another active question not yet assigned.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if enforce_live:
            error = _live_error(cur)
            if error:
                return False, error, None
        cur.execute("SELECT is_used FROM powerups WHERE team_id = ? AND powerup_type = 'GIT_REVERT'", (team_id,))
        row = cur.fetchone()
        if not row:
            return False, "Power-up not found.", None
        if row["is_used"]:
            return False, "Git Revert has already been used by your team.", None

        if not _assignment_allowed(cur, team_id, question_id):
            return False, "Question is not unlocked for your team.", None

        cur.execute("""
            SELECT question_order, is_completed
            FROM question_assignments
            WHERE team_id = ? AND question_id = ? AND is_abandoned = 0
        """, (team_id, question_id))
        asgn = cur.fetchone()
        if not asgn:
            return False, "Question is not currently assigned to your team.", None
        if asgn["is_completed"]:
            return False, "Cannot revert a question that has already been completed.", None

        # 1. Try finding an unassigned question from bank
        cur.execute("""
            SELECT id FROM questions
            WHERE is_active = 1 AND id NOT IN (
                SELECT question_id FROM question_assignments WHERE team_id = ?
            )
            LIMIT 1
        """, (team_id,))
        candidate = cur.fetchone()

        if candidate:
            new_qid = candidate["id"]
            # Mark old question abandoned
            cur.execute("""
                UPDATE question_assignments
                SET is_abandoned = 1, is_unlocked = 0
                WHERE team_id = ? AND question_id = ?
            """, (team_id, question_id))

            # Insert replacement with same question order
            cur.execute("""
                INSERT INTO question_assignments
                (team_id, question_id, question_order, is_unlocked, is_completed, is_abandoned)
                VALUES (?, ?, ?, 1, 0, 0)
            """, (team_id, new_qid, asgn["question_order"]))
        else:
            # Fall back to swapping with a locked, unattempted question
            cur.execute("""
                SELECT question_id, question_order FROM question_assignments
                WHERE team_id = ? AND is_unlocked = 0 AND is_completed = 0 AND is_abandoned = 0
                ORDER BY question_order DESC
                LIMIT 1
            """, (team_id,))
            swap_target = cur.fetchone()
            if not swap_target:
                return False, "No alternative questions available.", None

            new_qid = swap_target["question_id"]
            # Mark old question abandoned
            cur.execute("""
                UPDATE question_assignments
                SET is_abandoned = 1, is_unlocked = 0
                WHERE team_id = ? AND question_id = ?
            """, (team_id, question_id))

            # Unlock swap target at current order
            cur.execute("""
                UPDATE question_assignments
                SET is_unlocked = 1, question_order = ?
                WHERE team_id = ? AND question_id = ?
            """, (asgn["question_order"], team_id, new_qid))

        # Mark powerup used
        cur.execute("""
            UPDATE powerups
            SET is_used = 1, used_at = CURRENT_TIMESTAMP, target_question_id = ?, score_adjustment = ?
            WHERE team_id = ? AND powerup_type = 'GIT_REVERT'
        """, (question_id, -SWAP_COST, team_id))

        recalculate_team_score(cur, team_id)
        conn.commit()
        return True, "Question changed. 7 points deducted from your team score.", new_qid
    except Exception as e:
        conn.rollback()
        return False, "Power-up could not be saved. Please try again.", None
    finally:
        conn.close()

def arm_double_commit(team_id, question_id, enforce_live=False):
    """Apply the team's one-time +15 Double Commit bonus immediately."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if enforce_live:
            error = _live_error(cur)
            if error:
                return False, error
        cur.execute("SELECT is_used, is_armed FROM powerups WHERE team_id = ? AND powerup_type = 'DOUBLE_COMMIT'", (team_id,))
        row = cur.fetchone()
        if not row:
            return False, "Power-up not found."
        if row["is_used"] or row["is_armed"]:
            return False, "Double Commit has already been used by your team."

        if not _assignment_allowed(cur, team_id, question_id):
            return False, "Question is not unlocked for your team."

        cur.execute("""
            UPDATE powerups
            SET is_used = 1, is_armed = 0, used_at = CURRENT_TIMESTAMP,
                target_question_id = ?, score_adjustment = ?
            WHERE team_id = ? AND powerup_type = 'DOUBLE_COMMIT'
        """, (question_id, DOUBLE_COMMIT_BONUS, team_id))
        recalculate_team_score(cur, team_id)
        conn.commit()
        return True, "Double Commit used. 15 points added to your team score."
    except Exception as e:
        conn.rollback()
        return False, "Power-up could not be saved. Please try again."
    finally:
        conn.close()
