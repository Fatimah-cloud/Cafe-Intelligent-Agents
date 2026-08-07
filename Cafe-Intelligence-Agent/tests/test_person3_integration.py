"""
tests/test_person3_integration.py — Person 3's test suite against Person 2's
REAL findings schema (state.py: `{agent, claim, number, evidence}`), not the
earlier mock-data contract.

Covers: content-idea grounding/validation, calendar tie-in correctness,
memory integration, report generation, and a full full_graph.py run
including the interrupt()/resume human-approval roundtrip.

Sandbox note: margin_agent/reviews_agent/critic_agent's LLM calls need
GOOGLE_API_KEY (Gemini) or network access this environment doesn't have —
see run_full_pipeline.py's `_install_llm_stubs()`. These tests reuse the
same stubbing approach so they run in any clean clone without secrets, and
exercise the REAL sales/operations/anomaly agents (no LLM needed there) plus
the real pandas math inside margin/reviews with only the free-text
extraction step stubbed.

Run: python -m pytest tests/test_person3_integration.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN_DATA = os.path.join(REPO_ROOT, "clean_data")
PROFILE_PATH = os.path.join(REPO_ROOT, "data_raw", "cafe_profile.json")

FAKE_MARGIN_EVENTS = [
    {"item": "roasted_coffee", "old_value": 88, "new_value": 96, "effective_date": "2026-04-15"},
    {"item": "full_fat_milk", "old_value": 7.10, "new_value": 8.40, "effective_date": "2026-05-01"},
]
FAKE_REVIEW_THEMES = [
    {"theme": "V60/filter coffee quality dropped", "review_count": 30, "example_quote": "V60 quality dropped recently"},
]


def _load_profile():
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _real_verified_findings() -> dict:
    """Runs the REAL sales/operations/anomaly agents (no LLM needed) plus
    margin/reviews/critic with only the LLM extraction step stubbed —
    matches run_full_pipeline.py's sandbox fallback. Skips (not fakes) if
    clean_data/ hasn't been generated yet."""
    import agents.margin as margin_mod
    import agents.reviews as reviews_mod
    import agents.critic as critic_mod
    from agents.sales import sales_agent
    from agents.operations import operations_agent
    from agents.anomaly import anomaly_agent

    state = {"clean_data_dir": CLEAN_DATA}
    findings = []
    findings += sales_agent(state)["findings"]
    findings += operations_agent(state)["findings"]
    findings += anomaly_agent(state)["findings"]

    with patch.object(margin_mod, "_get_llm") as mock_llm:
        mock_llm.return_value.invoke.return_value = MagicMock(content=json.dumps(FAKE_MARGIN_EVENTS))
        findings += margin_mod.margin_agent(state)["findings"]

    with patch.object(reviews_mod, "_get_llm") as mock_llm:
        mock_llm.return_value.invoke.return_value = MagicMock(content=json.dumps(FAKE_REVIEW_THEMES))
        findings += reviews_mod.reviews_agent(state)["findings"]

    with patch.object(critic_mod, "_get_llm") as mock_llm:
        mock_llm.return_value.invoke.return_value = MagicMock(content='{"ok": true, "target": "none", "feedback": ""}')
        critic_out = critic_mod.critic_agent({"revision_count": 0, "findings": findings})

    return critic_out


def _require_clean_data():
    if not os.path.exists(os.path.join(CLEAN_DATA, "quality_report.json")):
        import pytest
        pytest.skip("clean_data/ not found — run `python clean_data.py` first")


# ---------- content agent grounding ----------

