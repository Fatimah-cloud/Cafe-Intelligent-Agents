"""
cleaning/cleaner.py — Module 2: Data Cleaning.

Takes the ParseResults produced by ingestion and turns each source into a
clean pandas DataFrame the analysts can trust. Every fix is counted and
reported — nothing here is a silent correction. If a number in the final
report can't be traced back to something in the quality report, that's a bug.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from parsers.base import ParseResult


@dataclass
class QualityIssue:
    source: str
    issue: str
    count: int
    detail: str = ""


@dataclass
class SourceQualityReport:
    source: str
    rows_in: int
    rows_out: int
    rows_dropped: int = 0
    rows_repaired: int = 0
    issues: list[QualityIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# pos_transactions
# ---------------------------------------------------------------------------

def clean_pos_transactions(pos_result: ParseResult, menu_df: pd.DataFrame) -> tuple[pd.DataFrame, SourceQualityReport]:
    rows = [{**r.attrs, "record_id": r.record_id, "date_raw": r.date, "time_raw": r.time} for r in pos_result.records]
    df = pd.DataFrame(rows)
    report = SourceQualityReport(source="pos_transactions", rows_in=len(df), rows_out=len(df))

    # 1. Reconcile the two timestamp formats into one datetime column.
    df["timestamp"] = df.apply(lambda r: _parse_pos_timestamp(r["date_raw"], r["time_raw"]), axis=1)
    n_alt_format = df["date_raw"].astype(str).str.match(r"^\d{2}-[A-Za-z]{3}-\d{4}$").sum()
    if n_alt_format:
        report.issues.append(QualityIssue(
            "pos_transactions", "alt_timestamp_format_reconciled", int(n_alt_format),
            "DD-Mon-YYYY HH:MM rows parsed and converted to ISO"
        ))
    n_bad_ts = df["timestamp"].isna().sum()
    if n_bad_ts:
        report.issues.append(QualityIssue("pos_transactions", "unparseable_timestamp", int(n_bad_ts)))

    # 2. Deduplicate double-swiped transactions: exact duplicate rows
    #    (same transaction_id, sku, quantity, prices, payment, channel, cashier).
    #    We deliberately do NOT treat two rows with the same sku+qty in the
    #    same transaction_id as duplicates unless every other field also
    #    matches — a customer ordering the same item twice as two lines is
    #    legitimate; a double-swipe reproduces the row byte-for-byte.
    dedup_cols = ["transaction_id", "sku", "quantity", "unit_price_sar", "discount_sar",
                  "line_total_sar", "payment_method", "channel", "cashier_id", "date_raw", "time_raw"]
    before = len(df)
    dup_mask = df.duplicated(subset=dedup_cols, keep="first")
    n_dupes = int(dup_mask.sum())
    df = df[~dup_mask].copy()
    report.rows_dropped += n_dupes
    if n_dupes:
        report.issues.append(QualityIssue(
            "pos_transactions", "double_swipe_duplicate_rows_dropped", n_dupes,
            f"{n_dupes/before:.2%} of rows"
        ))

    # 3. Repair item_name via SKU join — item_name in the source is unreliable
    #    (nulls, Arabic, uppercased-with-trailing-space). menu_items.sku is
    #    always present and correct, so we use it as ground truth.
    n_unreliable = int((df["item_name"].isna() | (df["item_name"].astype(str).str.strip() == "")).sum())
    menu_lookup = menu_df.set_index("sku")[["item_en", "item_ar"]]
    df = df.join(menu_lookup, on="sku", rsuffix="_menu")
    n_repaired = int((df["item_name"].fillna("").str.strip().str.lower()
                      != df["item_en"].fillna("").str.strip().str.lower()).sum())
    df["item_name_original"] = df["item_name"]
    df["item_name"] = df["item_en"]  # canonical English name, always from menu_items
    report.rows_repaired += n_repaired
    report.issues.append(QualityIssue(
        "pos_transactions", "item_name_repaired_via_sku", n_repaired,
        f"includes {n_unreliable} originally null/blank"
    ))

    # 4. Flag refunds — keep them (they're needed for net revenue), just flag.
    df["is_refund"] = df["quantity"] < 0
    n_refunds = int(df["is_refund"].sum())
    report.issues.append(QualityIssue("pos_transactions", "refund_rows_flagged", n_refunds))
    mismatched_refund_sign = int(((df["is_refund"]) & (df["line_total_sar"] > 0)).sum())
    if mismatched_refund_sign:
        report.issues.append(QualityIssue(
            "pos_transactions", "refund_sign_mismatch", mismatched_refund_sign,
            "quantity negative but line_total_sar positive — needs manual review"
        ))

    # 5. cashier_id missingness — not fixable, just report it (~8% expected).
    n_missing_cashier = int(df["cashier_id"].isna().sum())
    report.issues.append(QualityIssue(
        "pos_transactions", "missing_cashier_id", n_missing_cashier,
        f"{n_missing_cashier/len(df):.1%} of rows — left as null, not imputed"
    ))

    df = df.drop(columns=["item_en", "item_ar", "date_raw", "time_raw"])
    report.rows_out = len(df)
    return df, report


def _parse_pos_timestamp(date_raw, time_raw):
    if date_raw is None:
        return pd.NaT
    date_raw = str(date_raw)
    try:
        if "-" in date_raw and date_raw[:4].isdigit():
            # YYYY-MM-DD
            return pd.to_datetime(f"{date_raw} {time_raw or '00:00:00'}")
        # DD-Mon-YYYY (e.g. 05-Jan-2026), time is HH:MM (no seconds)
        return pd.to_datetime(f"{date_raw} {time_raw or '00:00'}", format="%d-%b-%Y %H:%M")
    except (ValueError, TypeError):
        return pd.NaT


# ---------------------------------------------------------------------------
# menu_items — passthrough, minimal validation
# ---------------------------------------------------------------------------

def clean_menu_items(menu_result: ParseResult) -> tuple[pd.DataFrame, SourceQualityReport]:
    rows = [r.attrs for r in menu_result.records]
    df = pd.DataFrame(rows)
    report = SourceQualityReport(source="menu_items", rows_in=len(df), rows_out=len(df))
    df["launch_date"] = pd.to_datetime(df["launch_date"], errors="coerce")
    df["retire_date"] = pd.to_datetime(df["retire_date"], errors="coerce")
    n_with_launch = int(df["launch_date"].notna().sum())
    if n_with_launch:
        report.issues.append(QualityIssue(
            "menu_items", "items_with_mid_period_launch_date", n_with_launch,
            "these items have no sales before launch_date — analysts should exclude the pre-launch window, not read it as zero demand"
        ))
    return df, report


# ---------------------------------------------------------------------------
# foot_traffic — dead-sensor flagging
# ---------------------------------------------------------------------------

def clean_foot_traffic(traffic_result: ParseResult) -> tuple[pd.DataFrame, SourceQualityReport]:
    rows = [{**r.attrs, "date": r.date} for r in traffic_result.records]
    df = pd.DataFrame(rows)
    report = SourceQualityReport(source="foot_traffic", rows_in=len(df), rows_out=len(df))
    df["date"] = pd.to_datetime(df["date"])

    # A day where every recorded hour has door_count == 0 is a dead sensor,
    # not zero footfall — a cafe doesn't get literally zero visitors for a
    # whole open day. Flag any such day; expected to be 3 days in June.
    daily_max = df.groupby("date")["door_count"].max()
    dead_days = daily_max[daily_max == 0].index
    df["sensor_dead"] = df["date"].isin(dead_days)
    n_dead_rows = int(df["sensor_dead"].sum())
    if len(dead_days):
        report.issues.append(QualityIssue(
            "foot_traffic", "dead_sensor_days_flagged", len(dead_days),
            f"dates: {[d.strftime('%Y-%m-%d') for d in dead_days]} — excluded from conversion-rate calcs, not treated as zero"
        ))
    return df, report


# ---------------------------------------------------------------------------
# staff_shifts — passthrough
# ---------------------------------------------------------------------------

def clean_staff_shifts(shifts_result: ParseResult) -> tuple[pd.DataFrame, SourceQualityReport]:
    rows = [{**r.attrs, "date": r.date} for r in shifts_result.records]
    df = pd.DataFrame(rows)
    report = SourceQualityReport(source="staff_shifts", rows_in=len(df), rows_out=len(df))
    df["date"] = pd.to_datetime(df["date"])

    last_shift = df.groupby("employee_id")["date"].max()
    overall_last_date = df["date"].max()
    left_early = last_shift[last_shift < overall_last_date - pd.Timedelta(days=30)]
    if len(left_early):
        report.issues.append(QualityIssue(
            "staff_shifts", "employees_with_no_recent_shifts", len(left_early),
            f"{list(left_early.index)} — likely departed; last shift "
            f"{[pd.Timestamp(d).strftime('%Y-%m-%d') for d in left_early.values]}. "
            "Not removed from the data, flagged for the Operations analyst."
        ))
    return df, report


# ---------------------------------------------------------------------------
# inventory_weekly — reconcile date formats, distinguish blank vs zero waste
# ---------------------------------------------------------------------------

def clean_inventory_weekly(inv_result: ParseResult) -> tuple[pd.DataFrame, SourceQualityReport]:
    rows = [{**r.attrs, "week_starting_raw": r.date} for r in inv_result.records]
    df = pd.DataFrame(rows)
    report = SourceQualityReport(source="inventory_weekly", rows_in=len(df), rows_out=len(df))

    df["week_starting"] = df["week_starting_raw"].apply(_parse_inventory_date)
    n_alt = df["week_starting_raw"].astype(str).str.match(r"^\d{2}-[A-Za-z]{3}-\d{4}$").sum()
    if n_alt:
        report.issues.append(QualityIssue(
            "inventory_weekly", "alt_date_format_reconciled", int(n_alt),
            "DD-Mon-YYYY rows parsed and converted to ISO"
        ))
    n_bad = int(df["week_starting"].isna().sum())
    if n_bad:
        report.issues.append(QualityIssue("inventory_weekly", "unparseable_week_starting", n_bad))

    # Blank units_wasted means "not recorded", not zero — keep as NaN and
    # add an explicit flag so analysts don't accidentally treat it as 0 waste.
    df["waste_recorded"] = df["units_wasted"].notna()
    n_not_recorded = int((~df["waste_recorded"]).sum())
    if n_not_recorded:
        report.issues.append(QualityIssue(
            "inventory_weekly", "units_wasted_not_recorded", n_not_recorded,
            "left as null — do not treat as zero waste"
        ))

    df = df.drop(columns=["week_starting_raw"])
    return df, report


def _parse_inventory_date(raw):
    if raw is None:
        return pd.NaT
    raw = str(raw)
    try:
        if "-" in raw and raw[:4].isdigit():
            return pd.to_datetime(raw)  # YYYY-MM-DD
        return pd.to_datetime(raw, format="%d-%b-%Y")  # DD-Mon-YYYY
    except (ValueError, TypeError):
        return pd.NaT


# ---------------------------------------------------------------------------
# supplier_emails / customer_reviews — light passthrough
# ---------------------------------------------------------------------------

def clean_supplier_emails(email_result: ParseResult) -> tuple[pd.DataFrame, SourceQualityReport]:
    rows = [{**r.attrs, "record_id": r.record_id, "date": r.date} for r in email_result.records]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    report = SourceQualityReport(source="supplier_emails", rows_in=len(df), rows_out=len(df))
    return df, report


def clean_customer_reviews(reviews_result: ParseResult) -> tuple[pd.DataFrame, SourceQualityReport]:
    rows = [{**r.attrs, "record_id": r.record_id, "date": r.date} for r in reviews_result.records]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["text"] = df["text"].fillna("").str.strip()
    report = SourceQualityReport(source="customer_reviews", rows_in=len(df), rows_out=len(df))
    n_empty = int((df["text"] == "").sum())
    if n_empty:
        report.issues.append(QualityIssue("customer_reviews", "empty_review_text", n_empty))
    return df, report
