"""Signal 2 in the spec, but the *first* signal implemented (Milestone 3).

Stylometric heuristics: a pure-Python, dependency-free, deterministic estimate of
how "AI-like" a piece of text reads, based purely on its *structure* — never its
meaning. See planning.md ("Detection signals" -> Signal 2) for the rationale and the
documented blind spots (meaning-blind; unreliable on short text).

The public entry point is ``analyze_stylometry(text)`` which returns a dict:

    {
        "ai_score": float in [0, 1],   # higher = more AI-like
        "reliable": bool,              # False for very short inputs (too few data points)
        "metrics": {
            "burstiness": float,            # coefficient of variation of sentence lengths
            "type_token_ratio": float,      # vocabulary diversity
            "punctuation_variety": int,     # count of distinct "rich" punctuation marks used
            "avg_sentence_length": float,   # mean words per sentence
            "sentence_count": int,
            "word_count": int,
        },
    }
"""

import re
import statistics

# Inputs below these sizes don't have enough data points for stylometry to be
# trustworthy. We still return a score, but flag it as unreliable so the confidence
# scorer (Milestone 4) can down-weight it.
MIN_RELIABLE_WORDS = 40
MIN_RELIABLE_SENTENCES = 3

# "Rich" punctuation that human writers tend to reach for and AI prose tends to use
# more sparingly / uniformly.
RICH_PUNCTUATION = set(";:—–()\"'!?")

_SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s+|$)")
_WORD = re.compile(r"[A-Za-z']+")


def _split_sentences(text):
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    return parts


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def analyze_stylometry(text):
    sentences = _split_sentences(text)
    words = _WORD.findall(text.lower())
    word_count = len(words)
    sentence_count = len(sentences)

    # Words per sentence.
    sentence_lengths = [len(_WORD.findall(s)) for s in sentences] or [word_count]
    avg_len = statistics.fmean(sentence_lengths) if sentence_lengths else 0.0

    # --- Metric 1: burstiness (coefficient of variation of sentence length) ---
    # Human writing alternates short and long sentences (high CV); AI prose is more
    # uniform (low CV).
    if len(sentence_lengths) >= 2 and avg_len > 0:
        cv = statistics.pstdev(sentence_lengths) / avg_len
    else:
        cv = 0.0
    # Map CV -> AI-likeness: CV<=0.2 reads very AI (1.0), CV>=0.6 reads very human (0.0).
    burst_ai = _clamp((0.6 - cv) / 0.4)

    # --- Metric 2: type-token ratio (vocabulary diversity) ---
    ttr = (len(set(words)) / word_count) if word_count else 0.0
    # TTR is length-sensitive, so this gets the smallest weight. Lower diversity reads
    # slightly more AI-like in longer passages.
    ttr_ai = _clamp((0.72 - ttr) / 0.42)

    # --- Metric 3: punctuation variety ---
    variety = len(RICH_PUNCTUATION & set(text))
    # 0 distinct rich marks reads AI-like; 4+ reads human.
    punct_ai = _clamp((4 - variety) / 4)

    # Weighted blend — burstiness is the strongest structural discriminator.
    ai_score = 0.50 * burst_ai + 0.30 * punct_ai + 0.20 * ttr_ai

    reliable = word_count >= MIN_RELIABLE_WORDS and sentence_count >= MIN_RELIABLE_SENTENCES

    return {
        "ai_score": round(ai_score, 3),
        "reliable": reliable,
        "metrics": {
            "burstiness": round(cv, 3),
            "type_token_ratio": round(ttr, 3),
            "punctuation_variety": variety,
            "avg_sentence_length": round(avg_len, 2),
            "sentence_count": sentence_count,
            "word_count": word_count,
        },
    }


if __name__ == "__main__":
    # Quick standalone check — call the signal directly on a few inputs and eyeball
    # that the scores move in the expected direction (Milestone 3 verification step).
    import json

    samples = {
        "bursty_human": (
            "Rain again. I hadn't planned for it, but the sky had other ideas — "
            "great bruised clouds rolling in from the west while I stood there, "
            "umbrella-less, laughing. What else can you do? You get wet. You go home."
        ),
        "uniform_ai": (
            "The weather today is rainy. It is important to bring an umbrella when it "
            "rains. Rain can make you wet if you do not have protection. Therefore, you "
            "should always check the forecast. This will help you stay dry and comfortable."
        ),
        "too_short": "It rained today.",
    }
    for name, txt in samples.items():
        print(f"\n=== {name} ===")
        print(json.dumps(analyze_stylometry(txt), indent=2))
