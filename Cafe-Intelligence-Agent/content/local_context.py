"""
local_context.py — what's happening locally, this week, for this cafe.

Two layers, deliberately separated:

1. `CalendarContext` — deterministic, no network call, no API key needed. Reads
   `cafe_profile.json` for opening-hours overrides and derives which calendar regime
   (Ramadan / Eid / normal / peak-summer) a given week falls in, using the fixed
   Hijri-calendar-derived date ranges for the KSA. This never hardcodes the cafe
   name; it only reads dates and the profile's own `opening_hours` keys.

2. `search_local_events()` — optional Tavily web search for events/weather specific
   to the cafe's city/governorate (read from the profile, never hardcoded). If
   TAVILY_API_KEY isn't set or the `tavily-python` package isn't installed, this
   degrades to an empty result with a note — the content agent still runs, it just
   has one less grounding signal, and the report says so rather than pretending.

Both are meant to be swapped/extended per-cafe purely through `cafe_profile.json` —
onboarding cafe #2 means their profile has different `city`/`governorate` and
(if their calendar differs) a different `CALENDAR_EVENTS` table, which is the one
piece of this file that is genuinely KSA-specific and should move into
`cafe_profile.json` as a `calendar_events` key for a non-Saudi cafe. Left inline here
for now since both pilot cafes are in the Eastern Province and this ships Wednesday;
flagged in the "second cafe" section of the README.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

# Fixed 2026 KSA calendar context — see data_raw/DATA_DICTIONARY.md "Calendar context".
# Move to cafe_profile.json (as `calendar_events`) if a future cafe needs a different
# calendar (different country, different fiscal year, etc).
CALENDAR_EVENTS_2026 = [
    {"name_en": "Ramadan", "name_ar": "رمضان", "start": date(2026, 2, 17), "end": date(2026, 3, 19),
     "note": "Cafe should read opening_hours.ramadan from cafe_profile.json — behaves like a night business."},
    {"name_en": "Eid al-Fitr", "name_ar": "عيد الفطر", "start": date(2026, 3, 20), "end": date(2026, 3, 23), "note": ""},
    {"name_en": "Founding Day", "name_ar": "يوم التأسيس", "start": date(2026, 2, 22), "end": date(2026, 2, 22), "note": ""},
    {"name_en": "Eid al-Adha", "name_ar": "عيد الأضحى", "start": date(2026, 5, 27), "end": date(2026, 5, 30), "note": ""},
    {"name_en": "Peak summer heat", "name_ar": "ذروة الحر الصيفي", "start": date(2026, 6, 1), "end": date(2026, 8, 31),
     "note": "Daytime 40C+, push iced/cold-brew and delivery/AC dine-in over outdoor seating."},
]


@dataclass
class CalendarContext:
    week_start: date
    week_end: date
    active_events: list[dict] = field(default_factory=list)
    opening_hours_note: Optional[str] = None

    @property
    def is_ramadan(self) -> bool:
        return any(e["name_en"] == "Ramadan" for e in self.active_events)

    @property
    def is_eid(self) -> bool:
        return any("Eid" in e["name_en"] for e in self.active_events)

    def summary_en(self) -> str:
        if not self.active_events:
            return "No special calendar events this week."
        return "; ".join(f"{e['name_en']}" + (f" — {e['note']}" if e["note"] else "") for e in self.active_events)

    def summary_ar(self) -> str:
        if not self.active_events:
            return "لا توجد مناسبات خاصة هذا الأسبوع."
        return "؛ ".join(e["name_ar"] for e in self.active_events)


def get_calendar_context(week_start: date, week_end: date, cafe_profile: dict,
                          calendar_events: Optional[list[dict]] = None) -> CalendarContext:
    """Deterministic, offline. `calendar_events` lets a future cafe override the table
    via cafe_profile.json instead of editing this file."""
    events = calendar_events or CALENDAR_EVENTS_2026
    active = [e for e in events if e["start"] <= week_end and e["end"] >= week_start]

    ctx = CalendarContext(week_start=week_start, week_end=week_end, active_events=active)

    is_ramadan = any(e["name_en"] == "Ramadan" for e in active)
    hours = cafe_profile.get("opening_hours", {})
    if is_ramadan and "ramadan" in hours:
        ctx.opening_hours_note = (
            f"Ramadan hours in effect per cafe_profile.json: {hours['ramadan']} "
            f"(vs default {hours.get('default', '?')}) — the cafe is effectively a night business this week."
        )
    return ctx


def search_local_events(cafe_profile: dict, week_start: date) -> dict:
    """Optional Tavily search for events/weather near the cafe's city. Returns
    {"available": bool, "results": [...], "note": str}. Never raises — a missing
    key or network failure degrades to available=False with a note, not a crash,
    matching the "break it on purpose, degrade and report" testing requirement.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    city = cafe_profile.get("city", "")
    governorate = cafe_profile.get("governorate", "")

    if not api_key:
        return {"available": False, "results": [],
                "note": "TAVILY_API_KEY not set — local-events search skipped, "
                        "content agent falls back to calendar context only."}

    try:
        from tavily import TavilyClient
    except ImportError:
        return {"available": False, "results": [],
                "note": "tavily-python not installed — local-events search skipped."}

    try:
        client = TavilyClient(api_key=api_key)
        query = f"events weather {city} {governorate} week of {week_start.isoformat()}"
        resp = client.search(query=query, max_results=5, search_depth="basic")
        results = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content", "")[:300]}
                   for r in resp.get("results", [])]
        return {"available": True, "results": results, "note": f"Tavily search: '{query}'"}
    except Exception as e:  # noqa: BLE001 — deliberately broad, this must never take the run down
        return {"available": False, "results": [], "note": f"Tavily search failed ({type(e).__name__}: {e}) — degraded to calendar context only."}