def test_content_ideas_cite_real_verified_findings():
    _require_clean_data()
    from content.content_agent import generate_content_ideas, _validate_idea
    from content.local_context import get_calendar_context, search_local_events

    critic_out = _real_verified_findings()
    findings = critic_out["verified_findings"]
    assert len(findings) > 0

    profile = _load_profile()
    fallback_ctx = get_calendar_context(date(2026, 7, 13), date(2026, 7, 19), profile)
    local_events = search_local_events(profile, date(2026, 7, 13))

    ideas = generate_content_ideas(findings, profile, fallback_ctx, local_events, CLEAN_DATA, memory_store=None)
    assert len(ideas) == 3
    for idea in ideas:
        ok, reason = _validate_idea(idea, findings)
        assert ok, f"idea {idea.idea_id} failed validation: {reason}"
        assert idea.hook_en and idea.hook_ar
        assert idea.best_day and idea.best_time


def test_anomaly_finding_gets_correct_calendar_tie_in():
    """The real anomaly agent found spikes on 2026-03-20/21 — inside the
    dataset's documented Eid al-Fitr window. An idea built from that finding
    must say Eid al-Fitr, derived from the finding's OWN date, not from
    whatever the run's nominal 'current week' happens to be."""
    _require_clean_data()
    from content.content_agent import _calendar_context_for_finding
    from content.local_context import get_calendar_context

    profile = _load_profile()
    unrelated_fallback = get_calendar_context(date(2026, 7, 13), date(2026, 7, 19), profile)  # peak summer, NOT Eid

    finding = {"agent": "anomaly", "claim": "Flat White sales spike on 2026-03-21 (214.8% vs baseline)",
               "number": 4.83, "evidence": "..."}
    ctx = _calendar_context_for_finding(finding, profile, unrelated_fallback)
    assert ctx.is_eid, "finding dated inside the Eid al-Fitr window should resolve to an Eid calendar context"
    assert not ctx.is_ramadan  # Eid al-Fitr window here is just after Ramadan ends


def test_empty_findings_degrades_not_crashes():
    from content.content_agent import generate_content_ideas
    from content.local_context import get_calendar_context, search_local_events

    profile = _load_profile()
    ctx = get_calendar_context(date(2026, 7, 13), date(2026, 7, 19), profile)
    local_events = search_local_events(profile, date(2026, 7, 13))
    ideas = generate_content_ideas([], profile, ctx, local_events, CLEAN_DATA, memory_store=None)
    assert ideas == []


def test_no_cafe_name_hardcoded_in_person3_modules():
    for fname in ["content/content_agent.py", "content/local_context.py", "content/posting_time.py",
                  "content/item_matcher.py", "content/item_selector.py", "content/translate.py",
                  "content/content_node.py", "report/report_generator.py", "report/charts.py",
                  "report/report_node.py", "report/approval_node.py", "memory/memory_node.py"]:
        text = open(os.path.join(REPO_ROOT, fname), encoding="utf-8").read()
        assert "Qahwa Saihat" not in text and "qahwa" not in text.lower(), f"{fname} hardcodes the pilot cafe's name"


def test_featured_idea_is_margin_and_trend_grounded_not_just_revenue():
    """Regression test for the brief's literal requirement: 'push the
    high-margin item that's trending up; don't promote the thing you're
    about to run out of.' Confirms idea #1 is NOT simply the sales
    best-seller (which is ranked by revenue, not margin) and IS grounded in
    a real margin/trend number."""
    _require_clean_data()
    from content.item_selector import select_featured_item

    featured = select_featured_item(CLEAN_DATA)
    assert featured is not None
    assert featured.margin_pct > 0
    assert "margin" in featured.evidence_string().lower()
    assert not featured.stockout_risk or featured.stockout_reason, \
        "if stockout_risk is True, there must be a concrete reason string, never a bare flag"


