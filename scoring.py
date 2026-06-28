"""Confidence scorer — combines the two signals into one calibrated result.

Implements the formula and verdict bands from planning.md
("Confidence scoring & uncertainty representation") exactly:

    combined_ai_score = w_llm * llm_ai_score + w_stylo * stylo_ai_score
    agreement         = 1 - |llm_ai_score - stylo_ai_score|
    raw_strength      = min(1.0, |combined_ai_score - 0.5| * 2.5)   # see calibration note
    confidence        = raw_strength * (0.5 + 0.5 * agreement)      # see calibration note

Calibration note (divergence from the original planning.md formula, kept deliberately
so the README spec reflection can document it): the spec defined
`raw_strength = |combined - 0.5| * 2` and `confidence = raw_strength * agreement`.
Empirically that compressed the scale badly — the LLM almost never returns extreme
probabilities, so realistic "clearly human" text only reaches a combined score around
0.2, and multiplying two sub-1 factors meant even obvious cases couldn't clear the 0.60
floor. Two adjustments fix this WITHOUT moving the 0.60 decision boundary (so the
"what 0.6 means" story in planning.md still holds):
  1. Steepen strength: `min(1.0, |combined - 0.5| * 2.5)` so the confidence scale uses
     its full range for realistic inputs.
  2. Soften the agreement penalty: `(0.5 + 0.5 * agreement)` — full agreement leaves
     confidence untouched, total disagreement halves it (rather than zeroing it).
The false-positive guard is preserved: the formal-human and academic-AI samples (both
~0.70 combined) still land below 0.60 -> "uncertain", while genuinely clear, agreeing
cases on either side now earn a confident label.

Verdict bands (deliberately human-biased — it takes a strong, confident signal to
call something AI; the middle is always "uncertain"):

    confidence >= 0.60 and combined >= 0.65  -> likely_ai     (high_confidence_ai)
    confidence >= 0.60 and combined <= 0.35  -> likely_human  (high_confidence_human)
    otherwise                                -> uncertain     (uncertain)

Two adjustments to the base weights (0.65 LLM / 0.35 stylometry):

* If stylometry is unreliable (very short text), shift weight to the LLM (0.85 / 0.15)
  so a noisy structural score doesn't dominate.
* If the LLM signal is unavailable, fall back to stylometry alone with confidence
  capped at 0.50 — a single structural signal should never produce a high-confidence
  call. The verdict then can never exceed "uncertain" in practice.
"""

# Base weights when both signals are present and stylometry is reliable.
W_LLM_BASE, W_STYLO_BASE = 0.65, 0.35
# Weights when stylometry is unreliable (short text): lean on the LLM.
W_LLM_SHORT, W_STYLO_SHORT = 0.85, 0.15

CONFIDENCE_FLOOR = 0.60   # minimum certainty to make any directional call
AI_BAND = 0.65            # combined score at/above this leans AI
HUMAN_BAND = 0.35         # combined score at/below this leans human
FALLBACK_CONF_CAP = 0.50  # cap when only stylometry is available


def _verdict(combined, confidence):
    if confidence >= CONFIDENCE_FLOOR and combined >= AI_BAND:
        return "likely_ai", "high_confidence_ai"
    if confidence >= CONFIDENCE_FLOOR and combined <= HUMAN_BAND:
        return "likely_human", "high_confidence_human"
    return "uncertain", "uncertain"


def combine_signals(llm, stylo):
    """Combine the LLM and stylometry signal dicts into a scored result.

    Args:
        llm:   {"available": bool, "ai_score": float|None, "rationale": str}
        stylo: {"ai_score": float, "reliable": bool, "metrics": {...}}

    Returns a dict with combined_ai_score, confidence, agreement, verdict, the chosen
    label variant key, the weights used, and human-readable notes.
    """
    stylo_score = stylo["ai_score"]
    stylo_reliable = stylo["reliable"]
    llm_available = llm.get("available", False)
    llm_score = llm.get("ai_score")
    notes = []

    if not llm_available:
        # Fallback: stylometry only, confidence capped so it can't make a strong call.
        combined = stylo_score
        raw_strength = abs(combined - 0.5) * 2
        confidence = round(min(raw_strength, FALLBACK_CONF_CAP), 3)
        agreement = None
        weights = {"llm": 0.0, "stylometry": 1.0}
        notes.append(
            "LLM signal unavailable; stylometry-only fallback with confidence "
            f"capped at {FALLBACK_CONF_CAP}."
        )
        verdict, variant = _verdict(combined, confidence)
        return {
            "combined_ai_score": round(combined, 3),
            "confidence": confidence,
            "agreement": agreement,
            "verdict": verdict,
            "label_variant": variant,
            "weights": weights,
            "notes": notes,
        }

    # Both signals present.
    if stylo_reliable:
        w_llm, w_stylo = W_LLM_BASE, W_STYLO_BASE
    else:
        w_llm, w_stylo = W_LLM_SHORT, W_STYLO_SHORT
        notes.append("Stylometry unreliable on short text; LLM up-weighted.")

    combined = w_llm * llm_score + w_stylo * stylo_score
    agreement = 1 - abs(llm_score - stylo_score)
    # Calibrated strength + softened agreement penalty (see module docstring note).
    raw_strength = min(1.0, abs(combined - 0.5) * 2.5)
    confidence = raw_strength * (0.5 + 0.5 * agreement)

    if agreement < 0.6:
        notes.append("Signals disagree; confidence reduced and verdict pulled toward uncertain.")

    verdict, variant = _verdict(combined, confidence)
    return {
        "combined_ai_score": round(combined, 3),
        "confidence": round(confidence, 3),
        "agreement": round(agreement, 3),
        "verdict": verdict,
        "label_variant": variant,
        "weights": {"llm": w_llm, "stylometry": w_stylo},
        "notes": notes,
    }
