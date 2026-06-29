"""Provenance Guard — Flask API.

A multi-signal AI-content attribution service. POST /submit classifies text (or, via the
multi-modal stretch feature, image metadata) using an ensemble of signals, scores
confidence, and returns a plain-language transparency label. POST /appeal lets creators
contest a verdict. Creators can earn a "Verified Human" provenance certificate. Every
decision, appeal, and certification is recorded in a structured audit log (GET /log), and
aggregate metrics are exposed via GET /analytics and a /dashboard view.
"""

import uuid

from flask import Flask, Response, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from analytics import DASHBOARD_HTML, compute_metrics
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
from certificates import certificate_for, complete_verification, start_challenge
from labels import build_label, build_verified_label
from llm_signal import analyze_llm
from scoring import combine_signals
from signals_image import analyze_image_metadata
from signals_lexical import analyze_lexical
from stylometry import analyze_stylometry

app = Flask(__name__)

# Rate limiting (see README "Rate Limiting" for the chosen limits and reasoning).
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)
SUBMIT_LIMITS = "10 per minute;100 per hour;500 per day"

MIN_TEXT_CHARS = 40

init_db()


# --------------------------------------------------------------------------- pipelines


def _run_text_pipeline(text):
    """Ensemble of three signals for text: stylometry + LLM + lexical tells."""
    stylo = analyze_stylometry(text)
    llm = analyze_llm(text)
    lex = analyze_lexical(text)

    signals = [
        {"name": "llm", "ai_score": llm.get("ai_score"), "weight": 0.50,
         "available": llm.get("available", False), "reliable": True},
        {"name": "stylometry", "ai_score": stylo["ai_score"], "weight": 0.30,
         "available": True, "reliable": stylo["reliable"]},
        {"name": "lexical", "ai_score": lex["ai_score"], "weight": 0.20,
         "available": True, "reliable": True},
    ]
    signals_out = {
        "llm": {"ai_score": llm.get("ai_score"), "available": llm.get("available", False),
                "rationale": llm.get("rationale", "")},
        "stylometry": {"ai_score": stylo["ai_score"], "reliable": stylo["reliable"],
                       "metrics": stylo["metrics"]},
        "lexical": {"ai_score": lex["ai_score"], "matched": lex["matched"],
                    "density_per_100w": lex["density_per_100w"]},
    }
    return signals, signals_out


def _run_image_pipeline(metadata, caption):
    """Multi-modal pipeline: image-metadata provenance + LLM on the caption (if given)."""
    img = analyze_image_metadata(metadata)
    signals = [
        {"name": "image_metadata", "ai_score": img["ai_score"], "weight": 0.60,
         "available": True, "reliable": img["reliable"]},
    ]
    signals_out = {"image_metadata": img}

    if caption and len(caption.strip()) >= 20:
        llm = analyze_llm(caption)
        signals.append(
            {"name": "llm_caption", "ai_score": llm.get("ai_score"), "weight": 0.40,
             "available": llm.get("available", False), "reliable": True}
        )
        signals_out["llm_caption"] = {
            "ai_score": llm.get("ai_score"), "available": llm.get("available", False),
            "rationale": llm.get("rationale", ""),
        }
    return signals, signals_out


