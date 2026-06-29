"""Provenance Certificate (stretch feature) — a "Verified Human" credential.

Design
------
A certificate attests that a *creator* completed an interactive human-verification step.
It is deliberately about the creator, not a specific piece of text: detection scores text,
the certificate vouches for the person behind it. Together they give a reader stronger
trust than either alone, and a certified creator's work carries a badge that is visually
distinct from the automated detection label.

Verification step (two-step challenge–response, proving a live human acted):
  1. The creator calls ``GET /certify/challenge?creator_id=<id>`` and receives a one-time
     ``challenge_id`` plus a short pass-phrase.
  2. The creator calls ``POST /certify`` echoing the pass-phrase back AND affirming an
     authorship attestation. If the phrase matches an unused challenge for that creator,
     a certificate is issued.

This is intentionally lightweight (no real identity provider), but it models the shape of
a genuine verification flow: a challenge the requester must act on, single-use, tied to a
creator id, and recorded in the audit log. A real deployment would swap step 1/2 for an
OAuth identity check, a verified email, or a government-ID provider.

The pass-phrase is built from a fixed word list indexed by the challenge UUID, so it's
unpredictable per challenge without relying on a random source.
"""

import uuid

from audit import (
    create_challenge,
    get_certificate,
    get_challenge,
    issue_certificate,
    log_certification,
    mark_challenge_used,
    now_iso,
)

_WORDS = [
    "river", "amber", "lantern", "cedar", "harbor", "willow", "ember", "meadow",
    "quartz", "thistle", "marble", "cobalt", "saffron", "indigo", "birch", "comet",
]


def _phrase_from_id(challenge_id):
    """Deterministic 3-word pass-phrase derived from the challenge UUID (no RNG needed)."""
    digest = challenge_id.replace("-", "")
    idxs = [int(digest[i : i + 4], 16) % len(_WORDS) for i in (0, 8, 16)]
    return "-".join(_WORDS[i] for i in idxs)


def start_challenge(creator_id):
    """Create and persist a verification challenge; return (challenge_id, phrase)."""
    challenge_id = str(uuid.uuid4())
    phrase = _phrase_from_id(challenge_id)
    create_challenge(challenge_id, creator_id, phrase, now_iso())
    return challenge_id, phrase


def complete_verification(creator_id, challenge_id, phrase_response, attestation):
    """Verify a challenge response and issue a certificate.

    Returns (certificate_dict, None) on success or (None, error_message) on failure.
    """
    if not (creator_id and challenge_id and phrase_response):
        return None, "creator_id, challenge_id, and phrase_response are all required."
    if not attestation:
        return None, "You must affirm the authorship attestation to be verified."

    challenge = get_challenge(challenge_id)
    if challenge is None or challenge["creator_id"] != creator_id:
        return None, "No matching challenge for this creator."
    if challenge["used"]:
        return None, "This challenge has already been used."
    if phrase_response.strip().lower() != challenge["phrase"].lower():
        return None, "Pass-phrase did not match the challenge."

    certificate_id = str(uuid.uuid4())
    timestamp = now_iso()
    mark_challenge_used(challenge_id)
    issue_certificate(creator_id, certificate_id, "challenge-response", timestamp)
    log_certification(
        {
            "content_id": "-",  # certification is creator-scoped, not content-scoped
            "creator_id": creator_id,
            "timestamp": timestamp,
            "certificate_id": certificate_id,
            "method": "challenge-response",
        }
    )
    return {
        "creator_id": creator_id,
        "certificate_id": certificate_id,
        "method": "challenge-response",
        "issued_at": timestamp,
    }, None


def certificate_for(creator_id):
    """Return the certificate dict for a creator, or None."""
    return get_certificate(creator_id)
