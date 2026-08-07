"""
email_parser.py — normalizes supplier_emails/*.txt

These are unstructured. This parser only extracts the structured
header fields (From / Date / Subject) and keeps the body as text.
Deciding WHICH emails matter (price changes vs noise) is analysis,
not ingestion — that's the Margin analyst's job, not this file's.
"""

import os
from parsers.base import NormalizedRecord, ParseResult


def parse_email_source(source_name: str, dir_path: str) -> ParseResult:
    result = ParseResult(source=source_name)

    if not os.path.isdir(dir_path):
        result.fatal_error = f"directory not found: {dir_path}"
        return result

    try:
        filenames = sorted(f for f in os.listdir(dir_path) if f.endswith(".txt"))
    except Exception as e:
        result.fatal_error = f"could not list directory: {e}"
        return result

    result.rows_in = len(filenames)

    for fname in filenames:
        fpath = os.path.join(dir_path, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            header, body = _split_header_body(content)
            date = header.get("date") or _date_from_filename(fname)
            rec = NormalizedRecord(
                source="supplier_emails",
                record_id=fname,
                date=date,
                attrs={
                    "from": header.get("from"),
                    "subject": header.get("subject"),
                    "body": body.strip(),
                },
                raw={"filename": fname, "content": content},
            )
            result.records.append(rec)
        except Exception as e:
            result.errors.append(f"{fname}: {e}")

    result.rows_out = len(result.records)
    return result


def _split_header_body(content: str) -> tuple[dict, str]:
    lines = content.splitlines()
    header = {}
    body_start = 0
    for i, line in enumerate(lines):
        if ":" in line and line.split(":", 1)[0].strip().lower() in ("from", "date", "subject"):
            key, val = line.split(":", 1)
            header[key.strip().lower()] = val.strip()
        elif line.strip() == "" and header:
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:])
    return header, body


def _date_from_filename(fname: str) -> str | None:
    # filenames look like 2026-02-14_03.txt
    stem = fname.split("_")[0]
    return stem if len(stem) == 10 and stem[4] == "-" else None
