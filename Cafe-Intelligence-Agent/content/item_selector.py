"""
item_selector.py — implements the brief's actual requirement literally:
"push the high-margin item that's trending up; don't promote the thing
you're about to run out of." Person 2's findings alone can't answer this —
`sales_agent` ranks by revenue (not margin), and nothing in verified_findings
checks inventory. This computes it directly from clean_data/, the same
pattern already used by posting_time.py: a Person-3-owned deterministic
metric, not a critic-approved "finding," but genuinely computed from real
data and clearly labeled as such wherever it's shown.

Note on stock-risk coverage: `inventory_weekly.csv` only tracks bakery/food
SKUs (FOD-*) in this dataset — drink SKUs (HOT-*/ICE-*) have no
ordered/sold/wasted rows at all. So a drink can never be flagged "about to
run out" here; that's a real limitation of the source data, not a bug in
this scorer. Documented rather than papered over with a guess.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from load_real_data import load_source_for_week, DEFAULT_LOOKBACK_WEEKS


@dataclass
class ItemScore:
    sku: str
    item_en: str
    item_ar: str
    category: str
    price_sar: float
    unit_cost_sar: float
    margin_sar: float
    margin_pct: float
    revenue_last_week: float
    revenue_prior_week: float
    trend_pct: Optional[float]        # None if not enough weeks of data
    stockout_risk: bool
    stockout_reason: str              # "" if no risk / no data

    def evidence_string(self) -> str:
        """The literal number(s) behind the pick — this IS the citation for
        an idea grounded in this scorer instead of a verified_finding."""
        trend = f"{self.trend_pct:+.1%} WoW" if self.trend_pct is not None else "trend unavailable"
        return (f"{self.item_en}: margin {self.margin_pct:.0%} (SAR {self.margin_sar:.2f}/unit), "
                f"revenue {trend} (SAR {self.revenue_prior_week:,.0f} -> SAR {self.revenue_last_week:,.0f})"
                + (f", stock risk: {self.stockout_reason}" if self.stockout_risk else ", no stockout risk flagged"))


def compute_item_scores(clean_data_dir: str, week_end: Optional[date] = None) -> list[ItemScore]:
    """week_end=None keeps the old full-history behavior (used by
    standalone tests); pass it to get a trailing-12-week window ending at
    that week, matching how the analysts already read data via
    load_real_data.load_source_for_state()."""
    pos_path = os.path.join(clean_data_dir, "pos_transactions.csv")
    menu_path = os.path.join(clean_data_dir, "menu_items.csv")
    inv_path = os.path.join(clean_data_dir, "inventory_weekly.csv")
    if not (os.path.exists(pos_path) and os.path.exists(menu_path)):
        return []

    if week_end is not None:
        pos = load_source_for_week(clean_data_dir, "pos", week_end, DEFAULT_LOOKBACK_WEEKS)
    else:
        pos = pd.read_csv(pos_path)
    is_refund_col = pos["is_refund"] if "is_refund" in pos.columns else pos.get("quantity", 0) < 0
    pos = pos[~is_refund_col.astype(bool)]
    pos["week"] = pd.to_datetime(pos["timestamp"]).dt.to_period("W-SUN").apply(lambda r: r.start_time.date())

    menu = pd.read_csv(menu_path)  # no date column — never sliced

    if week_end is not None:
        inv = load_source_for_week(clean_data_dir, "inventory", week_end, DEFAULT_LOOKBACK_WEEKS)
    else:
        inv = pd.read_csv(inv_path) if os.path.exists(inv_path) else pd.DataFrame()
    latest_inv_by_sku = {}
    if not inv.empty:
        inv["week_starting"] = pd.to_datetime(inv["week_starting"])
        latest_week = inv["week_starting"].max()
        latest_rows = inv[inv["week_starting"] == latest_week]
        latest_inv_by_sku = {row["sku"]: row for _, row in latest_rows.iterrows()}

    weeks_sorted = sorted(pos["week"].unique())

    scores = []
    for _, item in menu.iterrows():
        sku = item["sku"]
        price = float(item["price_sar"])
        cost = float(item["unit_cost_sar"])
        margin_sar = price - cost
        margin_pct = margin_sar / price if price else 0.0

        item_pos = pos[pos["sku"] == sku]
        weekly_rev = item_pos.groupby("week")["line_total_sar"].sum()

        if len(weeks_sorted) >= 2:
            last_w, prior_w = weeks_sorted[-1], weeks_sorted[-2]
            rev_last = float(weekly_rev.get(last_w, 0.0))
            rev_prior = float(weekly_rev.get(prior_w, 0.0))
            trend_pct = ((rev_last - rev_prior) / rev_prior) if rev_prior else None
        else:
            rev_last = float(weekly_rev.sum())
            rev_prior = 0.0
            trend_pct = None

        stockout_risk, reason = False, ""
        if sku in latest_inv_by_sku:
            row = latest_inv_by_sku[sku]
            ordered, sold = row.get("units_ordered"), row.get("units_sold")
            if pd.notna(ordered) and pd.notna(sold) and ordered:
                sell_through = sold / ordered
                if sell_through >= 0.9:
                    stockout_risk = True
                    reason = f"sold {int(sold)}/{int(ordered)} ordered last week ({sell_through:.0%} sell-through)"

        scores.append(ItemScore(
            sku=sku, item_en=item["item_en"], item_ar=item.get("item_ar", item["item_en"]),
            category=item.get("category", ""), price_sar=price, unit_cost_sar=cost,
            margin_sar=margin_sar, margin_pct=margin_pct,
            revenue_last_week=rev_last, revenue_prior_week=rev_prior, trend_pct=trend_pct,
            stockout_risk=stockout_risk, stockout_reason=reason,
        ))
    return scores


def select_featured_item(clean_data_dir: str, week_end: Optional[date] = None) -> Optional[ItemScore]:
    """The brief's literal ask: highest margin among items that are (a)
    trending up week-over-week and (b) not flagged as about to run out.
    Falls back progressively if nothing meets every criterion, rather than
    returning nothing — but the fallback tiers are visible in the reason
    so the report never silently ships a weaker pick as if it were ideal."""
    scores = compute_item_scores(clean_data_dir, week_end)
    if not scores:
        return None

    trending_safe = [s for s in scores if s.trend_pct is not None and s.trend_pct > 0 and not s.stockout_risk]
    if trending_safe:
        return max(trending_safe, key=lambda s: s.margin_pct)

    safe = [s for s in scores if not s.stockout_risk]
    if safe:
        return max(safe, key=lambda s: s.margin_pct)

    return max(scores, key=lambda s: s.margin_pct)
