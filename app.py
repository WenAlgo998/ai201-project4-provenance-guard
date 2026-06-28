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
from llm_signal import analyze_llm
from scoring import combine_signals
from stylometry import analyze_stylometry

app = Flask(__name__)

# Detailed, justified rate limits land in Milestone 5. For now the limiter is wired
# up but left permissive so it doesn't get in the way of M3 testing.
limiter = Limiter(get_remote_address, app=app, default_limits=[])

# Stylometry is unreliable on very short inputs (see planning.md edge cases), so we
# require a minimum length before attempting analysis.
MIN_TEXT_CHARS = 40

init_db()


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

    # --- Multi-signal detection pipeline ---
    stylo = analyze_stylometry(text)        # structural signal (pure Python)
    llm = analyze_llm(text)                  # semantic signal (Groq)
    scored = combine_signals(llm, stylo)     # agreement-aware confidence scorer

    # Placeholder label until the label builder is added in Milestone 5; the variant
    # key is already chosen by the scorer, so M5 only supplies the wording.
    label = {
        "variant": scored["label_variant"],
        "title": "(transparency label added in Milestone 5)",
        "body": "",
    }

    # Persist: content status + structured audit entry capturing BOTH individual signal
    # scores alongside the combined confidence (required for the multi-signal audit log).
    create_content(content_id, creator_id, "classified", timestamp)
    audit_entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "attribution": scored["verdict"],
        "confidence": scored["confidence"],
        "combined_ai_score": scored["combined_ai_score"],
        "agreement": scored["agreement"],
        "signals": {
            "llm": {"ai_score": llm.get("ai_score"), "available": llm.get("available", False),
                    "rationale": llm.get("rationale", "")},
            "stylometry": {"ai_score": stylo["ai_score"], "reliable": stylo["reliable"],
                           "metrics": stylo["metrics"]},
        },
        "weights": scored["weights"],
        "notes": scored["notes"],
        "status": "classified",
    }
    log_classification(audit_entry)

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": {
                "verdict": scored["verdict"],
                "combined_ai_score": scored["combined_ai_score"],
                "confidence": scored["confidence"],
            },
            "confidence": scored["confidence"],
            "label": label,
            "signals": {
                "llm": {
                    "ai_score": llm.get("ai_score"),
                    "available": llm.get("available", False),
                    "rationale": llm.get("rationale", ""),
                },
                "stylometry": {
                    "ai_score": stylo["ai_score"],
                    "reliable": stylo["reliable"],
                    "metrics": stylo["metrics"],
                },
            },
            "scoring": {
                "agreement": scored["agreement"],
                "weights": scored["weights"],
                "notes": scored["notes"],
            },
            "status": "classified",
            "timestamp": timestamp,
        }
    )


@app.get("/log")
def log():
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
