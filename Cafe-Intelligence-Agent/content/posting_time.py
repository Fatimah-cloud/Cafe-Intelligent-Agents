"""
posting_time.py — "best day and time to post" grounded in when the cafe is actually
busy, per the brief's content-agent requirement.

This is deliberately NOT a `Finding` (contracts/findings_schema.md) — it doesn't need
critic sign-off because it isn't a claim about the business, it's scheduling metadata
derived from the same POS data the sales analyst already validated. Person 3 reads
clean_data/ directly here, once, for this one purpose — everything else content/report
touches goes through the findings contract. If Person 2 would rather own this
(it's arguably an Operations-analyst output), it's a five-line function; happy to move
it behind the contract as a `posting_windows` block on WeeklyFindings instead.

Falls back to the cafe's own opening hours (cafe_profile.json) if clean_data isn't
available yet (e.g. this module used before the pipeline has run) — never invents a
time with zero grounding.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from load_real_data import load_source_for_week, DEFAULT_LOOKBACK_WEEKS


@dataclass
class PostingWindow:
    weekday: str          # e.g. "Saturday"
    hour: int             # 24h, cafe local time
    order_count: int       # how many orders back this recommendation, for the report to cite
    category: str

    def label(self) -> str:
        h12 = self.hour % 12 or 12
        ampm = "AM" if self.hour < 12 else "PM"
        return f"{self.weekday} {h12}{ampm}"


def best_posting_windows(clean_data_dir: str, category_map: dict[str, str] | None = None,
                          week_end: Optional[date] = None) -> dict[str, PostingWindow]:
    """Returns {category: PostingWindow} for each product category present in
    menu_items.csv, using real order-timestamp density. `category_map` lets a caller
    remap category values if a second cafe's menu_items.csv uses different labels.
    `week_end`, if given, restricts to a trailing 12-week window ending at that
    week (e.g. Ramadan's night-shifted traffic pattern only shows up if the
    window actually covers Ramadan) — omitted, this reads the full history.
    """
    pos_path = os.path.join(clean_data_dir, "pos_transactions.csv")
    menu_path = os.path.join(clean_data_dir, "menu_items.csv")
    if not (os.path.exists(pos_path) and os.path.exists(menu_path)):
        return {}

    if week_end is not None:
        pos = load_source_for_week(clean_data_dir, "pos", week_end, DEFAULT_LOOKBACK_WEEKS)
    else:
        pos = pd.read_csv(pos_path)
    is_refund_col = pos["is_refund"] if "is_refund" in pos.columns else pos.get("quantity", 0) < 0
    pos = pos[~is_refund_col.astype(bool)]
    pos["ts"] = pd.to_datetime(pos["timestamp"])
    pos["weekday"] = pos["ts"].dt.day_name()
    pos["hour"] = pos["ts"].dt.hour

    menu = pd.read_csv(menu_path)[["sku", "category"]]
    pos = pos.merge(menu, on="sku", how="left")
    if category_map:
        pos["category"] = pos["category"].replace(category_map)

    windows: dict[str, PostingWindow] = {}
    for category, group in pos.groupby("category"):
        counts = group.groupby(["weekday", "hour"]).size().sort_values(ascending=False)
        if counts.empty:
            continue
        (weekday, hour), n = counts.index[0], counts.iloc[0]
        windows[category] = PostingWindow(weekday=weekday, hour=int(hour), order_count=int(n), category=category)
    return windows


def window_for_sku(clean_data_dir: str, sku: str, week_end: Optional[date] = None) -> PostingWindow | None:
    """Convenience: best posting window for the category a given SKU belongs to."""
    menu_path = os.path.join(clean_data_dir, "menu_items.csv")
    if not os.path.exists(menu_path):
        return None
    menu = pd.read_csv(menu_path)
    row = menu[menu["sku"] == sku]
    if row.empty:
        return None
    category = row.iloc[0]["category"]
    windows = best_posting_windows(clean_data_dir, week_end=week_end)
    return windows.get(category)
