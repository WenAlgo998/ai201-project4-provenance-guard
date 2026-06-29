"""Multi-modal support (stretch feature) — image-metadata provenance signal.

The text pipeline can't judge an image, so for ``content_type: "image"`` submissions the
pipeline swaps in image-appropriate signals (see app.py):

  * Signal A — **metadata provenance** (this module): inspects structured image metadata
    (EXIF-style fields, generator tags, C2PA-style flags) for AI-generation markers. This
    is the image analogue of stylometry: a deterministic, structural check.
  * Signal B — **LLM on the caption/description** (reuses llm_signal): if a human-readable
    caption is supplied, the same semantic signal judges whether the *description* reads
    AI-generated. This is the image analogue of the text LLM signal.

Both feed the same ensemble scorer, so confidence and labels work identically across
modalities.

``analyze_image_metadata(metadata)`` returns ``ai_score`` ∈ [0,1] plus the reasons it
fired, so the result is explainable in the audit log.
"""

# Substrings (lowercased) in software/generator fields that indicate AI image generators.
AI_GENERATOR_MARKERS = [
    "stable diffusion", "stablediffusion", "midjourney", "dall-e", "dall·e", "dalle",
    "adobe firefly", "firefly", "imagen", "flux", "comfyui", "automatic1111",
    "leonardo.ai", "nightcafe", "gan", "diffusion",
]


def analyze_image_metadata(metadata):
    metadata = metadata or {}
    reasons = []
    score = 0.5  # neutral prior

    software = str(metadata.get("software", "") or metadata.get("generator", "")).lower()

    # 1) Explicit AI-generation declaration (C2PA / provenance flag) — strongest signal.
    if metadata.get("ai_generated") is True or metadata.get("c2pa_ai") is True:
        score = 0.95
        reasons.append("Metadata explicitly declares AI generation (C2PA/ai_generated flag).")
        return {"ai_score": 0.95, "reliable": True, "reasons": reasons,
                "checked": {"software": software, "has_camera_exif": _has_camera_exif(metadata)}}

    # 2) Software/generator field names a known AI tool.
    matched = [m for m in AI_GENERATOR_MARKERS if m in software]
    if matched:
        score = 0.9
        reasons.append(f"Generator/software field names an AI tool: {matched[0]}.")

    # 3) Camera EXIF presence: real photos carry make/model/lens; AI images usually don't.
    has_exif = _has_camera_exif(metadata)
    if has_exif:
        score = min(score, 0.2)
        reasons.append("Camera EXIF present (make/model) — consistent with a real photo.")
    elif not matched:
        # No camera fingerprint and no AI tool named: weak lean toward AI, low reliability.
        score = 0.6
        reasons.append("No camera EXIF and no provenance data — weak/ambiguous evidence.")

    # Reliability: explicit markers or camera EXIF are strong; a bare 'no EXIF' guess is not.
    reliable = bool(matched or has_exif)

    return {
        "ai_score": round(score, 3),
        "reliable": reliable,
        "reasons": reasons,
        "checked": {"software": software, "has_camera_exif": has_exif},
    }


def _has_camera_exif(metadata):
    return bool(metadata.get("make") or metadata.get("model") or metadata.get("lens"))
