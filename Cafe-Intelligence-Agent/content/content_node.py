"""
content_node.py — the graph node person2.md points to: "graph.py's END is
where your content_agent node should attach. final_state['verified_findings']
is the list to build content ideas from."

Wraps content_agent.generate_content_ideas() as a CafeState -> dict node,
matching the exact function signature every other node in graph.py uses.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from content.content_agent import generate_content_ideas, ContentIdea
from content.local_context import get_calendar_context, search_local_events
from memory.store import WeeklyMemoryStore

if TYPE_CHECKING:
    from state import CafeState


def _week_id_to_dates(week_id: str) -> tuple[date, date]:
    """'2026-W29' -> (Monday, Sunday) of that ISO week."""
    try:
        year, week = week_id.split("-W")
        monday = date.fromisocalendar(int(year), int(week), 1)
        sunday = date.fromisocalendar(int(year), int(week), 7)
        return monday, sunday
    except (ValueError, IndexError):
        today = date.today()
        return today, today


def content_agent_node(state: "CafeState") -> dict:
    """Reads state['verified_findings'] + state['cafe_profile'] + state['week_id'],
    writes state['content_ideas'] (list[dict], JSON-serializable for graph state
    and for the LangSmith trace)."""
    verified_findings = state.get("verified_findings", [])
    cafe_profile = state.get("cafe_profile", {})

    # FIX: this used to fall back to datetime.now().strftime("%Y-W%W") when
    # no week_id was passed in. Python's %W week numbering is NOT ISO 8601 --
    # it can disagree with the ISO week number by a full week near year
    # boundaries. Every date-window function downstream of this (
    # load_source_for_week, item_selector.py, waste_analysis.py,
    # menu_engineering.py, posting_time.py) parses week_id via
    # date.fromisocalendar(), which expects a REAL ISO week number. A %W-
    # generated week_id could silently point those functions at the wrong
    # week. Using datetime.now().isocalendar() here keeps this in agreement
    # with every other consumer of week_id in the codebase.
    if state.get("week_id"):
        week_id = state["week_id"]
    else:
        iso_year, iso_week, _ = datetime.now().isocalendar()
        week_id = f"{iso_year}-W{iso_week:02d}"

    clean_data_dir = state.get("clean_data_dir", "clean_data")

    week_start, week_end = _week_id_to_dates(week_id)
    fallback_calendar = get_calendar_context(week_start, week_end, cafe_profile)
    local_events = search_local_events(cafe_profile, week_start)

    memory_store = WeeklyMemoryStore()

    ideas: list[ContentIdea] = generate_content_ideas(
        verified_findings, cafe_profile, fallback_calendar, local_events, clean_data_dir, memory_store,
        week_end=week_end
    )

    # Flag (not block) any idea that closely matches something proposed
    # before, per the assignment's "you approved this idea last month and
    # it didn't run" requirement.
    for idea in ideas:
        past = memory_store.find_matching_past_idea(idea.hook_en)
        if past:
            note = f"Similar idea proposed in {past['week_id']} (approved={past['approved']})."
            idea.memory_note = f"{idea.memory_note} {note}".strip()

    return {
        "content_ideas": [idea.__dict__ for idea in ideas],
        "messages": [{"role": "system", "content": f"content_agent produced {len(ideas)} ideas for {week_id}"}],
    }
