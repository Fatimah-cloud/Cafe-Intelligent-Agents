"""
report_generator.py — WhatsApp summary + full bilingual HTML report, rebuilt
against Person 2's real CafeState shape (`verified_findings`, `critic_feedback`,
`rejection_log`, `revision_count`, `data_quality_log`, `week_id`).
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from content.content_agent import ContentIdea
from content.menu_engineering import compute_menu_matrix
from content.waste_analysis import compute_waste_report, total_monthly_waste_cost, total_projected_monthly_savings
from report.charts import build_charts_for_findings
from load_real_data import week_id_to_end_date


def _safe_week_end(week_id: str):
    """Parses week_id ('2026-W29') into the date week_end that
    item_selector/waste_analysis/menu_engineering use for week-slicing.
    Returns None on a malformed/missing week_id rather than raising —
    those modules already treat week_end=None as "use full history," so
    this degrades gracefully instead of breaking the report."""
    try:
        return week_id_to_end_date(week_id)
    except (ValueError, IndexError, AttributeError):
        return None
WHATSAPP_CHAR_BUDGET_PER_LANG = 1100
TEMPLATE_DIR = Path(__file__).parent / "templates"


def _trim_block(header: str, body: list[str], footer: str, budget: int) -> str:
    text = "\n".join([header, *body, footer])
    if len(text) <= budget:
        return text
    kept, running = [], len(header) + len(footer) + 20
    for line in body:
        if running + len(line) > budget:
            kept.append("…(more in full report)")
            break
        kept.append(line)
        running += len(line) + 1
    return "\n".join([header, *kept, footer])


def _fmt_number(n) -> str:
    if isinstance(n, float):
        return f"{n:,.1f}"
    if isinstance(n, int):
        return f"{n:,}"
    return str(n)


AGENT_PRIORITY = ["sales", "margin", "operations", "reviews", "anomaly"]

AGENT_LABELS = {
    "sales": {"en": "Sales & Product Mix", "ar": "المبيعات والمنتجات", "icon": "📈"},
    "margin": {"en": "Margin & Cost", "ar": "الهامش والتكلفة", "icon": "💰"},
    "operations": {"en": "Operations", "ar": "التشغيل", "icon": "⚙️"},
    "reviews": {"en": "Customer Voice", "ar": "آراء العملاء", "icon": "⭐"},
    "anomaly": {"en": "Anomalies This Period", "ar": "حالات غير معتادة", "icon": "🔍"},
}


def _group_findings_by_agent(findings: list[dict]) -> list[dict]:
    """Groups findings under a friendly section per analyst, in a fixed
    readable order, instead of one flat undifferentiated list — the flat
    list was the single biggest readability problem in early report
    versions: 25+ cards with no way to scan to what you care about."""
    by_agent: dict[str, list[dict]] = {}
    for f in findings:
        by_agent.setdefault(f.get("agent", "other"), []).append(f)

    groups = []
    for agent in AGENT_PRIORITY:
        if agent in by_agent:
            label = AGENT_LABELS.get(agent, {"en": agent.title(), "ar": agent, "icon": "•"})
            groups.append({"agent": agent, "label_en": label["en"], "label_ar": label["ar"],
                            "icon": label["icon"], "findings": by_agent[agent]})
    for agent, items in by_agent.items():
        if agent not in AGENT_PRIORITY:
            groups.append({"agent": agent, "label_en": agent.title(), "label_ar": agent,
                            "icon": "•", "findings": items})
    return groups


def _pick_diverse_findings(findings: list[dict], n: int = 5) -> list[dict]:
    """The merged findings list order depends on which parallel analyst
    branch happened to finish first (state.py's operator.add reducer has no
    ordering guarantee), so a naive findings[:n] can accidentally show 5
    anomaly findings and nothing else. Picks one finding per agent first
    (in a fixed, readable priority order), then fills any remaining slots."""
    by_agent: dict[str, list[dict]] = {}
    for f in findings:
        by_agent.setdefault(f.get("agent", "other"), []).append(f)

    picked, picked_ids = [], set()
    for agent in AGENT_PRIORITY:
        if agent in by_agent and len(picked) < n:
            f = by_agent[agent][0]
            picked.append(f)
            picked_ids.add(id(f))

    if len(picked) < n:
        for f in findings:
            if id(f) not in picked_ids:
                picked.append(f)
                picked_ids.add(id(f))
            if len(picked) >= n:
                break
    return picked[:n]


def build_whatsapp_summary(week_id: str, verified_findings: list[dict], content_ideas: list[ContentIdea],
                            cafe_profile: dict, findings_ar: dict[int, str], clean_data_dir: str = "clean_data") -> str:
    cafe_name = cafe_profile.get("cafe_name", "your cafe")

    header_en = f"📊 {cafe_name} report — week {week_id}"
    header_ar = f"📊 تقرير {cafe_name} — الأسبوع {week_id}"

    # findings_ar is keyed by index into the ORIGINAL verified_findings list
    # (see translate_findings_to_arabic), so look up by identity, not position,
    # once we've picked a diverse (and differently-ordered) subset.
    index_by_id = {id(f): i for i, f in enumerate(verified_findings)}
    diverse = _pick_diverse_findings(verified_findings, n=5)

    body_en = [""] + [f"• {f['claim']}" + (f" ({_fmt_number(f['number'])})" if f.get("number") is not None else "")
                       for f in diverse]
    body_ar = [""] + [f"• {findings_ar.get(index_by_id[id(f)], f['claim'])}" for f in diverse]

    body_en.append("\n🎬 This week's content ideas:")
    body_ar.append("\n🎬 أفكار المحتوى هذا الأسبوع:")
    for idea in content_ideas:
        product_en = f" — {idea.product_name_en}" if idea.product_name_en else ""
        product_ar = f" — {idea.product_name_ar}" if idea.product_name_ar else ""
        body_en.append(f"- [{idea.format}] {idea.hook_en}{product_en} ({idea.best_day} {idea.best_time})")
        body_ar.append(f"- [{idea.format}] {idea.hook_ar}{product_ar} ({idea.best_day} {idea.best_time})")

    week_end = _safe_week_end(week_id)
    waste_lines = compute_waste_report(clean_data_dir, week_end)
    if waste_lines:
        total_waste = total_monthly_waste_cost(clean_data_dir, week_end)
        top_waste = waste_lines[0]
        body_en.append(f"\n🗑️ Waste: SAR {total_waste:,.0f}/month across bakery items. "
                        f"Biggest: {top_waste.item} (SAR {top_waste.monthly_waste_cost_sar:,.0f}/mo) — "
                        f"order {top_waste.recommended_weekly_order:.0f}/wk instead of {top_waste.avg_weekly_ordered:.0f}/wk.")
        body_ar.append(f"\n🗑️ الهدر: {total_waste:,.0f} ريال/شهر في المخبوزات. "
                        f"الأكبر: {top_waste.item} ({top_waste.monthly_waste_cost_sar:,.0f} ريال/شهر) — "
                        f"اطلب {top_waste.recommended_weekly_order:.0f}/أسبوع بدلاً من {top_waste.avg_weekly_ordered:.0f}.")

    menu_matrix = compute_menu_matrix(clean_data_dir, week_end)
    dogs = [e for e in menu_matrix if e.quadrant == "dog"]
    if dogs:
        top_dog = dogs[0]
        body_en.append(f"📋 Menu: {len(dogs)} item(s) below-average on both popularity and margin — "
                        f"consider cutting {top_dog.item_en} (popularity {top_dog.popularity_index:.1f}x avg, margin {top_dog.margin_pct:.0%}).")
        body_ar.append(f"📋 المنيو: {len(dogs)} صنف أقل من المتوسط في الشعبية والهامش معًا — "
                        f"فكر بإلغاء {top_dog.item_ar} (الشعبية {top_dog.popularity_index:.1f}x، الهامش {top_dog.margin_pct:.0%}).")

    footer_en = "\n✅ Reply APPROVE to send, EDIT to change, or REJECT to skip this week."
    footer_ar = "\n✅ ردّ بكلمة موافقة للإرسال، تعديل للتغيير، أو رفض لتخطي هذا الأسبوع."

    ar_block = _trim_block(header_ar, body_ar, footer_ar, WHATSAPP_CHAR_BUDGET_PER_LANG)
    en_block = _trim_block(header_en, body_en, footer_en, WHATSAPP_CHAR_BUDGET_PER_LANG)

    return ar_block + "\n\n" + ("—" * 12) + "\n\n" + en_block


def build_html_report(week_id: str, verified_findings: list[dict], content_ideas: list[ContentIdea],
                       cafe_profile: dict, clean_data_dir: str, data_quality_log: list[str],
                       critic_feedback: str, rejection_log: list[str], revision_count: int,
                       output_path: str, findings_ar: dict[int, str], generated_at: str = "") -> str:
    """
    FIX: findings_ar is now a REQUIRED parameter instead of being recomputed
    here via translate_findings_to_arabic(). Previously this function called
    that translator a second time on the exact same verified_findings that
    report_node.py had already translated once for the WhatsApp summary --
    doubling the LLM cost/latency of every run for identical output, and
    silently doubling the effective per-run budget in cost_tracker.py (each
    call created its own separate CostTracker instance, so the documented
    $0.50/run cap was never actually enforced across both calls). Callers
    must now compute findings_ar once (see report_node.py) and pass it in.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
    template = env.get_template("report.html.j2")

    # _idx preserves the ORIGINAL position in verified_findings, since charts
    # are keyed by that index (report/charts.py) — grouping findings by
    # agent below reorders them for display, so the chart lookup in the
    # template needs this to still find the right image.
    findings_for_template = [
        {**f, "claim_ar": findings_ar.get(i, f["claim"]), "_idx": i}
        for i, f in enumerate(verified_findings)
    ]
    finding_groups = _group_findings_by_agent(findings_for_template)
    highlights = _pick_diverse_findings(findings_for_template, n=4)

    charts = build_charts_for_findings(clean_data_dir, verified_findings)
    week_end = _safe_week_end(week_id)
    waste_lines = compute_waste_report(clean_data_dir, week_end)
    menu_matrix = compute_menu_matrix(clean_data_dir, week_end)

    html = template.render(
        cafe_name_en=cafe_profile.get("cafe_name", "Cafe"),
        cafe_name_ar=cafe_profile.get("cafe_name_ar", cafe_profile.get("cafe_name", "")),
        week_id=week_id,
        generated_at=generated_at,
        finding_groups=finding_groups,
        highlights=highlights,
        total_findings=len(verified_findings),
        charts=charts,
        content_ideas=[asdict(i) for i in content_ideas],
        data_quality_log=data_quality_log,
        critic_feedback=critic_feedback,
        rejection_log=rejection_log,
        revision_count=revision_count,
        waste_lines=[vars(w) for w in waste_lines],
        waste_total_monthly=total_monthly_waste_cost(clean_data_dir, week_end),
        waste_projected_savings=total_projected_monthly_savings(clean_data_dir, week_end),
        menu_matrix=[vars(e) for e in menu_matrix],
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return output_path
