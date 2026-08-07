"""
memory_node.py — persists this run's outcome to long-term memory AFTER the
human approval decision is known, so next week's `find_matching_past_idea()`
can correctly say "you approved this idea last month and it didn't run" (or
"...and you rejected it") instead of just "you saw this idea."

Runs last in the graph, per person2.md: memory/store.py's WeeklyMemoryStore
is explicitly available to Person 3's report/approval flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from memory.store import WeeklyMemoryStore, build_week_summary
from report.run_archive import update_run_history

if TYPE_CHECKING:
    from state import CafeState


def memory_save_node(state: "CafeState") -> dict:
    week_id = state.get("week_id", "unknown-week")
    verified_findings = state.get("verified_findings", [])
    rejection_log = state.get("rejection_log", [])
    approved = state.get("report_approved", False)

    store = WeeklyMemoryStore()
    store.save_week(week_id, build_week_summary(verified_findings, rejection_log))

    for idea in state.get("content_ideas", []):
        store.save_content_idea(week_id, {"hook": idea.get("hook_en", "")}, approved=approved)

    # Closes the loop on the same run_history.json row report_node created —
    # that row was written BEFORE the human decision existed; now it does.
    run_id = state.get("run_archive_id")
    if run_id:
        update_run_history(run_id, report_approved=approved,
                            final_whatsapp_summary=state.get("whatsapp_summary", ""))

    return {
        "messages": [{"role": "system",
                       "content": f"memory_save_node: saved week {week_id} "
                                  f"({len(verified_findings)} findings, approved={approved})"}],
    }