"""
report_node.py — the graph node that turns state['content_ideas'] +
state['verified_findings'] into the two artifacts the owner actually
receives: state['whatsapp_summary'] and state['report_html'].

Runs immediately after content_agent_node. Both read the same state fields
so they can never drift out of sync with what the critic actually approved.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING

from content.content_agent import ContentIdea
from content.translate import translate_findings_to_arabic
from report.report_generator import build_whatsapp_summary, build_html_report
from report.run_archive import archive_run_files, append_run_history

if TYPE_CHECKING:
    from state import CafeState

OUTPUT_DIR = "output"


def report_node(state: "CafeState") -> dict:
    verified_findings = state.get("verified_findings", [])
    content_ideas_dicts = state.get("content_ideas", [])
    content_ideas = [ContentIdea(**d) for d in content_ideas_dicts]
    cafe_profile = state.get("cafe_profile", {})
    week_id = state.get("week_id", "unknown-week")
    clean_data_dir = state.get("clean_data_dir", "clean_data")

    # FIX: computed ONCE here and passed to both build_whatsapp_summary()
    # and build_html_report() below. build_html_report() used to call
    # translate_findings_to_arabic() again internally on the same
    # verified_findings -- doubling the LLM cost/latency of every run for
    # identical output. See report_generator.py's build_html_report()
    # docstring for the full explanation.
    findings_ar = translate_findings_to_arabic(verified_findings, clean_data_dir)

    whatsapp = build_whatsapp_summary(week_id, verified_findings, content_ideas, cafe_profile, findings_ar, clean_data_dir)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    whatsapp_path = os.path.join(OUTPUT_DIR, f"whatsapp_summary_{week_id}.txt")
    with open(whatsapp_path, "w", encoding="utf-8") as fh:
        fh.write(whatsapp)

    html_path = os.path.join(OUTPUT_DIR, f"report_{week_id}.html")
    build_html_report(
        week_id=week_id,
        verified_findings=verified_findings,
        content_ideas=content_ideas,
        cafe_profile=cafe_profile,
        clean_data_dir=clean_data_dir,
        data_quality_log=state.get("data_quality_log", []),
        critic_feedback=state.get("critic_feedback", ""),
        rejection_log=state.get("rejection_log", []),
        revision_count=state.get("revision_count", 0),
        output_path=html_path,
        findings_ar=findings_ar,  # <- reuses the translation computed above
        generated_at=datetime.now().isoformat(),
    )
    report_html = open(html_path, encoding="utf-8").read()

    # Permanent, never-overwritten archive of this specific run — separate
    # from the "latest" files above, which intentionally DO get overwritten
    # on a re-run of the same week_id. See run_archive.py's module
    # docstring for why both exist.
    run_id = archive_run_files(week_id, report_html, whatsapp)
    append_run_history(
        run_id,
        week_id=week_id,
        verified_finding_count=len(verified_findings),
        rejection_count=len(state.get("rejection_log", [])),
        revision_count=state.get("revision_count", 0),
        critic_feedback=state.get("critic_feedback", ""),
        content_idea_count=len(content_ideas),
        data_quality_log=state.get("data_quality_log", []),
    )

    return {
        "whatsapp_summary": whatsapp,
        "report_html": report_html,
        "run_archive_id": run_id,
        "messages": [{"role": "system", "content": f"report_node wrote {html_path} (archived as {run_id})"}],
    }
