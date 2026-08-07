"""
json_parser.py — normalizes customer_reviews.json (list of review objects).
"""

import json
import os
from parsers.base import NormalizedRecord, ParseResult


def parse_json_source(source_name: str, file_path: str) -> ParseResult:
    result = ParseResult(source=source_name)

    if not os.path.exists(file_path):
        result.fatal_error = f"file not found: {file_path}"
        return result

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        result.fatal_error = f"could not read/parse file: {e}"
        return result

    if not isinstance(data, list):
        result.fatal_error = "expected a JSON list of review objects"
        return result

    result.rows_in = len(data)

    for i, item in enumerate(data):
        try:
            rec = NormalizedRecord(
                source="customer_reviews",
                record_id=item.get("review_id", f"review-{i}"),
                date=item.get("date"),
                attrs={
                    "source_platform": item.get("source"),
                    "rating": item.get("rating"),
                    "text": item.get("text"),
                    "language": _guess_language(item.get("text", "")),
                },
                raw=item,
            )
            result.records.append(rec)
        except Exception as e:
            result.errors.append(f"row {i}: {e}")

    result.rows_out = len(result.records)
    return result


def _guess_language(text: str) -> str:
    if not text:
        return "unknown"
    arabic_chars = sum(1 for ch in text if "\u0600" <= ch <= "\u06FF")
    return "ar" if arabic_chars > len(text) * 0.3 else "en"
