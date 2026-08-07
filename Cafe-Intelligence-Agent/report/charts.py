"""
charts.py — real charts computed from clean_data, keyed to Person 2's actual
findings (`{agent, claim, number, evidence}` — no `sku` field), using
content/item_matcher.py to resolve which product a claim is about.

Bilingual fix: every chart-generating function now takes a `lang` parameter
("en" | "ar") and renders its title/axis labels in that language, instead of
always rendering English text regardless of which language section of the
report the image ends up embedded in. Found via a real report review — the
Arabic section was showing an English-only "Average rating by review
language" chart title sitting directly under Arabic prose. Applies to every
chart, not just that one, since all four had the identical issue.

`build_charts_for_findings()` now returns `{key: {"en": base64, "ar": base64}}`
instead of `{key: base64}` — the report template picks `charts[key][lang]`
inside its per-language loop (see report.html.j2).
"""

from __future__ import annotations

import base64
import io
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from content.item_matcher import load_menu, match_item

plt.rcParams.update({
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.facecolor": "white",
})

BAR_COLOR = "#6F4E37"
ACCENT_COLOR = "#C08552"


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def revenue_trend_chart(clean_data_dir: str, sku: str, item_label_en: str, item_label_ar: str,
                         lang: str = "en", weeks_back: int = 10) -> str | None:
    path = os.path.join(clean_data_dir, "pos_transactions.csv")
    if not os.path.exists(path):
        return None
    pos = pd.read_csv(path)
    is_refund_col = pos["is_refund"] if "is_refund" in pos.columns else pos.get("quantity", 0) < 0
    pos = pos[(~is_refund_col.astype(bool)) & (pos["sku"] == sku)]
    if pos.empty:
        return None
    pos["week"] = pd.to_datetime(pos["timestamp"]).dt.to_period("W-SUN").apply(lambda r: r.start_time.date())
    series = pos.groupby("week")["line_total_sar"].sum().sort_index().tail(weeks_back)

    fig, ax = plt.subplots(figsize=(6, 3))
    ax.plot([str(w) for w in series.index], series.values, marker="o", color=BAR_COLOR, linewidth=2)
    item_label = item_label_en if lang == "en" else item_label_ar
    title = f"{item_label} — weekly revenue (SAR)" if lang == "en" else f"{item_label} — الإيرادات الأسبوعية (ريال)"
    ax.set_title(title, fontsize=11)
    ax.set_ylabel("SAR" if lang == "en" else "ريال")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return _fig_to_base64(fig)


