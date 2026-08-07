"""
Loads Person 1's delivered output (clean_data/*.csv + quality_report.json).

Person 1's clean_data.py writes:
    clean_data/menu_items.csv
    clean_data/pos_transactions.csv
    clean_data/foot_traffic.csv
    clean_data/staff_shifts.csv
    clean_data/inventory_weekly.csv
    clean_data/supplier_emails.csv   (raw: record_id, date, from, subject, body)
    clean_data/customer_reviews.csv
    clean_data/quality_report.json

load_source() is what graph.py's analyst nodes actually call -- each
agent loads only the one or two sources it needs, keyed by clean_data_dir
(a short path string carried in graph state), instead of a full dict of
DataFrames living in state. load_cleaned_data() loads everything at once
and is kept for standalone testing and building mock data directories.

load_source_for_week() is the week-slicing addition: without it, every
analyst read the ENTIRE 6-month dataset regardless of state["week_id"], so
every week's report was identical -- the "week" was just a filename label,
not an actual time window. This filters each date-bearing source down to a
trailing window ending at that week (default 12 weeks of lookback, enough
for week-over-week comparison and anomaly baselines without pulling in the
whole 6 months). menu_items.csv has no per-row date and is never filtered.
"""
import json
import os
from datetime import date

import pandas as pd


SOURCE_FILENAMES = {
    "menu": "menu_items.csv",
    "pos": "pos_transactions.csv",
    "traffic": "foot_traffic.csv",
    "staff": "staff_shifts.csv",
    "inventory": "inventory_weekly.csv",
    "supplier_emails": "supplier_emails.csv",
    "reviews": "customer_reviews.csv",
}

# Which column holds the date to filter on, per source. menu_items.csv is
# deliberately absent here -- it has no per-row date, so it's never
# week-sliced (a menu doesn't have a "this week's version").
DATE_COLUMNS = {
    "pos": "timestamp",
    "traffic": "date",
    "staff": "date",
    "inventory": "week_starting",
    "supplier_emails": "date",
    "reviews": "date",
}

DEFAULT_LOOKBACK_WEEKS = 12


def load_source(clean_data_dir: str, key: str) -> pd.DataFrame:
    """Loads exactly ONE source by name, e.g. load_source(dir, 'pos').
    This is what analysts call — each agent only loads what it needs,
    instead of the whole cleaned_data dict living in graph state."""
    filename = SOURCE_FILENAMES.get(key)
    if filename is None:
        raise KeyError(f"Unknown source key: {key}")
    path = os.path.join(clean_data_dir, filename)
    if not os.path.exists(path):
        return pd.DataFrame()  # missing source -> empty, not a crash
    return pd.read_csv(path)


def week_id_to_end_date(week_id: str) -> date:
    """'2026-W29' -> the Sunday (end) of that ISO week. Raises ValueError
    on a malformed week_id, same as the underlying date.fromisocalendar."""
    year_str, week_str = week_id.split("-W")
    return date.fromisocalendar(int(year_str), int(week_str), 7)


def load_source_for_week(clean_data_dir: str, key: str, week_end: date,
                          lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS) -> pd.DataFrame:
    """Same as load_source(), but filtered to
    [week_end - lookback_weeks, week_end] on that source's date column.
    Sources with no date column (menu_items) are returned unfiltered."""
    df = load_source(clean_data_dir, key)
    date_col = DATE_COLUMNS.get(key)
    if date_col is None or df.empty:
        return df

    dates = pd.to_datetime(df[date_col], errors="coerce")
    week_end_ts = pd.Timestamp(week_end)
    window_start_ts = week_end_ts - pd.Timedelta(weeks=lookback_weeks)
    mask = (dates <= week_end_ts) & (dates >= window_start_ts)
    return df[mask].reset_index(drop=True)


def load_source_for_state(state: dict, key: str, lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS) -> pd.DataFrame:
    """Convenience for analyst nodes: reads clean_data_dir + week_id
    straight from graph state and picks the right loader automatically —
    week-sliced if state["week_id"] is set and parses, full-history
    otherwise (e.g. standalone tests that build state by hand without a
    week_id). Safe to use for every source including menu_items, since
    load_source_for_week already returns date-less sources unfiltered."""
    clean_data_dir = state["clean_data_dir"]
    week_id = state.get("week_id")
    if week_id:
        try:
            week_end = week_id_to_end_date(week_id)
            return load_source_for_week(clean_data_dir, key, week_end, lookback_weeks)
        except (ValueError, IndexError):
            pass  # malformed week_id -> fall through to unfiltered
    return load_source(clean_data_dir, key)


def load_cleaned_data(clean_data_dir: str = "clean_data") -> dict:
    """Reads every available clean_data/*.csv into a DataFrame, all at
    once. Kept for standalone testing (`python load_real_data.py`) and
    for building mock data directories. graph.py's analyst nodes call
    load_source() individually instead of this, keeping graph state --
    and LangSmith traces -- small."""
    cleaned_data = {}
    for key, filename in SOURCE_FILENAMES.items():
        path = os.path.join(clean_data_dir, filename)
        if os.path.exists(path):
            cleaned_data[key] = pd.read_csv(path)
        # else: silently absent — analysts should use .get(key, empty_df)

    return cleaned_data


def load_quality_log(clean_data_dir: str = "clean_data") -> list[str]:
    """Converts Person 1's quality_report.json into flat log strings for
    the shared state's data_quality_log field."""
    path = os.path.join(clean_data_dir, "quality_report.json")
    if not os.path.exists(path):
        return []

    with open(path, encoding="utf-8") as f:
        report = json.load(f)

    log = []
    for missing in report.get("missing_sources", []):
        log.append(f"MISSING SOURCE: {missing}")

    for source in report.get("sources", []):
        log.append(
            f"{source['source']}: {source['rows_in']} in -> {source['rows_out']} out "
            f"(dropped {source['rows_dropped']}, repaired {source['rows_repaired']})"
        )
        for issue in source.get("issues", []):
            detail = f" — {issue['detail']}" if issue.get("detail") else ""
            log.append(f"  · {issue['issue']}: {issue['count']}{detail}")

    return log


if __name__ == "__main__":
    data = load_cleaned_data()
    print("Loaded sources:", list(data.keys()))
    for key, df in data.items():
        print(f"  {key}: {len(df)} rows")

    print("\nQuality log:")
    for line in load_quality_log():
        print(" ", line)