"""Confidence scorer — combines an ensemble of signals into one calibrated result.

Originally a two-signal weighted blend; extended for the Ensemble Detection stretch
feature to combine an arbitrary number of signals with a documented weighting + voting
strategy (see "Ensemble strategy" below and the README).

Ensemble strategy
-----------------
Each signal contributes an ``ai_score`` ∈ [0,1] and a trust weight. The default
text-pipeline weights reflect how much we trust each signal:

    LLM (semantic)      0.50   — most informative; judges meaning/voice
    Stylometry (struct) 0.30   — independent structural check
    Lexical (phrasing)  0.20   — shallow but precise; AI boilerplate tells

Conflict resolution between signals is handled three ways, in order:
  1. **Weighting (trust vote).** The combined score is a weighted average, so a
     higher-trust signal moves the result more. No single signal can dominate: the two
     pure-Python signals together (0.50) can outvote the LLM (0.50).
  2. **Reliability down-weighting.** A signal that self-reports low reliability (e.g.
     stylometry on very short text) has its weight cut to 30% and the weights are
     renormalized, so a noisy signal doesn't distort the vote.
  3. **Dispersion penalty (the tie-breaker).** When the signals *disagree* — a wide
     spread between the highest and lowest score — confidence is reduced. Disagreement
     therefore pushes the verdict toward "uncertain" rather than forcing a call. This is
     where genuine conflict is surfaced honestly instead of hidden.

If only one signal is available (e.g. the LLM is down), confidence is capped at 0.50 so
a lone signal can never produce a confident verdict.

Confidence formula (see calibration note for the divergence from the original spec):

    combined      = Σ wᵢ·scoreᵢ           (weights renormalized over included signals)
    agreement     = 1 - (max score - min score)        # dispersion across the ensemble
    raw_strength  = min(1.0, |combined - 0.5| * 2.5)
    confidence    = raw_strength * (0.5 + 0.5 * agreement)

Verdict bands (deliberately human-biased): confidence 0.60 is the decision boundary.
    confidence >= 0.60 and combined >= 0.65 -> likely_ai     (high_confidence_ai)
    confidence >= 0.60 and combined <= 0.35 -> likely_human  (high_confidence_human)
    otherwise                               -> uncertain     (uncertain)
"""

# Default trust weights for the text pipeline's three signals.
DEFAULT_WEIGHTS = {"llm": 0.50, "stylometry": 0.30, "lexical": 0.20}
UNRELIABLE_WEIGHT_FACTOR = 0.30   # multiply a signal's weight when it self-reports unreliable

CONFIDENCE_FLOOR = 0.60
AI_BAND = 0.65
HUMAN_BAND = 0.35
FALLBACK_CONF_CAP = 0.50


def _verdict(combined, confidence):
    if confidence >= CONFIDENCE_FLOOR and combined >= AI_BAND:
        return "likely_ai", "high_confidence_ai"
    if confidence >= CONFIDENCE_FLOOR and combined <= HUMAN_BAND:
        return "likely_human", "high_confidence_human"
    return "uncertain", "uncertain"


def combine_signals(signals):
    """Combine an ensemble of signal results.

    Args:
        signals: list of dicts, each:
            {"name": str, "ai_score": float|None, "weight": float,
             "available": bool, "reliable": bool}

    Returns a scored result dict (combined_ai_score, confidence, agreement, verdict,
    label_variant, weights actually used, and notes).
    """
    notes = []
    # Keep only available signals with a numeric score.
    usable = [s for s in signals if s.get("available", True) and s.get("ai_score") is not None]

    if not usable:
        # No signal produced a score at all — refuse to guess.
        return {
            "combined_ai_score": 0.5,
            "confidence": 0.0,
            "agreement": None,
            "verdict": "uncertain",
            "label_variant": "uncertain",
            "weights": {},
            "notes": ["No detection signal was available; cannot classify."],
        }

    # Effective weights: down-weight unreliable signals, then renormalize.
    eff = {}
    for s in usable:
        w = s["weight"]
        if not s.get("reliable", True):
            w *= UNRELIABLE_WEIGHT_FACTOR
            notes.append(f"{s['name']} self-reported unreliable; weight reduced.")
        eff[s["name"]] = w
    total_w = sum(eff.values()) or 1.0
    eff = {name: round(w / total_w, 3) for name, w in eff.items()}

    combined = sum(eff[s["name"]] * s["ai_score"] for s in usable)

    scores = [s["ai_score"] for s in usable]
    if len(scores) >= 2:
        agreement = 1 - (max(scores) - min(scores))   # dispersion across the ensemble
    else:
        agreement = None

    raw_strength = min(1.0, abs(combined - 0.5) * 2.5)
    if agreement is None:
        # Single signal: cap confidence so it can't make a strong call.
        confidence = round(min(raw_strength, FALLBACK_CONF_CAP), 3)
        notes.append(f"Only one signal available; confidence capped at {FALLBACK_CONF_CAP}.")
    else:
        confidence = round(raw_strength * (0.5 + 0.5 * agreement), 3)
        if agreement < 0.6:
            notes.append("Signals disagree; confidence reduced, verdict pulled toward uncertain.")

    verdict, variant = _verdict(combined, confidence)
    return {
        "combined_ai_score": round(combined, 3),
        "confidence": confidence,
        "agreement": round(agreement, 3) if agreement is not None else None,
        "verdict": verdict,
        "label_variant": variant,
        "weights": eff,
        "notes": notes,
    }