def test_content_ideas_first_idea_uses_item_selector():
    _require_clean_data()
    from content.content_agent import generate_content_ideas
    from content.local_context import get_calendar_context, search_local_events
    from content.item_selector import select_featured_item

    critic_out = _real_verified_findings()
    findings = critic_out["verified_findings"]
    profile = _load_profile()
    ctx = get_calendar_context(date(2026, 7, 13), date(2026, 7, 19), profile)
    local_events = search_local_events(profile, date(2026, 7, 13))
    ideas = generate_content_ideas(findings, profile, ctx, local_events, CLEAN_DATA, memory_store=None)

    expected = select_featured_item(CLEAN_DATA)
    assert ideas[0].product_sku == expected.sku, \
        "idea #1 should always be the margin/trend/stockout-selected item, not a revenue-ranked one"
    assert ideas[0].data_evidence, "idea #1 must carry its own computed evidence string"


# ---------- bonus features ----------

def test_waste_analysis_produces_real_numbers():
    _require_clean_data()
    from content.waste_analysis import compute_waste_report, total_monthly_waste_cost

    lines = compute_waste_report(CLEAN_DATA)
    assert len(lines) > 0, "expected at least one bakery/food item with waste data"
    for line in lines:
        assert line.monthly_waste_cost_sar >= 0
        assert line.recommended_weekly_order > 0
        assert line.avg_weekly_ordered >= line.avg_weekly_sold - 0.01, \
            "sanity check: ordered should not be meaningfully below sold on real data"
    total = total_monthly_waste_cost(CLEAN_DATA)
    assert total > 0, "real dataset has known waste (croissants etc.) - total must be nonzero"


def test_menu_engineering_covers_full_menu_with_valid_quadrants():
    _require_clean_data()
    from content.menu_engineering import compute_menu_matrix
    import pandas as pd

    matrix = compute_menu_matrix(CLEAN_DATA)
    menu = pd.read_csv(os.path.join(CLEAN_DATA, "menu_items.csv"))
    assert len(matrix) == len(menu), "every menu item should get a quadrant classification"
    for e in matrix:
        assert e.quadrant in ("star", "plowhorse", "puzzle", "dog")
        assert 0.0 <= e.margin_pct <= 1.0
        assert e.recommendation_en, "every quadrant entry needs a stated recommendation"


def test_cost_tracker_blocks_over_budget_calls():
    from content.cost_tracker import CostTracker

    tracker = CostTracker(budget_usd=0.0001)  # deliberately tiny
    assert not tracker.can_afford(0.001)
    tracker.record_skip("test")
    assert tracker.calls_skipped_over_budget == 1
    assert "SKIPPED" in tracker.summary() or tracker.calls_skipped_over_budget == 1


def test_product_grounding_catches_invented_mention_even_without_product_sku():
    """Regression test for a real run: the LLM cleared/never set product_sku
    but still wrote the invented product's name directly into the Arabic
    hook text ('have you tried our Iced Latte?') while an overall-rating
    finding was the only citation. Structured-field-only checking missed
    this; the free-text scan must catch it too."""
    _require_clean_data()
    from content.content_agent import ContentIdea, _validate_product_grounding
    from content.item_matcher import load_menu

    menu = load_menu(CLEAN_DATA)
    idea = ContentIdea(
        idea_id="idea_x", hook_en="Come cool down with us!",
        hook_ar="مقهى بـ تقييم 4.33.. جربتوا آيس لاتيه حقنا؟",
        format="carousel", product_sku=None, product_name_en=None, product_name_ar=None,
        rationale_en="...", rationale_ar="...", data_evidence="",
        cited_claims=["Overall average rating across 520 reviews"],
        best_day="Friday", best_time="9 PM", posting_grounding="...", local_context_used="...",
    )
    ok, reason = _validate_product_grounding(idea, menu)
    assert not ok, "an invented product mention in Arabic prose (with no matching citation) must be caught"
    assert "Iced Latte" in reason


