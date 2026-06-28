"""Provenance Guard — Flask API.

A multi-signal AI-content attribution service: POST /submit classifies text using two
signals (stylometry + LLM), scores confidence, and returns a plain-language transparency
label. POST /appeal lets creators contest a verdict. Every decision and appeal is
recorded in a structured audit log exposed via GET /log.
"""

import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from audit import (
    create_content,
    get_classification,
    get_content,
    get_log,
    init_db,
    log_appeal,
    log_classification,
    now_iso,
    update_content_status,
)
from labels import build_label
from llm_signal import analyze_llm
from scoring import combine_signals
from stylometry import analyze_stylometry

app = Flask(__name__)

# Rate limiting (see README "Rate Limiting" for the chosen limits and reasoning).
# In-memory storage is fine for local/dev; a real deployment would use Redis.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# Per-submission limits, justified in the README:
#   10/minute  — generous for a human writer drafting/revising, hostile to scripted floods
#   100/hour   — a heavy but plausible day of editing; blocks sustained automated abuse
#   500/day    — platform-level ceiling per client
SUBMIT_LIMITS = "10 per minute;100 per hour;500 per day"

# Stylometry is unreliable on very short inputs (see planning.md edge cases), so we
# require a minimum length before attempting analysis.
MIN_TEXT_CHARS = 40

init_db()


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit(SUBMIT_LIMITS)
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

    # Transparency label — text varies by confidence level (see labels.py / planning.md).
    label = build_label(scored["label_variant"], scored["confidence"])

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


@app.post("/appeal")
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = (data.get("content_id") or "").strip()
    reasoning = (data.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not reasoning:
        return (
            jsonify({"error": "Field 'creator_reasoning' is required; an appeal must "
                              "include the creator's reasoning."}),
            400,
        )

    content = get_content(content_id)
    if content is None:
        return jsonify({"error": f"No content found with id '{content_id}'."}), 404

    timestamp = now_iso()
    appeal_id = str(uuid.uuid4())

    # Update status: classified -> under_review.
    update_content_status(content_id, "under_review", timestamp)

    # Log the appeal alongside the original decision it contests.
    original = get_classification(content_id) or {}
    appeal_entry = {
        "appeal_id": appeal_id,
        "content_id": content_id,
        "creator_id": content.get("creator_id"),
        "timestamp": timestamp,
        "status": "under_review",
        "appeal_reasoning": reasoning,
        # Reference to the original classification so a reviewer sees both together.
        "original_attribution": original.get("attribution"),
        "original_confidence": original.get("confidence"),
    }
    log_appeal(appeal_entry)

    return jsonify(
        {
            "content_id": content_id,
            "appeal_id": appeal_id,
            "status": "under_review",
            "message": "Appeal received. This content is now under review by a human moderator.",
            "timestamp": timestamp,
        }
    )


@app.get("/log")
def log():
    return jsonify({"entries": get_log()})


@app.errorhandler(429)
def ratelimit_handler(e):
    return (
        jsonify(
            {
                "error": "Rate limit exceeded.",
                "detail": str(e.description),
                "limit": SUBMIT_LIMITS,
            }
        ),
        429,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
