"""Calibration harness for the confidence scorer (Milestone 4 validation).

Runs the full detection pipeline (both signals + scoring) on a set of deliberately
chosen inputs spanning the confidence range, and prints each signal score separately
alongside the combined result. This is how we validate that scores are *meaningful*:
clearly-AI and clearly-human inputs must land in different label categories, and
borderline inputs must surface as lower-confidence / uncertain rather than forcing a call.

Run:  .venv/bin/python calibration_check.py
"""

from llm_signal import analyze_llm
from scoring import combine_signals
from signals_lexical import analyze_lexical
from stylometry import analyze_stylometry


def _score(text):
    """Run the 3-signal text ensemble and return (llm, stylo, lexical, scored)."""
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
    return llm, stylo, lex, combine_signals(signals)

CASES = {
    "clearly_ai": (
        "Artificial intelligence represents a transformative paradigm shift in modern "
        "society. It is important to note that while the benefits of AI are numerous, it "
        "is equally essential to consider the ethical implications. Furthermore, "
        "stakeholders across various sectors must collaborate to ensure responsible "
        "deployment."
    ),
    "clearly_human": (
        "ok so i finally tried that new ramen place downtown and honestly? underwhelming. "
        "the broth was fine but they put WAY too much sodium in it and i was thirsty for "
        "like three hours after. my friend got the spicy version and said it was better. "
        "probably won't go back unless someone drags me there"
    ),
    "borderline_formal_human": (
        "The relationship between monetary policy and asset price inflation has been "
        "extensively studied in the literature. Central banks face a fundamental tension "
        "between their mandate for price stability and the unintended consequences of "
        "prolonged low interest rates on equity and real estate valuations."
    ),
    "borderline_edited_ai": (
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs — "
        "flexibility and no commute on one side, isolation and blurred work-life "
        "boundaries on the other. Studies show productivity varies widely by individual "
        "and role type."
    ),
}


def main():
    header = (f"{'case':<26}{'llm':>7}{'stylo':>8}{'lex':>7}{'comb':>8}"
              f"{'agree':>8}{'conf':>8}  verdict")
    print(header)
    print("-" * len(header))
    for name, text in CASES.items():
        llm, stylo, lex, scored = _score(text)
        llm_s = llm.get("ai_score")
        print(
            f"{name:<26}"
            f"{(llm_s if llm_s is not None else float('nan')):>7.2f}"
            f"{stylo['ai_score']:>8.2f}"
            f"{lex['ai_score']:>7.2f}"
            f"{scored['combined_ai_score']:>8.2f}"
            f"{(scored['agreement'] if scored['agreement'] is not None else float('nan')):>8.2f}"
            f"{scored['confidence']:>8.2f}"
            f"  {scored['verdict']}"
        )


if __name__ == "__main__":
    main()
