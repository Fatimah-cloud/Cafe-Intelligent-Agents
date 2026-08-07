"""
menu_engineering.py — bonus feature: classic four-quadrant menu engineering
(popularity x margin): stars, plough-horses, puzzles, dogs. Recommends what
to cut.

Popularity = units sold relative to the menu's own average (the standard
menu-engineering definition, not an absolute threshold). Margin reuses
item_selector.py's margin_pct computation rather than duplicating it.

Quadrants (standard definitions):
  - star:      high popularity, high margin -> keep, protect, feature
  - plowhorse: high popularity, low margin   -> keep for traffic, look for
               a cost or price fix
  - puzzle:    low popularity, high margin   -> underexposed; promote
               before cutting
  - dog:       low popularity, low margin    -> the actual cut candidates
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from content.item_selector import compute_item_scores
from load_real_data import load_source_for_week, DEFAULT_LOOKBACK_WEEKS


@dataclass
class MenuQuadrantEntry:
    sku: str
    item_en: str
    item_ar: str
    category: str
    units_sold: int
    popularity_index: float
    margin_pct: float
    quadrant: str
    quadrant_ar: str
    recommendation_en: str
    recommendation_ar: str


def compute_menu_matrix(clean_data_dir: str, week_end: Optional[date] = None) -> list[MenuQuadrantEntry]:
    """week_end, if given, restricts popularity to the same trailing
    12-week window item_selector.py uses for margin/trend — omitted, this
    reads the full 6-month history (the original behavior)."""
    pos_path = os.path.join(clean_data_dir, "pos_transactions.csv")
    if not os.path.exists(pos_path):
        return []

    if week_end is not None:
        pos = load_source_for_week(clean_data_dir, "pos", week_end, DEFAULT_LOOKBACK_WEEKS)
    else:
        pos = pd.read_csv(pos_path)
    is_refund_col = pos["is_refund"] if "is_refund" in pos.columns else pos.get("quantity", 0) < 0
    pos = pos[~is_refund_col.astype(bool)]
    units_by_sku = pos.groupby("sku")["quantity"].sum()

    scores = compute_item_scores(clean_data_dir, week_end)
    if not scores:
        return []

    avg_units = units_by_sku.mean()
    avg_margin = sum(s.margin_pct for s in scores) / len(scores)

    entries = []
    for s in scores:
        units = float(units_by_sku.get(s.sku, 0))
        popularity_index = (units / avg_units) if avg_units else 0.0
        high_popularity = popularity_index >= 1.0
        high_margin = s.margin_pct >= avg_margin

        if high_popularity and high_margin:
            quadrant = "star"
            quadrant_ar = "نجم"
            rec = f"Protect and feature - {s.item_en} is both a top seller and above-average margin ({s.margin_pct:.0%}). Don't discount it."
            rec_ar = f"حافظ عليه وروّج له - {s.item_ar} من الأكثر مبيعًا وبهامش ربح أعلى من المتوسط ({s.margin_pct:.0%}). لا تخفّض سعره."
        elif high_popularity and not high_margin:
            quadrant = "plowhorse"
            quadrant_ar = "حصان عمل"
            rec = f"Keep for foot traffic, but margin is below average ({s.margin_pct:.0%}) - worth a cost review or a small price adjustment."
            rec_ar = f"احتفظ به لجذب الزبائن، لكن هامشه أقل من المتوسط ({s.margin_pct:.0%}) - يستحق مراجعة التكلفة أو تعديل السعر قليلًا."
        elif not high_popularity and high_margin:
            quadrant = "puzzle"
            quadrant_ar = "لغز"
            rec = f"High margin ({s.margin_pct:.0%}) but underexposed (popularity {popularity_index:.1f}x average) - promote before considering a cut."
            rec_ar = f"هامش ربح مرتفع ({s.margin_pct:.0%}) لكنه غير معروف بما يكفي (الشعبية {popularity_index:.1f}x من المتوسط) - روّج له قبل التفكير بإلغائه."
        else:
            quadrant = "dog"
            quadrant_ar = "مرشح للإلغاء"
            rec = f"Below-average on both popularity ({popularity_index:.1f}x) and margin ({s.margin_pct:.0%}) - the actual cut candidate."
            rec_ar = f"أقل من المتوسط في الشعبية ({popularity_index:.1f}x) والهامش ({s.margin_pct:.0%}) معًا - المرشح الفعلي للإلغاء من المنيو."

        entries.append(MenuQuadrantEntry(
            sku=s.sku, item_en=s.item_en, item_ar=s.item_ar, category=s.category,
            units_sold=int(units), popularity_index=round(popularity_index, 2),
            margin_pct=round(s.margin_pct, 3), quadrant=quadrant, quadrant_ar=quadrant_ar,
            recommendation_en=rec, recommendation_ar=rec_ar,
        ))

    return sorted(entries, key=lambda e: (e.quadrant != "dog", -e.popularity_index))


def cut_candidates(clean_data_dir: str, week_end: Optional[date] = None) -> list[MenuQuadrantEntry]:
    return [e for e in compute_menu_matrix(clean_data_dir, week_end) if e.quadrant == "dog"]