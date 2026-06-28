"""Provenance Guard — Flask API.

Milestone 3 scope: a working POST /submit endpoint backed by the first detection
signal (stylometry), plus a structured audit log and GET /log.

Confidence and label are placeholders here; the real confidence scorer + LLM signal
arrive in Milestone 4, and the transparency labels + appeals in Milestone 5. The
response shape already matches the API contract in planning.md so later milestones
fill in fields rather than reshape them.
"""

import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from audit import (
    create_content,
    get_log,
    init_db,
    log_classification,
    now_iso,
)
from stylometry import analyze_stylometry

app = Flask(__name__)

# Detailed, justified rate limits land in Milestone 5. For now the limiter is wired
# up but left permissive so it doesn't get in the way of M3 testing.
limiter = Limiter(get_remote_address, app=app, default_limits=[])

# Stylometry is unreliable on very short inputs (see planning.md edge cases), so we
# require a minimum length before attempting analysis.
MIN_TEXT_CHARS = 40

init_db()


def _placeholder_verdict(ai_score):
    """Single-signal placeholder attribution (refined by the M4 confidence scorer).

    Uses the human-biased bands from planning.md: it takes a strong signal to call
    something AI, and the middle is 'uncertain'.
    """
    if ai_score >= 0.65:
        return "likely_ai"
    if ai_score <= 0.35:
        return "likely_human"
    return "uncertain"


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
def submit():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    creator_id = data.get("creator_id")

    if not text:
        return jsonify({"error": "Field 'text' is required and must be non-empty."}), 400
    if len(text) < MIN_TEXT_CHARS:
        return (
            jsonify(
                {
                    "error": f"Text too short for analysis; please submit at least "
                    f"{MIN_TEXT_CHARS} characters."
                }
            ),
            400,
        )

    content_id = str(uuid.uuid4())
    timestamp = now_iso()

    # --- Signal 1 (implemented): stylometry ---
    stylo = analyze_stylometry(text)
    stylo_score = stylo["ai_score"]

    attribution = _placeholder_verdict(stylo_score)
    # Placeholder confidence: distance of the single signal from the fence. The real,
    # agreement-aware confidence scorer replaces this in Milestone 4.
    confidence = round(abs(stylo_score - 0.5) * 2, 3)

    # Placeholder label until the label builder is added in Milestone 5.
    label = {
        "variant": "placeholder",
        "title": "(transparency label added in Milestone 5)",
        "body": "",
    }

    # Persist: content status + structured audit entry.
    create_content(content_id, creator_id, "classified", timestamp)
    audit_entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "attribution": attribution,
        "confidence": confidence,
        "stylo_score": stylo_score,
        "signals": {"stylometry": stylo},
        "status": "classified",
    }
    log_classification(audit_entry)

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": {"verdict": attribution, "stylo_ai_score": stylo_score},
            "confidence": confidence,
            "label": label,
            "signals": {"stylometry": stylo},
            "status": "classified",
            "timestamp": timestamp,
        }
    )


@app.get("/log")
def log():
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
