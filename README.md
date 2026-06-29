# Provenance Guard

A backend service that any creative-sharing platform (writing, poetry, blogging) can
call to estimate whether a submitted piece of content was written by a human or generated
by AI. Provenance Guard is deliberately **not** a verdict machine: it returns a verdict
*with* a confidence score, surfaces a plain-language transparency label, and gives
creators a path to appeal a classification they believe is wrong.

**Guiding principle — asymmetry of harm.** On a writing platform, falsely labeling a
human's original work as "AI-generated" is far more damaging than failing to catch a
piece of AI text. A false positive can hurt a real creator's reputation and livelihood.
Every design decision here — the wide "uncertain" band, the human-biased verdict
thresholds, the hedged label language, the appeals workflow — is shaped by that
asymmetry. When in doubt, the system says "uncertain," never "AI."

**Includes all four stretch features:** an ensemble of **three** detection signals with a
documented voting strategy, a **Verified-Human provenance certificate**, an **analytics
dashboard**, and **multi-modal (image) support**. See [Stretch features](#stretch-features).

> Full design rationale, the architecture diagram, edge-case analysis, the AI Tool Plan,
> and the stretch-feature pre-work plan live in [`planning.md`](./planning.md).

---

## Architecture

```
 POST /submit ─► [rate limiter] ─► content ─┬─► Signal: LLM semantic (Groq)        ─► ai_score
                                            ├─► Signal: Stylometry (pure Python)   ─► ai_score
                                            └─► Signal: Lexical AI-tells (Python)  ─► ai_score
       (image submissions swap in: image-metadata provenance + LLM-on-caption)
                                                       │  individual signal scores
                                                       ▼
                              [ensemble scorer]  ─► combined_ai_score + confidence
                                                       │
                                                       ▼
                              [label builder]    ─► transparency label  (+ verified badge if certified)
                                                       │
                            ┌──────────────────────────┴──────────────────────────┐
                            ▼                                                       ▼
                       [audit log]                                          [content store]
                            │                                               status=classified
                            ▼
              JSON: content_id, attribution, confidence, signals, label

 POST /appeal ─► [content store] classified ─► under_review ─► [audit log] appeal entry ─► JSON
 GET  /analytics  ◄── aggregate metrics computed from the audit log
```

**Submission flow:** content passes the rate limiter, runs through the signal ensemble
(text or image variant), gets combined into one confidence-scored verdict, is mapped to a
transparency label, recorded in the audit log + content store, and returned as JSON.
**Appeal flow:** a creator submits reasoning against a `content_id`; the content's status
flips to `under_review` and the appeal is logged beside the original decision.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the repo root (it is git-ignored — never commit it):

```
GROQ_API_KEY=your_key_here
```

Run the server:

```bash
python app.py            # serves on http://127.0.0.1:5000
```

> On macOS, prefer `http://127.0.0.1:5000` over `localhost` — port 5000 / IPv6 can be
> intercepted by AirPlay Receiver.

---

## API reference

| Method & path | Purpose |
|---------------|---------|
| `POST /submit` | Classify text (or image metadata). Returns attribution, confidence, **all signal scores**, and the transparency label. **Rate limited.** |
| `POST /appeal` | Contest a classification. Sets status to `under_review` and logs the appeal. |
| `GET /certify/challenge` | Step 1 of earning a Verified-Human certificate (returns a one-time pass-phrase). |
| `POST /certify` | Step 2: complete verification and issue the certificate. |
| `GET /analytics` | Aggregate detection/appeal metrics (JSON). |
| `GET /dashboard` | Minimal HTML analytics view. |
| `GET /log` | Return the structured audit log (newest first). |
| `GET /health` | Liveness check. |

### `POST /submit`

```bash
curl -s -X POST http://127.0.0.1:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "The sun dipped below the horizon, painting the sky in hues of amber and rose.", "creator_id": "test-user-1"}'
```

Returns a structured JSON response with the attribution result, the confidence score, **all
individual signal scores**, the ensemble weights, and the transparency label text:

```json
{
  "content_id": "ce1ea51f-82c2-40c8-ba84-b6bf6d12785e",
  "creator_id": "writer-ben",
  "content_type": "text",
  "attribution": { "verdict": "likely_ai", "combined_ai_score": 0.794, "confidence": 0.606 },
  "confidence": 0.606,
  "label": { "variant": "high_confidence_ai", "title": "🤖 Likely AI-generated", "body": "...", "confidence": 0.606 },
  "signals": {
    "llm": { "ai_score": 0.9, "available": true, "rationale": "Formulaic transitions; lacks specific detail." },
    "stylometry": { "ai_score": 0.548, "reliable": true, "metrics": { "burstiness": 0.402, "...": "..." } },
    "lexical": { "ai_score": 0.9, "matched": ["it is important to note", "furthermore", "..."], "density_per_100w": 18.0 }
  },
  "scoring": { "ensemble_weights": { "llm": 0.5, "stylometry": 0.3, "lexical": 0.2 }, "agreement": 0.648, "notes": [] },
  "status": "classified",
  "timestamp": "2026-06-28T19:02:49.019Z"
}
```

---

## Multi-signal detection pipeline

The pipeline uses **three genuinely independent signals** (the ensemble stretch feature;
the required minimum is two). Each targets a *different property* of the content, so they
fail in different ways — and that independence is what makes their agreement informative.

### Signal 1 — LLM semantic classifier (Groq `llama-3.3-70b-versatile`, `llm_signal.py`)

- **What it measures:** holistic semantic and stylistic coherence — does the text read as
  authentically human? Returns `ai_score` ∈ [0,1] plus a one-sentence rationale.
- **Why this property differs:** AI prose is fluent but *flavorless* — hedge-heavy, rich in
  connective tissue, light on lived specific detail. Human writing carries concrete
  specifics, uneven emphasis, and a distinct voice.
- **What it misses:** it has no ground truth and can be confidently wrong; it is **biased
  against non-native English speakers and formal/academic writers**. It can't reliably
  detect lightly human-edited AI text.

### Signal 2 — Stylometry (pure Python, `stylometry.py`)

- **What it measures:** structural statistics — sentence-length variance ("burstiness"),
  type-token ratio (vocabulary diversity), and punctuation variety.
- **Why this property differs:** AI prose is statistically *uniform* (low burstiness, even
  punctuation); human writing is *bursty* and idiosyncratic.
- **What it misses:** it is **meaning-blind** and **unreliable on short texts** (too few
  data points). Easily gamed by deliberately varying sentence length.

### Signal 3 — Lexical AI-tells (pure Python, `signals_lexical.py`) — *ensemble feature*

- **What it measures:** the *density of formulaic boilerplate phrasing* LLMs over-produce
  ("it is important to note", "furthermore", "delve into", "plays a crucial role", …),
  normalized per 100 words.
- **Why this property differs:** these stock phrases and connective scaffolding appear far
  more in LLM output than in casual or creative human writing.
- **What it misses:** it's shallow — trivially defeated by avoiding the phrases, and it can
  misfire on genuinely formal human writing that happens to use them. That's exactly why
  it's only *one* vote of three.

**Why the trio is strong:** semantic + structural + phrasal are three orthogonal views. A
clever adversary or unusual genuine writer can fool one, rarely all three the same way —
and when they disagree, that disagreement becomes uncertainty rather than a false call.

---

## Confidence scoring with uncertainty

Each signal returns an `ai_score` ∈ [0,1] and a trust weight. Confidence is derived from
how decisively the combined score leans *and* how much the signals agree:

```
combined_ai_score = Σ wᵢ · scoreᵢ                  weights: LLM 0.50, stylometry 0.30, lexical 0.20
agreement         = 1 - (max score − min score)    # dispersion across the ensemble
raw_strength      = min(1.0, |combined_ai_score − 0.5| · 2.5)
confidence        = raw_strength · (0.5 + 0.5 · agreement)
```

**Ensemble voting / conflict resolution.** Conflicts between signals are resolved three
ways: (1) the **weighted vote** (higher-trust signals move the result more, but the two
pure-Python signals together = 0.50 can outvote the LLM); (2) **reliability
down-weighting** — a signal that self-reports unreliable (e.g. stylometry on short text)
has its weight cut to 30% and the weights renormalized; (3) the **dispersion penalty** —
when the signals disagree, confidence drops, pushing the verdict to `uncertain` rather
than forcing a call. If only one signal is available (e.g. Groq down), confidence is capped
at 0.50 so a lone signal can never make a confident call.

**Verdict bands (deliberately human-biased).** Confidence `0.60` is the decision boundary:
below it the verdict is always `uncertain`, regardless of which side of 0.5 the score
leans. `0.60` is the weakest call we'll publish; `0.95` is near-certain.

| Condition | Verdict | Label |
|-----------|---------|-------|
| `confidence ≥ 0.60` and `combined ≥ 0.65` | `likely_ai` | High-confidence AI |
| `confidence ≥ 0.60` and `combined ≤ 0.35` | `likely_human` | High-confidence human |
| otherwise | `uncertain` | Uncertain |

### Two real submissions with different confidence

Actual results from the test pipeline (see `calibration_check.py`):

**High-confidence case** — a casual, idiosyncratic human review (all three signals agree):

```
text:   "ok so i finally tried that new ramen place downtown and honestly? underwhelming..."
llm = 0.20   stylo = 0.15   lexical = 0.15   agreement = 0.95
combined = 0.17   →   confidence = 0.79   →   verdict: likely_human   (✍️ Likely human-written)
```

**Lower-confidence case** — a polished, formal human paragraph on monetary policy:

```
text:   "The relationship between monetary policy and asset price inflation has been
         extensively studied in the literature..."
llm = 0.70   stylo = 0.73   lexical = 0.15   agreement = 0.42   (signals disagree)
combined = 0.56   →   confidence = 0.11   →   verdict: uncertain   (❓ Origin unclear)
```

The second case is the asymmetry principle working: the LLM and stylometry both lean AI,
but the lexical signal sees no boilerplate tells (0.15) — that **disagreement** collapses
confidence to 0.11, so a polished human essay is surfaced as "uncertain," never accused.

### How I validated the scores are meaningful

`calibration_check.py` runs the full ensemble on deliberately chosen inputs spanning the
range, printing each signal score next to the combined result, to confirm scores *vary*
and land in the right categories:

| Input | llm | stylo | lex | combined | confidence | verdict |
|-------|-----|-------|-----|----------|------------|---------|
| Clearly human (casual ramen review) | 0.20 | 0.15 | 0.15 | 0.17 | **0.79** | `likely_human` |
| Egregious AI (templated essay) | 0.90 | 0.55 | 0.90 | 0.79 | **0.61** | `likely_ai` |
| Borderline: formal human (monetary policy) | 0.70 | 0.73 | 0.15 | 0.56 | 0.11 | `uncertain` |
| Borderline: lightly-edited AI (remote work) | 0.40 | 0.42 | 0.15 | 0.34 | 0.35 | `uncertain` |

Confidence spans `0.11 → 0.79`, all three verdict categories are reachable, and the two
borderline cases correctly refuse to commit. Adding the lexical signal *strengthened* the
false-positive guard: the formal-human essay's confidence fell from ~0.50 (two signals) to
0.11 (three), because the lexical signal correctly dissented.

**If I were deploying this for real**, I'd calibrate weights and thresholds against a
labeled dataset rather than hand-tuned constants, and I'd track the false-positive rate
specifically — the error that matters most here — rather than overall accuracy.

---

## Transparency label

Plain language — no "score," "classifier," or "logit." The wording differs by confidence
level (different *text*, not just a number), and the AI variant is deliberately hedged.

| Variant | Title | Body text (verbatim) |
|---------|-------|----------------------|
| **High-confidence AI** | 🤖 Likely AI-generated | "Our analysis found strong signals that this text was generated by AI. Both how it reads and its writing patterns point this way. This is an automated estimate, not a certainty — the creator can appeal if they believe it's wrong." |
| **Uncertain** | ❓ Origin unclear | "We couldn't confidently tell whether this text was written by a person or generated by AI. Our signals were weak or disagreed with each other, so we're not making a call. Treat this as undetermined." |
| **High-confidence human** | ✍️ Likely human-written | "Our analysis found strong signals that a person wrote this text. How it reads and its writing patterns are both consistent with human authorship. This is an automated estimate, not a guarantee." |

A certified creator's content additionally carries a distinct Verified-Human badge — see
[Provenance certificate](#2-provenance-certificate-verified-human).

---

## Appeals workflow

A creator who believes a verdict is wrong appeals against the `content_id` from their
`/submit` response:

```bash
curl -s -X POST http://127.0.0.1:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "ce1ea51f-...", "creator_reasoning": "I wrote this myself. I am a non-native English speaker and my style may appear more formal than typical."}'
```

On receipt the system: (1) looks up the content (`404` if unknown), (2) flips its status
`classified → under_review`, (3) appends an **appeal entry** to the audit log carrying the
creator's reasoning and a reference to the original decision (attribution + confidence) so
a reviewer sees both together, and (4) returns a confirmation. Reasoning is required (`400`
if missing). Automated re-classification is intentionally out of scope — a human moderator
resolves the review.

```json
{
  "content_id": "ce1ea51f-82c2-40c8-ba84-b6bf6d12785e",
  "appeal_id": "daad14ca-99ea-45b7-b0cb-828e517a89f1",
  "status": "under_review",
  "message": "Appeal received. This content is now under review by a human moderator.",
  "timestamp": "2026-06-28T19:03:10.123Z"
}
```

---

## Rate limiting

`POST /submit` is rate-limited per client (Flask-Limiter) at:

```
10 per minute ; 100 per hour ; 500 per day
```

**Reasoning — tied to realistic writing-platform usage:**

- **10/minute** — a writer drafting and re-submitting revisions might fire several requests
  in a burst; 10 covers that while making a tight scripted flood impossible. A bot scraping
  verdicts at machine speed hits this instantly.
- **100/hour** — a heavy, sustained editing session (resubmitting a piece dozens of times as
  it's revised). Well above genuine human cadence over an hour, so it blocks slow-drip abuse
  that stays under the per-minute cap.
- **500/day** — a platform-level ceiling per client. No real creator submits 500 distinct
  pieces a day; this caps a determined abuser's daily volume without constraining a user.

Layered so a burst, a sustained session, and a long-running script are each caught by a
different limit.

**Rate limiting in action** — a burst of 12 requests (limit is 10/min):

```
req 1  -> 200      req 5  -> 200      req 9  -> 200
req 2  -> 200      req 6  -> 200      req 10 -> 200
req 3  -> 200      req 7  -> 200      req 11 -> 429   ◄── limit hit
req 4  -> 200      req 8  -> 200      req 12 -> 429
```

The `429` body: `{ "error": "Rate limit exceeded.", "detail": "10 per 1 minute", "limit": "10 per minute;100 per hour;500 per day" }`

---

## Audit log

Every decision, appeal, and certification is recorded in a structured SQLite-backed log
(full JSON payload per entry), exposed via `GET /log` (newest first). Each classification
entry captures the timestamp, content ID, content type, attribution, confidence, **all
individual signal scores**, the combined score, the ensemble weights, and any notes.
Appeal entries sit alongside the decision they contest.

Sample log (showing an appeal next to its classification, a multi-modal image entry, and a
certification — abbreviated):

```json
[
  {
    "type": "appeal",
    "appeal_id": "daad14ca-99ea-45b7-b0cb-828e517a89f1",
    "content_id": "ce1ea51f-82c2-40c8-ba84-b6bf6d12785e",
    "creator_id": "writer-ben",
    "timestamp": "2026-06-28T19:03:10.123Z",
    "status": "under_review",
    "appeal_reasoning": "I wrote this myself; I am a non-native English speaker, so my style may read as formal.",
    "original_attribution": "likely_ai",
    "original_confidence": 0.606
  },
  { "type": "certification", "creator_id": "writer-anna", "certificate_id": "c2ae30f9-...", "method": "challenge-response", "timestamp": "2026-06-28T19:03:05.000Z" },
  {
    "type": "classification",
    "content_id": "ce1ea51f-82c2-40c8-ba84-b6bf6d12785e",
    "creator_id": "writer-ben",
    "content_type": "text",
    "timestamp": "2026-06-28T19:02:49.019Z",
    "attribution": "likely_ai",
    "confidence": 0.606,
    "combined_ai_score": 0.794,
    "agreement": 0.648,
    "signals": {
      "llm": { "ai_score": 0.9, "available": true, "rationale": "Formulaic transitions; lacks specific detail." },
      "stylometry": { "ai_score": 0.548, "reliable": true, "metrics": { "burstiness": 0.402, "type_token_ratio": 0.86, "punctuation_variety": 0, "avg_sentence_length": 8.33 } },
      "lexical": { "ai_score": 0.9, "matched": ["it is important to note", "in conclusion", "furthermore", "moreover", "firstly", "secondly", "therefore"], "density_per_100w": 18.0 }
    },
    "weights": { "llm": 0.5, "stylometry": 0.3, "lexical": 0.2 },
    "creator_verified": false,
    "status": "classified"
  },
  {
    "type": "classification",
    "content_id": "4c705cef-d9ef-49f5-a64c-7e06c27970e8",
    "creator_id": "artist-x",
    "content_type": "image",
    "timestamp": "2026-06-28T19:02:49.533Z",
    "attribution": "likely_ai",
    "confidence": 0.855,
    "combined_ai_score": 0.86,
    "agreement": 0.9,
    "signals": {
      "image_metadata": { "ai_score": 0.9, "reliable": true, "reasons": ["Generator/software field names an AI tool: midjourney."], "checked": { "software": "midjourney v6", "has_camera_exif": false } },
      "llm_caption": { "ai_score": 0.8, "available": true, "rationale": "Formulaic, list-like; lacks specific personal detail." }
    },
    "weights": { "image_metadata": 0.6, "llm_caption": 0.4 },
    "creator_verified": false,
    "status": "classified"
  }
]
```

The appeal entry (top) references the `likely_ai @ 0.606` classification it contests, so a
reviewer opening the queue sees the original verdict, all signal scores, and the creator's
reasoning in one place.

---

## Stretch features

All four stretch features are implemented and demonstrated above and below.

### 1. Ensemble detection (3 signals + voting)

Three distinct signals — **LLM semantic** (0.50), **stylometry** (0.30), and **lexical
AI-tells** (0.20) — described in [the pipeline section](#multi-signal-detection-pipeline).
The **weighting / voting strategy** and how **conflicts between signals are resolved**
(weighted vote → reliability down-weighting → dispersion penalty) are documented in
[Confidence scoring](#confidence-scoring-with-uncertainty). Every `/submit` response and
audit entry shows the **individual signal scores alongside the ensemble result** (see the
sample JSON above — `signals` + `scoring.ensemble_weights`).

### 2. Provenance certificate ("Verified Human")

**Design.** A certificate attests that a *creator* completed human verification — it
vouches for the person, not a specific text. It's shown *alongside* the detection label,
never instead of it, so a verified credential is never confused with an automated guess.

**Verification step** (two-step challenge–response, proving a live human acted):

```bash
# Step 1 — request a one-time pass-phrase
curl -s "http://127.0.0.1:5000/certify/challenge?creator_id=writer-anna"
# → { "challenge_id": "...", "pass_phrase": "harbor-ember-saffron", ... }

# Step 2 — echo the phrase back + affirm authorship attestation
curl -s -X POST http://127.0.0.1:5000/certify -H "Content-Type: application/json" \
  -d '{"creator_id":"writer-anna","challenge_id":"...","pass_phrase":"harbor-ember-saffron","attestation":true}'
# → issues a certificate
```

Afterwards, that creator's `/submit` responses include a **Verified-Human badge distinct
from the standard label**:

```
detection label:  ✍️ Likely human-written          ← automated estimate about the text
verified_label:   ✅ Verified Human  (cert c2ae30f9-...) ← credential vouching for the creator
```

The badge text: *"This creator completed Provenance Guard's human-verification step, so
their authorship is confirmed — this is a verified credential, not an automated guess."*
(A real deployment would swap the challenge for an OAuth/identity-provider check.)

### 3. Analytics dashboard

`GET /analytics` returns aggregate metrics from the audit log; `GET /dashboard` renders
them as a simple HTML view. It reports **five** metrics (≥3 required):

```json
{
  "total_classifications": 3,
  "detection_pattern": { "likely_ai": 2, "likely_human": 1, "uncertain": 0, "ai_to_human_ratio": 2.0 },
  "appeal_rate": 0.333,
  "average_confidence": 0.73,
  "uncertain_rate": 0.0,
  "verified_creators": 1
}
```

— **detection pattern** (AI vs. human ratio), **appeal rate**, plus average confidence,
uncertain rate, and verified-creator count.

### 4. Multi-modal support (images)

`POST /submit` accepts `content_type: "image"` with structured `metadata` and/or a
`caption`. The pipeline swaps in image-appropriate signals: a **metadata-provenance**
check (AI-generator tags, C2PA flags, camera-EXIF presence — the structural analogue of
stylometry) plus the **LLM applied to the caption** (the semantic analogue). Both feed the
same ensemble scorer, so confidence and labels work identically across modalities.

```bash
# AI-generated image (generator named in metadata)
curl -s -X POST http://127.0.0.1:5000/submit -H "Content-Type: application/json" \
  -d '{"content_type":"image","metadata":{"software":"Midjourney v6"},"caption":"hyperrealistic astronaut riding a horse on mars, octane render"}'
# → likely_ai, conf 0.855  (image_metadata 0.9, llm_caption 0.8)

# Real photo (camera EXIF present)
curl -s -X POST http://127.0.0.1:5000/submit -H "Content-Type: application/json" \
  -d '{"content_type":"image","metadata":{"make":"Canon","model":"EOS R5"},"caption":"my daughter blowing out birthday candles, slightly blurry"}'
# → likely_human, conf 0.75  (image_metadata 0.2 — "Camera EXIF present")
```

---

## Known limitations

**Formal or non-native-English human writing that *also* uses connective phrasing is the
content type this system is most likely to get wrong** — pushing it toward a false AI
verdict. This is tied directly to the signals:

- **Stylometry** keys on uniformity: formal/academic prose and the careful, evenly
  structured writing common among non-native English speakers has low burstiness and sparse
  punctuation — the exact fingerprint it reads as AI. In testing it rated a genuine formal
  human essay (0.73) as *more* AI-like than a real AI essay.
- **The LLM** shares that bias: clean, hedge-free text reads as "AI-like" to the model too.
- The **lexical signal** is the usual corrective (it dissents when no boilerplate is
  present — which is what saves the monetary-policy example). But a formal human who *does*
  write "Furthermore… Moreover… In conclusion…" trips **all three** signals at once, and
  the ensemble's agreement-based confidence would then rise toward a false `likely_ai`. The
  three signals are independent in the common case but can correlate on exactly this kind of
  writing — the worst case for an agreement-based scorer.

The defense is structural, not perfect: human-biased thresholds and the wide "uncertain"
band keep most such writing on `uncertain`, and the appeals + certificate paths exist for
what slips through. A second weakness: **very short submissions** (a two-line poem) starve
stylometry of data points, so the system leans on the LLM and reports low confidence —
honest, but not very useful alone.

---

## Spec reflection

**How the spec helped.** Writing `planning.md` before any code meant the API response
shape, the three label variants, and the verdict thresholds were settled in advance. Each
build milestone filled in fields rather than reshaping the contract — the M3 `/submit`
response already had `confidence` and `label` slots that M4/M5 simply populated, and the
ensemble (3rd signal) and multi-modal pipeline slotted into the same `signals` structure
without a rewrite. The verbatim label text was written once, reviewed for plain language,
and implemented exactly.

**How the implementation diverged.** The spec defined confidence as
`raw_strength * agreement` with `raw_strength = |combined − 0.5| * 2`. Running the pipeline
(M4) showed this compressed the scale badly: the LLM almost never returns extreme
probabilities, so realistic "clearly human" text only reached a combined score around 0.2,
and multiplying two sub-1 factors meant even obvious cases couldn't clear the 0.60 floor —
a clearly-human submission scored *uncertain*. I diverged by **steepening** `raw_strength`
to `min(1.0, |combined − 0.5| * 2.5)` and **softening** the agreement penalty to
`(0.5 + 0.5 * agreement)`, keeping the `0.60` decision boundary unchanged so the
user-facing meaning of confidence still holds. (When the ensemble grew to three signals,
`agreement` also generalized from a pairwise difference to the score *dispersion*
`max − min`.) The divergence is annotated in `scoring.py`. The lesson: a confidence formula
is a calibration decision you can't fully make on paper — you have to see real outputs.

---

## AI usage

I used Claude Code as the AI tool throughout, feeding it sections of `planning.md` plus the
architecture diagram as context (per the AI Tool Plan).

**1. Generating the confidence-scoring logic.** I directed the AI to implement the
combination formula and verdict bands from my uncertainty section. It produced a clean
`combine_signals()` faithful to the spec — `confidence = raw_strength * agreement`. I then
ran my own calibration harness and found the output *wrong in practice*: a clearly-human
ramen review came back `uncertain` because the formula compressed the scale. I **overrode**
the AI's faithful-to-spec version, steepening `raw_strength` (×2.5, clamped) and softening
the agreement penalty, and re-validated until clear cases earned confident labels while
borderline cases stayed uncertain. The AI implemented what I specified; I had to recognize
the spec itself needed recalibration.

**2. Designing the third (lexical) ensemble signal.** I directed the AI to add a third
signal for the ensemble feature. Its first instinct was an n-gram repetition / perplexity
proxy — but I **overrode** that because it overlapped with stylometry's type-token ratio
(both measure lexical variety), which would have made the "ensemble" two views of the same
property. I redirected it to a **lexical AI-tell phrase detector** instead — a genuinely
orthogonal signal (phrasing, not structure or semantics) — and I curated the phrase list
and tuned the density→score mapping myself. Calibration then confirmed it *improved* the
false-positive guard (the formal-human case dropped from conf ~0.50 to 0.11), validating
the choice.

---

## Project structure

```
app.py                Flask API: /submit, /appeal, /certify, /analytics, /dashboard, /log
stylometry.py         Signal — structural heuristics (pure Python)
llm_signal.py         Signal — semantic classification via Groq
signals_lexical.py    Signal — lexical AI-tell detector (ensemble feature)
signals_image.py      Multi-modal — image-metadata provenance signal
scoring.py            Ensemble scorer: weighted vote, dispersion penalty, verdict bands
labels.py             Transparency labels (3 variants) + Verified-Human badge
certificates.py       Provenance certificate: challenge–response verification
analytics.py          Analytics metrics + HTML dashboard
audit.py              SQLite audit log, content store, certificates, challenges
calibration_check.py  Validation harness for the confidence scorer
planning.md           Design doc: architecture, signals, thresholds, AI & stretch plans
```
