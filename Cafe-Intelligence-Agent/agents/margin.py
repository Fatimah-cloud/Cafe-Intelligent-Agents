"""
Margin & Cost Analyst.

Questions this agent answers:
- Revenue, profit, margin
- Supplier cost changes and their effect on margin

Design note: menu_items.csv has a STATIC unit_cost_sar per item — it does
not reflect real supplier price changes that happened mid-period (e.g.
coffee beans +9% on 2026-04-15, milk +18% on 2026-05-01, both mentioned
in supplier emails). This agent must split margin calculations into
pre/post periods around each cost-affecting event, not use one flat cost
for the whole six months.

Data contract note: Person 1's supplier_emails.csv is intentionally RAW
(record_id, date, from, subject, body) — deciding which emails describe a
price change, and extracting the numbers, is analysis, not ingestion, so
that extraction happens here, via the LLM, not in the cleaning step.
"""
import json
from typing import TYPE_CHECKING
import pandas as pd

from agents._code_runner import run_self_correcting_code
from load_real_data import load_source_for_state

if TYPE_CHECKING:
    from state import CafeState


_llm = None


def _get_llm():
    """Lazy-loaded, same pattern as _code_runner.py — no API call happens
    unless price events actually need to be extracted from raw emails."""
    global _llm
    if _llm is None:
        from langchain_google_genai import ChatGoogleGenerativeAI
        _llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite")
    return _llm


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


def extract_price_change_events(supplier_emails: pd.DataFrame) -> list[dict]:
    """
    Reads the raw supplier_emails DataFrame (subject + body text) and asks
    the model to identify which emails describe a supplier PRICE CHANGE
    (ignoring delivery delays, event announcements, packaging quotes, etc.),
    extracting: item, old_value, new_value, effective_date.

    All emails are sent in ONE model call (not one call per email) — cheap
    on the daily quota, and lets the model see all emails together to avoid
    double-counting a price mentioned in more than one email.
    """
    if supplier_emails.empty:
        return []

    emails_text = "\n\n---\n\n".join(
        f"record_id: {row['record_id']}\ndate: {row['date']}\n"
        f"subject: {row.get('subject', '')}\nbody: {row.get('body', '')}"
        for _, row in supplier_emails.iterrows()
    )

    prompt = (
        "Below are supplier emails for a cafe. Identify ONLY the emails that "
        "describe a SUPPLIER PRICE CHANGE for an ingredient (not delivery "
        "delays, not event announcements, not packaging quotes, not POS "
        "maintenance notices). For each price-change email, extract:\n"
        '{"item": "<short snake_case ingredient name>", '
        '"old_value": <number>, "new_value": <number>, '
        '"effective_date": "<YYYY-MM-DD>"}\n\n'
        "Return ONLY a JSON array of these objects, nothing else. If none "
        "of the emails describe a price change, return [].\n\n"
        f"{emails_text}"
    )
    response = _get_llm().invoke(prompt)
    try:
        return json.loads(_extract_text(response.content).strip())
    except json.JSONDecodeError:
        return []


