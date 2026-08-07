"""
Voice of Customer Analyst.

Questions this agent answers:
- Sentiment and common complaints across Arabic and English reviews
- Correlation between rating and timing/item where possible

Data contract (from Person 1's parsers/json_parser.py):
    customer_reviews.csv columns: record_id, date, source_platform,
    rating, text, language ("ar" or "en", auto-detected by character ratio)

Design note: sentiment/theme extraction from free text is a genuine
language-understanding task -- this agent uses the LLM for that (batched,
not one call per review), while rating statistics are plain pandas run
through the shared subprocess runner like the other analysts.
"""
import json
from typing import TYPE_CHECKING
import pandas as pd

from agents._code_runner import run_self_correcting_code
from load_real_data import load_source_for_state

if TYPE_CHECKING:
    from state import CafeState


_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        from langchain_google_genai import ChatGoogleGenerativeAI
        _llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite")
    return _llm


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


def summarize_low_rated_themes(reviews: pd.DataFrame, max_reviews: int = 40) -> dict:
    """
    Sends low-rated reviews (rating <= 2, both languages) to the model in
    ONE batched call to identify recurring complaint themes -- this is a
    real understanding task (bilingual, free text), unlike the rating
    math below which is plain arithmetic.
    """
    low = reviews[reviews["rating"] <= 2].head(max_reviews)
    if low.empty:
        return {"themes": [], "note": "no low-rated reviews to analyze"}

    reviews_text = "\n".join(
        f"[{row['language']}, rating={row['rating']}] {row['text']}"
        for _, row in low.iterrows()
    )
    prompt = (
        "These are low-rated (1-2 star) customer reviews for a cafe, in "
        "Arabic and English. Identify the top 3 recurring complaint themes. "
        "Respond with JSON only:\n"
        '[{"theme": "<short label>", "review_count": <int>, '
        '"example_quote": "<one representative quote, original language>"}]\n\n'
        f"Reviews:\n{reviews_text}"
    )
    response = _get_llm().invoke(prompt)
    try:
        themes = json.loads(_extract_text(response.content).strip())
    except json.JSONDecodeError:
        themes = []
    return {"themes": themes, "reviews_analyzed": len(low)}


REVIEWS_CODE_TEMPLATE = '''
import pandas as pd
import json

reviews = pd.read_json("{reviews_path}")
reviews["date"] = pd.to_datetime(reviews["date"], errors="coerce")

overall_avg = reviews["rating"].mean()

by_lang = reviews.groupby("language")["rating"].agg(["mean", "count"])
lang_stats = {{
    lang: {{"avg_rating": round(row["mean"], 2), "count": int(row["count"])}}
    for lang, row in by_lang.iterrows()
}}

by_source = reviews.groupby("source_platform")["rating"].mean().round(2).to_dict() \
    if "source_platform" in reviews.columns else {{}}

low_rated_pct = (reviews["rating"] <= 2).mean() * 100

result = {{
    "overall_avg_rating": round(float(overall_avg), 2) if pd.notna(overall_avg) else None,
    "total_reviews": int(len(reviews)),
    "by_language": lang_stats,
    "by_source_platform": {{k: float(v) for k, v in by_source.items()}},
    "low_rated_pct": round(float(low_rated_pct), 1),
}}
print(json.dumps(result))
'''


def reviews_agent(state: "CafeState") -> dict:
    """Runs rating statistics via subprocess, plus one batched LLM call
    for complaint theme extraction."""
    reviews: pd.DataFrame = load_source_for_state(state, "reviews")

    if reviews.empty:
        return {
            "findings": [{
                "agent": "reviews",
                "claim": "No review data available",
                "number": None,
                "evidence": "customer_reviews source missing or empty",
            }]
        }

    import tempfile
    import os
    tmp_dir = tempfile.gettempdir()
    reviews_path = os.path.join(tmp_dir, "_reviews.json").replace("\\", "/")
    reviews.to_json(reviews_path, orient="records")

    code = REVIEWS_CODE_TEMPLATE.format(reviews_path=reviews_path)
    result = run_self_correcting_code(code, max_fix_attempts=3)

    if not result["ok"]:
        return {
            "findings": [{
                "agent": "reviews",
                "claim": "Review statistics failed after self-correction attempts",
                "number": None,
                "evidence": f"final error: {result['error']} | attempts: {result['attempts_log']}",
            }]
        }

    data = result["data"]
    findings = [
        {
            "agent": "reviews",
            "claim": f"Overall average rating across {data['total_reviews']} reviews",
            "number": data["overall_avg_rating"],
            "evidence": "mean of the rating column",
        },
        {
            "agent": "reviews",
            "claim": "Percentage of reviews rated 1-2 stars",
            "number": data["low_rated_pct"],
            "evidence": "share of rows with rating <= 2",
        },
    ]

    for lang, stats in data["by_language"].items():
        findings.append({
            "agent": "reviews",
            "claim": f"Average rating for {lang}-language reviews",
            "number": stats["avg_rating"],
            "evidence": f"n={stats['count']} reviews in this language",
        })

    theme_result = summarize_low_rated_themes(reviews)
    for theme in theme_result.get("themes", []):
        findings.append({
            "agent": "reviews",
            "claim": f"Recurring complaint theme: {theme['theme']}",
            "number": theme.get("review_count"),
            "evidence": f"example: \"{theme.get('example_quote', '')}\"",
        })

    return {"findings": findings}


if __name__ == "__main__":
    from mock_data.mock_cleaned_data import write_mock_clean_data_dir

    fake_state = {"clean_data_dir": write_mock_clean_data_dir()}
    output = reviews_agent(fake_state)
    for f in output["findings"]:
        print(f)
        print()