def test_translate_never_leaks_raw_unrounded_float():
    """Regression test: translate.py's LLM prompt used to hand the model a
    raw Python float ('number=6257.772972972973'), and a real run showed the
    model echoing that exact string into the Arabic output instead of a
    clean number. The template fallback (exercised here, no API key) must
    never produce that pattern either."""
    _require_clean_data()
    from content.translate import _template_translate_one
    from content.item_matcher import load_menu

    menu = load_menu(CLEAN_DATA)
    finding = {"agent": "sales", "claim": "Matcha Latte is the best seller by average weekly revenue",
               "number": 6257.772972972973, "evidence": "..."}
    text = _template_translate_one(finding, menu)
    assert "6257.772972972973" not in text, "raw unrounded float must never appear in translated output"
    assert "6,257.8" in text


# ---------- scheduler (full pipeline, not just analysis) ----------

def test_full_scheduler_pauses_and_resume_completes():
    """Regression test for the gap where Person 2's scheduler only fired
    graph.py (analysis-only) and never proved the content/report/approval
    part runs unattended too."""
    _require_clean_data()
    import agents.margin as margin_mod
    import agents.reviews as reviews_mod
    import agents.critic as critic_mod

    pending_path = os.path.join(REPO_ROOT, "pending_approvals.json")
    if os.path.exists(pending_path):
        os.remove(pending_path)

    with patch.object(margin_mod, "_get_llm") as m1, \
         patch.object(reviews_mod, "_get_llm") as m2, \
         patch.object(critic_mod, "_get_llm") as m3:
        m1.return_value.invoke.return_value = MagicMock(content=json.dumps(FAKE_MARGIN_EVENTS))
        m2.return_value.invoke.return_value = MagicMock(content=json.dumps(FAKE_REVIEW_THEMES))
        m3.return_value.invoke.return_value = MagicMock(content='{"ok": true, "target": "none", "feedback": ""}')

        from scheduler.full_scheduler import run_weekly_full_cycle, resume_pending_approval, _load_pending

        result = run_weekly_full_cycle(clean_data_dir=CLEAN_DATA)
        assert "__interrupt__" in result, "scheduled run should pause at the human-approval breakpoint"

        pending = _load_pending()
        assert len(pending) >= 1
        week_id = list(pending.keys())[-1]
        assert pending[week_id]["resolved"] is False

        final = resume_pending_approval(week_id, "APPROVE")
        assert final.get("report_approved") is True

        pending_after = _load_pending()
        assert pending_after[week_id]["resolved"] is True

    if os.path.exists(pending_path):
        os.remove(pending_path)


# ---------- report generation ----------

def test_whatsapp_summary_is_diverse_across_agents():
    """Regression test for the bug where a naive findings[:5] could show 5
    anomaly findings and nothing else, since the merged findings list order
    depends on which parallel analyst branch finished first."""
    _require_clean_data()
    from report.report_generator import _pick_diverse_findings

    findings = [{"agent": "anomaly", "claim": f"anomaly {i}", "number": 1} for i in range(8)]
    findings += [{"agent": "sales", "claim": "sales finding", "number": 2}]
    findings += [{"agent": "margin", "claim": "margin finding", "number": 3}]

    diverse = _pick_diverse_findings(findings, n=5)
    agents_shown = {f["agent"] for f in diverse}
    assert "sales" in agents_shown and "margin" in agents_shown, \
        "diverse pick should surface sales/margin even though anomaly dominates the list"


def test_html_report_renders_with_no_broken_template_vars():
    _require_clean_data()
    from content.content_agent import generate_content_ideas, ContentIdea
    from content.local_context import get_calendar_context, search_local_events
    from content.translate import translate_findings_to_arabic
    from report.report_generator import build_html_report

    critic_out = _real_verified_findings()
    findings = critic_out["verified_findings"]
    profile = _load_profile()
    ctx = get_calendar_context(date(2026, 7, 13), date(2026, 7, 19), profile)
    local_events = search_local_events(profile, date(2026, 7, 13))
    ideas = generate_content_ideas(findings, profile, ctx, local_events, CLEAN_DATA, memory_store=None)

    ideas = generate_content_ideas(findings, profile, ctx, local_events, CLEAN_DATA, memory_store=None)
    findings_ar = translate_findings_to_arabic(findings, CLEAN_DATA)

    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "report.html")
        build_html_report("2026-W99", findings, ideas, profile, CLEAN_DATA, ["quality note"],
                           critic_out["critic_feedback"], critic_out["rejection_log"],
                           critic_out.get("revision_count", 0), out_path, findings_ar)
        html = open(out_path, encoding="utf-8").read()
        assert "{{" not in html and "{%" not in html
        assert 'dir="rtl"' in html
        assert html.count("data:image/png;base64") >= 1
        assert "None" not in html