MARGIN_CODE_TEMPLATE = '''
import pandas as pd
import json

pos = pd.read_json("{pos_path}")
menu = pd.read_json("{menu_path}")
events = json.loads(\'\'\'{events_json}\'\'\')

pos["timestamp"] = pd.to_datetime(pos["timestamp"])
merged = pos.merge(menu[["sku", "item_en", "category", "unit_cost_sar"]], on="sku", how="left")

# Overall (naive) margin using the static menu unit_cost_sar, for comparison
merged["cost_total"] = merged["quantity"].abs() * merged["unit_cost_sar"]
merged["revenue"] = merged["line_total_sar"]
naive_revenue = merged["revenue"].sum()
naive_cost = merged["cost_total"].sum()
naive_margin_pct = ((naive_revenue - naive_cost) / naive_revenue * 100) if naive_revenue else None

# Per cost-change event: compare margin in the window just before vs just
# after the effective_date, restricted to items in the affected category
# (coffee-related SKUs for coffee price changes, anything using milk is
# approximated here as hot/iced coffee categories, since we don't have a
# recipe-level ingredient breakdown in this dataset).
event_impacts = []
for ev in events:
    eff_date = pd.Timestamp(ev["effective_date"])
    window = pd.Timedelta(days=14)

    before = merged[(merged["timestamp"] >= eff_date - window) & (merged["timestamp"] < eff_date)]
    after = merged[(merged["timestamp"] >= eff_date) & (merged["timestamp"] < eff_date + window)]

    if ev["item"] == "roasted_coffee":
        cat_mask_before = before["category"].isin(["hot_coffee", "iced_coffee"])
        cat_mask_after = after["category"].isin(["hot_coffee", "iced_coffee"])
    elif ev["item"] == "full_fat_milk":
        cat_mask_before = before["category"].isin(["hot_coffee", "iced_coffee"])
        cat_mask_after = after["category"].isin(["hot_coffee", "iced_coffee"])
    else:
        cat_mask_before = pd.Series([True] * len(before))
        cat_mask_after = pd.Series([True] * len(after))

    before_cat = before[cat_mask_before]
    after_cat = after[cat_mask_after]

    pct_cost_increase = ((ev["new_value"] - ev["old_value"]) / ev["old_value"] * 100) if ev["old_value"] else None

    before_rev = before_cat["revenue"].sum()
    before_cost = before_cat["cost_total"].sum()
    before_margin_pct = ((before_rev - before_cost) / before_rev * 100) if before_rev else None

    after_rev = after_cat["revenue"].sum()
    after_cost_naive = after_cat["cost_total"].sum()
    after_margin_naive_pct = ((after_rev - after_cost_naive) / after_rev * 100) if after_rev else None

    event_impacts.append({{
        "item": ev["item"],
        "effective_date": ev["effective_date"],
        "supplier_pct_increase": round(pct_cost_increase, 1) if pct_cost_increase is not None else None,
        "affected_category_revenue_14d_before": float(before_rev),
        "affected_category_revenue_14d_after": float(after_rev),
        "margin_pct_before_using_stale_cost": round(before_margin_pct, 1) if before_margin_pct is not None else None,
        "margin_pct_after_using_stale_cost": round(after_margin_naive_pct, 1) if after_margin_naive_pct is not None else None,
        "note": "menu_items.csv unit_cost_sar is static and does NOT reflect this increase; true post-increase margin is lower than shown here unless costs are updated.",
    }})

result = {{
    "naive_overall_margin_pct": round(naive_margin_pct, 1) if naive_margin_pct is not None else None,
    "naive_overall_revenue": float(naive_revenue),
    "naive_overall_cost": float(naive_cost),
    "event_impacts": event_impacts,
}}
print(json.dumps(result))
'''


def margin_agent(state: "CafeState") -> dict:
    """Runs margin analysis via subprocess with self-correction, splitting
    margin around each confirmed supplier price-change event."""
    pos: pd.DataFrame = load_source_for_state(state, "pos")
    menu: pd.DataFrame = load_source_for_state(state, "menu")
    supplier_emails: pd.DataFrame = load_source_for_state(state, "supplier_emails")

    # Raw emails in -> structured price-change events out. This is the one
    # real LLM call this agent makes; everything after this is deterministic
    # pandas math run in a subprocess.
    price_change_events = extract_price_change_events(supplier_emails)

    # Cross-platform temp path (works on Windows too, unlike hardcoded /tmp)
    import tempfile
    import os
    tmp_dir = tempfile.gettempdir()
    pos_path = os.path.join(tmp_dir, "_margin_pos.json").replace("\\", "/")
    menu_path = os.path.join(tmp_dir, "_margin_menu.json").replace("\\", "/")
    pos.to_json(pos_path, orient="records")
    menu.to_json(menu_path, orient="records")

    import json as _json
    code = MARGIN_CODE_TEMPLATE.format(
        pos_path=pos_path,
        menu_path=menu_path,
        events_json=_json.dumps(price_change_events),
    )

    result = run_self_correcting_code(code, max_fix_attempts=3)

    if not result["ok"]:
        return {
            "findings": [{
                "agent": "margin",
                "claim": "Margin analysis failed after self-correction attempts",
                "number": None,
                "evidence": f"final error: {result['error']} | attempts: {result['attempts_log']}",
            }]
        }

    data = result["data"]
    findings = [
        {
            "agent": "margin",
            "claim": "Overall margin using menu.csv's static unit cost (naive baseline)",
            "number": data["naive_overall_margin_pct"],
            "evidence": f"revenue SAR {data['naive_overall_revenue']} vs cost SAR {data['naive_overall_cost']}, using unit_cost_sar as-is",
        }
    ]

    for impact in data["event_impacts"]:
        findings.append({
            "agent": "margin",
            "claim": (
                f"Supplier raised {impact['item']} cost by "
                f"{impact['supplier_pct_increase']}% effective {impact['effective_date']}; "
                f"menu_items.csv does not reflect this, so reported margin on affected "
                f"items is overstated after this date"
            ),
            "number": impact["supplier_pct_increase"],
            "evidence": (
                f"14-day window: revenue SAR {impact['affected_category_revenue_14d_before']} "
                f"before -> SAR {impact['affected_category_revenue_14d_after']} after; "
                f"{impact['note']}"
            ),
        })

    return {"findings": findings}


if __name__ == "__main__":
    from mock_data.mock_cleaned_data import write_mock_clean_data_dir

    fake_state = {"clean_data_dir": write_mock_clean_data_dir()}
    output = margin_agent(fake_state)
    for f in output["findings"]:
        print(f)
        print()