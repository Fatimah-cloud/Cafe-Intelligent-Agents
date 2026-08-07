"""
waste_analysis.py — bonus feature: "Waste-to-riyals — quantify what waste is
costing per month and propose order quantities that fix it."

Reads clean_data/inventory_weekly.csv directly (same status as
posting_time.py / item_selector.py — a genuinely computed Person-3 metric,
not a critic-reviewed finding). Coverage note: this dataset's inventory
tracking only covers bakery/food SKUs (FOD-*) — drink SKUs have no
ordered/sold/wasted rows at all, so this report is necessarily food-only.
That's a real property of the source data, documented rather than
papered over.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from load_real_data import load_source_for_week, DEFAULT_LOOKBACK_WEEKS

@dataclass
class WasteLine:
    sku: str
    item: str
    weeks_recorded: int
    avg_weekly_waste_units: float
    avg_weekly_waste_cost_sar: float
    monthly_waste_cost_sar: float          # avg_weekly * 4.33 (weeks/month)
    avg_weekly_ordered: float
    avg_weekly_sold: float
    current_order_to_sold_ratio: float     # how much is ordered per unit actually sold
    recommended_weekly_order: float        # sold + a modest safety margin, not the current over-order pattern
    projected_monthly_savings_sar: float

def compute_waste_report(clean_data_dir: str, week_end: Optional[date] = None) -> list[WasteLine]:
    """week_end, if given, restricts to a trailing 12-week window ending at
    that week — omitted, this reads the full 6-month history (the original
    behavior)."""
    path = os.path.join(clean_data_dir, "inventory_weekly.csv")
    if not os.path.exists(path):
        return []

    if week_end is not None:
        inv = load_source_for_week(clean_data_dir, "inventory", week_end, DEFAULT_LOOKBACK_WEEKS)
        if inv.empty:
            return []
    else:
        inv = pd.read_csv(path)
    inv["week_starting"] = pd.to_datetime(inv["week_starting"])
    recorded = inv[inv.get("waste_recorded", inv["units_wasted"].notna()) == True]  # noqa: E712

    lines = []
    for sku, group in recorded.groupby("sku"):
        item_name = group["item"].iloc[0]
        weeks_recorded = group["week_starting"].nunique()
        avg_waste_units = group["units_wasted"].mean()
        unit_cost = group["unit_cost_sar"].iloc[-1]  # most recent recorded cost
        avg_waste_cost = avg_waste_units * unit_cost
        monthly_waste_cost = avg_waste_cost * 4.33

        avg_ordered = group["units_ordered"].mean()
        avg_sold = group["units_sold"].mean()
        ratio = (avg_ordered / avg_sold) if avg_sold else None

        # Recommended order: what was actually sold, plus a 10% safety
        # margin (not the current pattern of over-ordering by whatever
        # the waste gap already shows) — a real, computable reduction the
        # owner can act on next week, not a vague "order less."
        recommended = avg_sold * 1.10
        projected_savings = max(0.0, (avg_ordered - recommended)) * unit_cost * 4.33

        lines.append(WasteLine(
            sku=sku, item=item_name, weeks_recorded=int(weeks_recorded),
            avg_weekly_waste_units=round(float(avg_waste_units), 1),
            avg_weekly_waste_cost_sar=round(float(avg_waste_cost), 2),
            monthly_waste_cost_sar=round(float(monthly_waste_cost), 2),
            avg_weekly_ordered=round(float(avg_ordered), 1),
            avg_weekly_sold=round(float(avg_sold), 1),
            current_order_to_sold_ratio=round(float(ratio), 2) if ratio else 0.0,
            recommended_weekly_order=round(float(recommended), 1),
            projected_monthly_savings_sar=round(float(projected_savings), 2),
        ))

    return sorted(lines, key=lambda w: -w.monthly_waste_cost_sar)


def total_monthly_waste_cost(clean_data_dir: str, week_end: Optional[date] = None) -> float:
    lines = compute_waste_report(clean_data_dir, week_end)
    return round(sum(l.monthly_waste_cost_sar for l in lines), 2)


def total_projected_monthly_savings(clean_data_dir: str, week_end: Optional[date] = None) -> float:
    lines = compute_waste_report(clean_data_dir, week_end)
    return round(sum(l.projected_monthly_savings_sar for l in lines), 2)
