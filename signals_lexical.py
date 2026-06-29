"""Signal 3 (ensemble) — lexical "AI-tell" detector (pure Python).

This is the third, genuinely distinct signal added for the Ensemble Detection stretch
feature. Where stylometry measures *structure* and the LLM judges *meaning/voice*, this
signal measures *phrasing*: the density of formulaic boilerplate words and stock phrases
that modern LLMs over-produce ("it is important to note", "furthermore", "delve into",
"plays a crucial role", "in conclusion", ...).

It returns ``ai_score`` ∈ [0,1] plus the phrases it matched, so the ensemble can show its
contribution. It is deterministic, free, and captures a property neither other signal
targets directly — but it is shallow: it can be defeated by simply avoiding the phrases,
and it can misfire on genuinely formal human writing that happens to use them.
"""

import re

# Stock phrases and transition words LLMs lean on heavily. Multi-word tells are weighted
# more than single connectives because they're stronger indicators.
PHRASE_TELLS = [
    "it is important to note",
    "it is worth noting",
    "in conclusion",
    "in summary",
    "plays a crucial role",
    "plays a significant role",
    "navigate the complexities",
    "delve into",
    "in today's world",
    "in the realm of",
    "a testament to",
    "rich tapestry",
    "ever-evolving",
    "it is essential to",
    "when it comes to",
    "as a result",
    "on the other hand",
]
WORD_TELLS = [
    "furthermore",
    "moreover",
    "additionally",
    "firstly",
    "secondly",
    "thirdly",
    "therefore",
    "consequently",
    "overall",
    "ultimately",
    "notably",
]

_WORD = re.compile(r"[A-Za-z']+")


def analyze_lexical(text):
    lower = text.lower()
    word_count = len(_WORD.findall(text)) or 1

    phrase_hits = [p for p in PHRASE_TELLS if p in lower]
    word_hits = [w for w in WORD_TELLS if re.search(rf"\b{re.escape(w)}\b", lower)]

    # Weighted tell count, normalized to a rate per 100 words so length doesn't dominate.
    weighted = 2.0 * len(phrase_hits) + 1.0 * len(word_hits)
    density = weighted / word_count * 100.0

    # Map density -> AI-likeness. ~0 tells reads human (0.15); a dense cluster reads AI.
    # density of ~3 per 100 words saturates toward the top.
    ai_score = min(0.9, 0.15 + density * 0.22)

    return {
        "ai_score": round(ai_score, 3),
        "matched": phrase_hits + word_hits,
        "density_per_100w": round(density, 2),
    }


if __name__ == "__main__":
    import json

    samples = {
        "ai_ish": "Furthermore, it is important to note that this plays a crucial role. In conclusion, we must delve into the topic. Moreover, as a result, therefore.",
        "human_ish": "i grabbed coffee, sat on the porch, watched the dog chase a squirrel. nothing happened. it was great.",
    }
    for name, txt in samples.items():
        print(name, json.dumps(analyze_lexical(txt)))
