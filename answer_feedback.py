"""Participant-safe feedback derived only from saved scoring components."""
import json

ANSWER_FIELDS = (
    ("error_location", "Error location", "error_loc_score", 0.10),
    ("error_type", "Error type", "error_type_score", 0.15),
    ("expected_output", "Expected output", "output_score", 0.20),
    ("correction", "Corrected code line", "correction_score", 0.55),
)


def answer_field_results(scores, base_points):
    """Describe automated credit before penalties, multipliers or total overrides.

    Accept component scores, never reference answers, so old submissions can be
    reviewed without regrading them against a newer question bank.
    """
    version = scores.get("scoring_version")
    if not version:
        try:
            saved = json.loads(scores.get("response_json") or "{}")
            version = saved.get("scoring_version") if isinstance(saved, dict) else None
        except (TypeError, ValueError):
            version = None
    results = []
    for key, label, score_key, weight in ANSWER_FIELDS:
        # Historical correction points retain their original denominator. Hiding
        # the retired cause field must never regrade or alter a team's total.
        if key == "correction" and version != "single-line-v1":
            weight = 0.30
        score = round(float(scores.get(score_key) or 0), 2)
        maximum = round(max(0, float(base_points)) * weight, 2)
        status = "correct" if maximum > 0 and score >= maximum else "partial" if score > 0 else "incorrect"
        results.append({"key": key, "label": label, "status": status,
                        "score": score, "max_score": maximum})
    return results