def item_daily_quantity_chart(clean_data_dir: str, sku: str, item_label_en: str, item_label_ar: str,
                               around_date: str, lang: str = "en", window_days: int = 21) -> str | None:
    """For an anomaly finding: daily unit sales for the item around the
    flagged date, so the spike is visible in context, not just a z-score."""
    path = os.path.join(clean_data_dir, "pos_transactions.csv")
    if not os.path.exists(path):
        return None
    pos = pd.read_csv(path)
    is_refund_col = pos["is_refund"] if "is_refund" in pos.columns else pos.get("quantity", 0) < 0
    pos = pos[(~is_refund_col.astype(bool)) & (pos["sku"] == sku)]
    if pos.empty:
        return None
    pos["date"] = pd.to_datetime(pos["timestamp"]).dt.date
    center = pd.Timestamp(around_date).date()
    lo = center - pd.Timedelta(days=window_days // 2)
    hi = center + pd.Timedelta(days=window_days // 2)
    daily = pos[(pos["date"] >= lo) & (pos["date"] <= hi)].groupby("date")["quantity"].sum()
    if daily.empty:
        return None

    fig, ax = plt.subplots(figsize=(6, 3))
    colors = [ACCENT_COLOR if d == center else BAR_COLOR for d in daily.index]
    ax.bar([str(d) for d in daily.index], daily.values, color=colors)
    item_label = item_label_en if lang == "en" else item_label_ar
    title = (f"{item_label} — daily units around {around_date}" if lang == "en"
              else f"{item_label} — الوحدات اليومية حول {around_date}")
    ax.set_title(title, fontsize=11)
    ax.set_ylabel("units" if lang == "en" else "الوحدات")
    ax.tick_params(axis="x", rotation=60)
    fig.tight_layout()
    return _fig_to_base64(fig)


def top_products_chart(clean_data_dir: str, lang: str = "en", top_n: int = 6) -> str | None:
    path = os.path.join(clean_data_dir, "pos_transactions.csv")
    menu_path = os.path.join(clean_data_dir, "menu_items.csv")
    if not (os.path.exists(path) and os.path.exists(menu_path)):
        return None
    pos = pd.read_csv(path)
    is_refund_col = pos["is_refund"] if "is_refund" in pos.columns else pos.get("quantity", 0) < 0
    pos = pos[~is_refund_col.astype(bool)]
    menu = pd.read_csv(menu_path)[["sku", "item_en", "item_ar"]]

    top = pos.groupby("sku")["line_total_sar"].sum().sort_values(ascending=False).head(top_n)
    top = top.reset_index().merge(menu, on="sku", how="left").sort_values("line_total_sar")
    name_col = "item_en" if lang == "en" else "item_ar"

    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.barh(top[name_col], top["line_total_sar"], color=BAR_COLOR)
    title = f"Top {top_n} products by revenue (period)" if lang == "en" else f"أفضل {top_n} منتجات حسب الإيرادات (الفترة)"
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("SAR" if lang == "en" else "ريال")
    fig.tight_layout()
    return _fig_to_base64(fig)


def ratings_by_language_chart(clean_data_dir: str, lang: str = "en") -> str | None:
    path = os.path.join(clean_data_dir, "customer_reviews.csv")
    if not os.path.exists(path):
        return None
    reviews = pd.read_csv(path)
    if "language" not in reviews.columns or reviews.empty:
        return None
    stats = reviews.groupby("language")["rating"].mean().sort_values()

    fig, ax = plt.subplots(figsize=(4, 2.5))
    ax.barh(stats.index, stats.values, color=BAR_COLOR)
    ax.set_xlim(0, 5)
    title = "Average rating by review language" if lang == "en" else "متوسط التقييم حسب لغة المراجعة"
    ax.set_title(title, fontsize=11)
    fig.tight_layout()
    return _fig_to_base64(fig)


def build_charts_for_findings(clean_data_dir: str, findings: list[dict]) -> dict[str, dict[str, str]]:
    """One chart per finding where the claim resolves to a real chart,
    keyed by a stable index into `findings` (not a finding_id — Person 2's
    schema doesn't have one, so the report template keys charts by list
    position instead). Each value is now {"en": base64, "ar": base64} —
    the SAME underlying data, rendered twice with language-matched
    titles/labels, so an image never contradicts the prose language it's
    sitting next to."""
    menu = load_menu(clean_data_dir)
    charts: dict[str, dict[str, str]] = {}
    lang_chart_built = False

    for i, f in enumerate(findings):
        claim = f["claim"]
        agent = f["agent"]
        item = match_item(claim, menu)

        if agent == "sales" and item and ("best seller" in claim.lower() or "worst seller" in claim.lower()):
            img_en = revenue_trend_chart(clean_data_dir, item.sku, item.item_en, item.item_ar, lang="en")
            img_ar = revenue_trend_chart(clean_data_dir, item.sku, item.item_en, item.item_ar, lang="ar")
            if img_en:
                charts[str(i)] = {"en": img_en, "ar": img_ar or img_en}
        elif agent == "anomaly" and item:
            import re
            m = re.search(r"\d{4}-\d{2}-\d{2}", claim)
            if m:
                img_en = item_daily_quantity_chart(clean_data_dir, item.sku, item.item_en, item.item_ar, m.group(0), lang="en")
                img_ar = item_daily_quantity_chart(clean_data_dir, item.sku, item.item_en, item.item_ar, m.group(0), lang="ar")
                if img_en:
                    charts[str(i)] = {"en": img_en, "ar": img_ar or img_en}
        elif agent == "reviews" and "language reviews" in claim.lower() and not lang_chart_built:
            img_en = ratings_by_language_chart(clean_data_dir, lang="en")
            img_ar = ratings_by_language_chart(clean_data_dir, lang="ar")
            if img_en:
                charts[str(i)] = {"en": img_en, "ar": img_ar or img_en}
                lang_chart_built = True

    overview_en = top_products_chart(clean_data_dir, lang="en")
    overview_ar = top_products_chart(clean_data_dir, lang="ar")
    if overview_en:
        charts["_overview_top_products"] = {"en": overview_en, "ar": overview_ar or overview_en}
    return charts