# ---------- full graph: real integration incl. human breakpoint ----------

def test_full_graph_hits_interrupt_and_resumes():
    _require_clean_data()
    import agents.margin as margin_mod
    import agents.reviews as reviews_mod
    import agents.critic as critic_mod
    from langgraph.types import Command

    with patch.object(margin_mod, "_get_llm") as m1, \
         patch.object(reviews_mod, "_get_llm") as m2, \
         patch.object(critic_mod, "_get_llm") as m3:
        m1.return_value.invoke.return_value = MagicMock(content=json.dumps(FAKE_MARGIN_EVENTS))
        m2.return_value.invoke.return_value = MagicMock(content=json.dumps(FAKE_REVIEW_THEMES))
        m3.return_value.invoke.return_value = MagicMock(content='{"ok": true, "target": "none", "feedback": ""}')

        from full_graph import full_graph
        profile = _load_profile()
        config = {"configurable": {"thread_id": "test-thread-1"}, "recursion_limit": 40}
        initial_state = {
            "clean_data_dir": CLEAN_DATA, "cafe_profile": profile, "data_quality_log": [],
            "findings": [], "revision_count": 0, "critic_target": "", "critic_feedback": "",
            "week_id": "2026-Wtest1",
        }
        result = full_graph.invoke(initial_state, config=config)
        assert "__interrupt__" in result, "graph should pause at human_approval_node"
        assert "whatsapp_summary" in result["__interrupt__"][0].value

        final = full_graph.invoke(Command(resume="APPROVE"), config=config)
        assert final.get("report_approved") is True
        assert final.get("content_ideas")
        assert final.get("report_html")


def test_full_graph_reject_path_does_not_approve():
    _require_clean_data()
    import agents.margin as margin_mod
    import agents.reviews as reviews_mod
    import agents.critic as critic_mod
    from langgraph.types import Command

    with patch.object(margin_mod, "_get_llm") as m1, \
         patch.object(reviews_mod, "_get_llm") as m2, \
         patch.object(critic_mod, "_get_llm") as m3:
        m1.return_value.invoke.return_value = MagicMock(content=json.dumps(FAKE_MARGIN_EVENTS))
        m2.return_value.invoke.return_value = MagicMock(content=json.dumps(FAKE_REVIEW_THEMES))
        m3.return_value.invoke.return_value = MagicMock(content='{"ok": true, "target": "none", "feedback": ""}')

        from full_graph import full_graph
        profile = _load_profile()
        config = {"configurable": {"thread_id": "test-thread-2"}, "recursion_limit": 40}
        initial_state = {
            "clean_data_dir": CLEAN_DATA, "cafe_profile": profile, "data_quality_log": [],
            "findings": [], "revision_count": 0, "critic_target": "", "critic_feedback": "",
            "week_id": "2026-Wtest2",
        }
        full_graph.invoke(initial_state, config=config)
        final = full_graph.invoke(Command(resume="REJECT"), config=config)
        assert final.get("report_approved") is False


if __name__ == "__main__":
    import traceback
    tests = [(name, obj) for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed, skipped = 0, 0, 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as e:
            if "skip" in type(e).__name__.lower():
                print(f"SKIP {name}: {e}")
                skipped += 1
                continue
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)
