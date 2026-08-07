"""
item_matcher.py — Person 2's findings are flat free text (`{agent, claim, number,
evidence}`, per state.py) with no `sku` field. Several downstream needs (a
revenue-trend chart for a spiking item, the right posting-time window, resolving
the Arabic name for a hook) need to know WHICH menu item a claim is actually about.

This does a plain substring match against `menu_items.csv`'s `item_en` column,
longest name first so "Spanish Latte" matches before the bare "Latte" a shorter
name might also contain. Deliberately conservative — a false match would let
content/charts cite a number under the wrong product name, which is worse than
matching nothing and falling back to a generic angle.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import pandas as pd


@dataclass
class MenuItem:
    sku: str
    item_en: str
    item_ar: str
    category: str


def load_menu(clean_data_dir: str) -> list[MenuItem]:
    path = os.path.join(clean_data_dir, "menu_items.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path)
    items = [MenuItem(sku=r["sku"], item_en=r["item_en"], item_ar=r.get("item_ar", r["item_en"]),
                       category=r.get("category", ""))
             for _, r in df.iterrows()]
    # Longest name first so "Iced Spanish Latte" is tried before "Spanish Latte"
    # before "Latte" — prevents a short name matching inside a longer one's claim.
    return sorted(items, key=lambda m: -len(m.item_en))


def match_item(claim: str, menu: list[MenuItem]) -> MenuItem | None:
    """First menu item whose name appears as a whole word/phrase in the claim."""
    for item in menu:
        pattern = r"\b" + re.escape(item.item_en) + r"\b"
        if re.search(pattern, claim, flags=re.IGNORECASE):
            return item
    return None


def match_all_items(claim: str, menu: list[MenuItem]) -> list[MenuItem]:
    """All menu items mentioned (rare, but a claim could name more than one)."""
    return [item for item in menu if re.search(r"\b" + re.escape(item.item_en) + r"\b", claim, flags=re.IGNORECASE)]