# ------------------------------------------------------------------------------ routes


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit(SUBMIT_LIMITS)
def submit():
    data = request.get_json(silent=True) or {}
    creator_id = data.get("creator_id")
    content_type = (data.get("content_type") or "text").lower()

    # ---- Modality branch (multi-modal stretch feature) ----
    if content_type == "image":
        metadata = data.get("metadata") or {}
        caption = data.get("caption") or data.get("text") or ""
        if not metadata and not caption:
            return jsonify({"error": "Image submissions require 'metadata' and/or a 'caption'."}), 400
        signals, signals_out = _run_image_pipeline(metadata, caption)
    else:
        content_type = "text"
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Field 'text' is required and must be non-empty."}), 400
        if len(text) < MIN_TEXT_CHARS:
            return (
                jsonify({"error": f"Text too short for analysis; please submit at least "
                                  f"{MIN_TEXT_CHARS} characters."}),
                400,
            )
        signals, signals_out = _run_text_pipeline(text)

    content_id = str(uuid.uuid4())
    timestamp = now_iso()

    scored = combine_signals(signals)
    label = build_label(scored["label_variant"], scored["confidence"])

    # ---- Provenance certificate display (stretch feature) ----
    cert = certificate_for(creator_id)
    verified_label = build_verified_label(cert) if cert else None

    create_content(content_id, creator_id, "classified", timestamp)
    audit_entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "content_type": content_type,
        "timestamp": timestamp,
        "attribution": scored["verdict"],
        "confidence": scored["confidence"],
        "combined_ai_score": scored["combined_ai_score"],
        "agreement": scored["agreement"],
        "signals": signals_out,
        "weights": scored["weights"],
        "notes": scored["notes"],
        "creator_verified": bool(cert),
        "status": "classified",
    }
    log_classification(audit_entry)

    response = {
        "content_id": content_id,
        "creator_id": creator_id,
        "content_type": content_type,
        "attribution": {
            "verdict": scored["verdict"],
            "combined_ai_score": scored["combined_ai_score"],
            "confidence": scored["confidence"],
        },
        "confidence": scored["confidence"],
        "label": label,
        "signals": signals_out,
        "scoring": {
            "ensemble_weights": scored["weights"],
            "agreement": scored["agreement"],
            "notes": scored["notes"],
        },
        "status": "classified",
        "timestamp": timestamp,
    }
    if verified_label:
        response["provenance_certificate"] = cert
        response["verified_label"] = verified_label
    return jsonify(response)


@app.post("/appeal")
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = (data.get("content_id") or "").strip()
    reasoning = (data.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not reasoning:
        return jsonify({"error": "Field 'creator_reasoning' is required; an appeal must "
                               "include the creator's reasoning."}), 400

    content = get_content(content_id)
    if content is None:
        return jsonify({"error": f"No content found with id '{content_id}'."}), 404

    timestamp = now_iso()
    appeal_id = str(uuid.uuid4())
    update_content_status(content_id, "under_review", timestamp)

    original = get_classification(content_id) or {}
    log_appeal(
        {
            "appeal_id": appeal_id,
            "content_id": content_id,
            "creator_id": content.get("creator_id"),
            "timestamp": timestamp,
            "status": "under_review",
            "appeal_reasoning": reasoning,
            "original_attribution": original.get("attribution"),
            "original_confidence": original.get("confidence"),
        }
    )
    return jsonify(
        {
            "content_id": content_id,
            "appeal_id": appeal_id,
            "status": "under_review",
            "message": "Appeal received. This content is now under review by a human moderator.",
            "timestamp": timestamp,
        }
    )


# --------------------------------------------- Provenance Certificate (stretch feature)


@app.get("/certify/challenge")
def certify_challenge():
    creator_id = request.args.get("creator_id")
    if not creator_id:
        return jsonify({"error": "Query param 'creator_id' is required."}), 400
    challenge_id, phrase = start_challenge(creator_id)
    return jsonify(
        {
            "creator_id": creator_id,
            "challenge_id": challenge_id,
            "pass_phrase": phrase,
            "instructions": "To verify, POST /certify with creator_id, challenge_id, the "
                            "pass_phrase echoed back, and attestation=true affirming you "
                            "are the human author of work submitted under this id.",
        }
    )


@app.post("/certify")
def certify():
    data = request.get_json(silent=True) or {}
    cert, err = complete_verification(
        creator_id=data.get("creator_id"),
        challenge_id=data.get("challenge_id"),
        phrase_response=data.get("pass_phrase"),
        attestation=bool(data.get("attestation")),
    )
    if err:
        return jsonify({"error": err}), 400
    return jsonify(
        {
            "message": "Verification complete. This creator now holds a Provenance "
                       "Certificate and their content will display a Verified Human badge.",
            "certificate": cert,
            "verified_label": build_verified_label(cert),
        }
    )


# --------------------------------------------------------- Analytics (stretch feature)


@app.get("/analytics")
def analytics():
    return jsonify(compute_metrics())


@app.get("/dashboard")
def dashboard():
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.get("/log")
def log():
    return jsonify({"entries": get_log()})


@app.errorhandler(429)
def ratelimit_handler(e):
    return (
        jsonify({"error": "Rate limit exceeded.", "detail": str(e.description),
                 "limit": SUBMIT_LIMITS}),
        429,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
