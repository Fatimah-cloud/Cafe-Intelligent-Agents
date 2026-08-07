"""
csv_parser.py — normalizes every CSV source into NormalizedRecord.

No cleaning happens here (no dedup, no repair, no dropping bad rows).
Ingestion's only job is: read the file, don't crash, and pass along
exactly what's in it (including the messiness) plus row-level parse errors.
Cleaning is a separate stage (cleaning/cleaner.py) that decides what to
do about that messiness — and reports what it did.
"""

import csv
import os
from parsers.base import NormalizedRecord, ParseResult


def parse_csv_source(source_name: str, file_path: str) -> ParseResult:
    result = ParseResult(source=source_name)

    if not os.path.exists(file_path):
        result.fatal_error = f"file not found: {file_path}"
        return result

    try:
        with open(file_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        result.fatal_error = f"could not read/parse file: {e}"
        return result

    result.rows_in = len(rows)

    dispatch = {
        "pos_transactions": _parse_pos_row,
        "menu_items": _parse_menu_row,
        "foot_traffic": _parse_traffic_row,
        "staff_shifts": _parse_shift_row,
    }
    row_parser = dispatch.get(source_name)
    if row_parser is None:
        result.fatal_error = f"no row parser registered for source '{source_name}'"
        return result

    for i, row in enumerate(rows):
        try:
            rec = row_parser(row, i)
            result.records.append(rec)
        except Exception as e:
            result.errors.append(f"row {i}: {e}")

    result.rows_out = len(result.records)
    return result


def _parse_pos_row(row: dict, idx: int) -> NormalizedRecord:
    ts = (row.get("timestamp") or "").strip()
    date_part, time_part = _split_pos_timestamp(ts)
    rec_id = f"{row.get('transaction_id','?')}-{idx}"
    return NormalizedRecord(
        source="pos_transactions",
        record_id=rec_id,
        date=date_part,
        time=time_part,
        attrs={
            "transaction_id": row.get("transaction_id"),
            "sku": row.get("sku"),
            "item_name": row.get("item_name"),
            "quantity": _to_float(row.get("quantity")),
            "unit_price_sar": _to_float(row.get("unit_price_sar")),
            "discount_sar": _to_float(row.get("discount_sar")),
            "line_total_sar": _to_float(row.get("line_total_sar")),
            "payment_method": row.get("payment_method") or None,
            "channel": row.get("channel") or None,
            "cashier_id": row.get("cashier_id") or None,
        },
        raw=row,
    )


def _split_pos_timestamp(ts: str) -> tuple[str | None, str | None]:
    """
    Two known formats in this source:
      'YYYY-MM-DD HH:MM:SS'   (majority)
      'DD-Mon-YYYY HH:MM'     (~1.5% of rows)
    We only SPLIT here, not reconcile — reconciliation into one
    ISO format happens explicitly in cleaning, so it's visible in the
    data-quality report rather than silently fixed at read time.
    """
    if not ts:
        return None, None
    if "-" in ts and ts[:4].isdigit():
        # YYYY-MM-DD HH:MM:SS
        parts = ts.split(" ")
        return parts[0], parts[1] if len(parts) > 1 else None
    # DD-Mon-YYYY HH:MM  -> keep as raw date string, cleaning will reparse
    parts = ts.split(" ")
    return parts[0], parts[1] if len(parts) > 1 else None


def _parse_menu_row(row: dict, idx: int) -> NormalizedRecord:
    return NormalizedRecord(
        source="menu_items",
        record_id=row.get("sku", f"menu-{idx}"),
        date=row.get("launch_date") or None,
        attrs={
            "sku": row.get("sku"),
            "item_en": row.get("item_en"),
            "item_ar": row.get("item_ar"),
            "category": row.get("category"),
            "price_sar": _to_float(row.get("price_sar")),
            "unit_cost_sar": _to_float(row.get("unit_cost_sar")),
            "is_iced": (row.get("is_iced") or "").strip().lower() == "true",
            "launch_date": row.get("launch_date") or None,
            "retire_date": row.get("retire_date") or None,
        },
        raw=row,
    )


def _parse_traffic_row(row: dict, idx: int) -> NormalizedRecord:
    return NormalizedRecord(
        source="foot_traffic",
        record_id=f"{row.get('date')}-{row.get('hour')}",
        date=row.get("date"),
        time=f"{int(row['hour']):02d}:00" if row.get("hour") not in (None, "") else None,
        attrs={
            "hour": _to_int(row.get("hour")),
            "door_count": _to_int(row.get("door_count")),
        },
        raw=row,
    )


def _parse_shift_row(row: dict, idx: int) -> NormalizedRecord:
    return NormalizedRecord(
        source="staff_shifts",
        record_id=f"{row.get('employee_id')}-{row.get('date')}-{idx}",
        date=row.get("date"),
        time=row.get("shift_start") or None,
        attrs={
            "employee_id": row.get("employee_id"),
            "name": row.get("name"),
            "role": row.get("role"),
            "shift_start": row.get("shift_start"),
            "shift_end": row.get("shift_end"),
            "hours": _to_float(row.get("hours")),
            "hourly_rate_sar": _to_float(row.get("hourly_rate_sar")),
        },
        raw=row,
    )


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _to_int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None
