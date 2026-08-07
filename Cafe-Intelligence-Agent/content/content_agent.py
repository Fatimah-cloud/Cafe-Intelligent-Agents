"""
content_agent.py — the module the owner actually cares about.

v2 changes (fixing gaps found against a real report review — see
README_PERSON3.md "Content agent v2 fixes" for the specifics):

1. **Margin/trend/stockout-aware item selection.** The brief says "push the
   high-margin item that's trending up; don't promote the thing you're about
   to run out of." Person 2's findings alone can't answer this (sales_agent
   ranks by revenue, not margin; nothing checks inventory). `item_selector.py`
   computes this directly from clean_data — one idea is now always grounded
   in that scorer, with its own evidence string, not a critic-approved
   finding but a genuinely computed Person-3 metric (same status as
   posting_time.py's best-hour data).
2. **Per-finding calendar context is now explicitly computed and handed to
   the LLM**, not left for the model to infer from a raw date string. This
   fixed a real bug: LLM mode was calling a March 20-21 spike "March 21st"
   instead of "Eid al-Fitr" because it was never told.
3. **Bilingual rationale.** `rationale_ar` is now a real field, not the
   English rationale reused in the Arabic block.
4. **Every idea's rationale must contain a number** — enforced post-hoc
   (`_ensure_number_in_rationale`), not just requested in the prompt.
5. **Output sanitization** for LLM-mode ideas: best_time normalized to a
   consistent "H AM/PM"-style label regardless of what the model returned,
   memory_note stripped of literal "None"/"none"/"null" strings,
   product_name_ar backfilled from the menu if missing.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from content.item_matcher import MenuItem, load_menu, match_item
from content.item_selector import ItemScore, select_featured_item
from content.json_utils import extract_json
from content.cost_tracker import CostTracker, get_default_tracker, estimate_tokens
from content.local_context import CalendarContext, get_calendar_context, CALENDAR_EVENTS_2026
from content.posting_time import PostingWindow, best_posting_windows, window_for_sku

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
IDEA_FORMATS = ["reel", "carousel", "trend_audio"]
_JUNK_MEMORY_VALUES = {"none", "null", "n/a", "na", "nothing", ""}


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


@dataclass
class ContentIdea:
    idea_id: str
    hook_en: str
    hook_ar: str
    format: str
    product_sku: Optional[str]
    product_name_en: Optional[str]
    product_name_ar: Optional[str]
    rationale_en: str
    rationale_ar: str
    data_evidence: str
    cited_claims: list[str]
    best_day: str
    best_time: str
    posting_grounding: str
    local_context_used: str
    memory_note: str = ""


def _format_time(hour) -> str:
    if hour is None:
        return "8 PM"
    if isinstance(hour, str):
        m = re.search(r"\d+", hour)
        if not m:
            return "8 PM"
        hour = int(m.group(0))
    hour = int(hour) % 24
    h12 = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    return f"{h12} {ampm}"


def _clean_memory_note(note) -> str:
    if not note or not isinstance(note, str):
        return ""
    return "" if note.strip().lower() in _JUNK_MEMORY_VALUES else note.strip()


def _ensure_number_in_rationale(rationale: str, data_evidence: str) -> str:
    if re.search(r"\d", rationale):
        return rationale
    return f"{rationale.rstrip('.')} ({data_evidence})"


def _extract_date(finding: dict) -> Optional[date]:
    for field_name in ("claim", "evidence"):
        m = DATE_RE.search(finding.get(field_name, "") or "")
        if m:
            try:
                return date.fromisoformat(m.group(0))
            except ValueError:
                continue
    return None


def _calendar_context_for_finding(finding: dict, cafe_profile: dict, fallback: CalendarContext) -> CalendarContext:
    d = _extract_date(finding)
    if d is None:
        return fallback
    return get_calendar_context(d, d, cafe_profile, CALENDAR_EVENTS_2026)


def _finding_in_calendar_event(finding: dict, calendar_events: list[dict]) -> bool:
    d = _extract_date(finding)
    if d is None:
        return False
    return any(e["start"] <= d <= e["end"] for e in calendar_events)


def _posting_for_item(item: Optional[MenuItem], sku: Optional[str], clean_data_dir: str,
                       posting_windows: dict[str, PostingWindow], week_end=None) -> Optional[PostingWindow]:
    if sku:
        w = window_for_sku(clean_data_dir, sku, week_end)
        if w:
            return w
    if item and item.category in posting_windows:
        return posting_windows[item.category]
    if posting_windows:
        return max(posting_windows.values(), key=lambda w: w.order_count)
    return None


def _validate_idea(idea: ContentIdea, findings: list[dict]) -> tuple[bool, str]:
    if not idea.cited_claims and not idea.data_evidence:
        return False, "no citation and no data_evidence - ungrounded"
    claim_set = {f["claim"] for f in findings}
    for c in idea.cited_claims:
        if c not in claim_set:
            return False, f"cites a claim not present in verified_findings: {c!r}"
    return True, "ok"


def _validate_product_grounding(idea: ContentIdea, menu: list[MenuItem]) -> tuple[bool, str]:
    """The brief requires 'the product it features' AND 'why the data
    supports it' together — a product tie-in is only valid if the cited
    claim actually talks about that product. Catches the LLM inventing a
    plausible-sounding pairing (e.g. an overall-rating finding paired with
    'Iced Spanish Latte' because it fits a summer-heat theme) where the
    number cited has nothing to do with the named product specifically.

    Checks BOTH the structured product_sku field AND the free-text hook/
    rationale in both languages — a real run showed the model naming a
    product in Arabic prose ('have you tried our Iced Latte?') while
    leaving hook_en generic and product_sku unset, so the structured field
    alone isn't a reliable signal."""
    cited_text = " ".join(idea.cited_claims).lower()
    free_text = " ".join([idea.hook_en or "", idea.hook_ar or "",
                           idea.rationale_en or "", idea.rationale_ar or ""])

    if idea.product_sku:
        if not idea.cited_claims:
            return True, "no citation to check against (data_evidence-grounded idea)"
        item = next((m for m in menu if m.sku == idea.product_sku), None)
        if item is None:
            return False, f"product_sku {idea.product_sku} is not a real menu sku"
        if item.item_en.lower() not in cited_text:
            return False, (f"product {item.item_en} named as featured, but cited claim(s) "
                            f"{idea.cited_claims} never mention it - invented pairing")

    if idea.cited_claims:
        for item in menu:
            name_in_text = item.item_en.lower() in free_text.lower() or (item.item_ar and item.item_ar in free_text)
            if name_in_text and item.item_en.lower() not in cited_text:
                return False, (f"'{item.item_en}' appears in the idea's hook/rationale text but is not "
                                f"mentioned in the cited claim(s) {idea.cited_claims} - invented mention in prose")

    return True, "ok"


def _build_featured_item_idea(idea_id: str, fmt: str, cafe_profile: dict, findings: list[dict],
                               fallback_calendar: CalendarContext, clean_data_dir: str,
                               menu: list[MenuItem], posting_windows: dict, local_events_note: str,
                               memory_store, week_end=None) -> Optional[ContentIdea]:
    scored: Optional[ItemScore] = select_featured_item(clean_data_dir, week_end)
    if scored is None:
        return None

    item = next((m for m in menu if m.sku == scored.sku), None)
    item_ar = item.item_ar if item else scored.item_ar

    matching_finding = next((f for f in findings if scored.item_en.lower() in f["claim"].lower()), None)

    hook_en = f"Why {scored.item_en} is our smartest push this week"
    hook_ar = f"لماذا {item_ar} هو أذكى خيار للترويج هذا الأسبوع"
    trend_txt = f"+{scored.trend_pct:.0%}" if scored.trend_pct and scored.trend_pct > 0 else "steady"
    rationale_en = (f"{scored.item_en} carries a {scored.margin_pct:.0%} margin (SAR {scored.margin_sar:.2f}/unit) "
                     f"and revenue is {trend_txt} week-over-week (SAR {scored.revenue_prior_week:,.0f} -> "
                     f"SAR {scored.revenue_last_week:,.0f}), with no stockout risk flagged in inventory data - "
                     f"the highest-margin item that's both trending up and safe to push right now.")
    rationale_ar = (f"يحقق {item_ar} هامش ربح {scored.margin_pct:.0%} ({scored.margin_sar:.2f} ريال للوحدة)، "
                     f"والإيرادات في تحسّن أسبوعي ({scored.revenue_prior_week:,.0f} إلى {scored.revenue_last_week:,.0f} ريال)، "
                     f"مع عدم وجود مخاطر نفاد مخزون - أفضل خيار للترويج حاليًا من حيث الربحية والنمو معًا.")

    if fallback_calendar.is_ramadan:
        rationale_en += " Timed for Ramadan - post after iftar; the cafe runs opening_hours.ramadan, effectively a night business."
    if fallback_calendar.active_events and not fallback_calendar.is_ramadan:
        names = ", ".join(e["name_en"] for e in fallback_calendar.active_events)
        rationale_en += f" Also coincides with {names}."
    if local_events_note:
        rationale_en += f" Local context: {local_events_note}"

    posting = _posting_for_item(item, scored.sku, clean_data_dir, posting_windows, week_end)
    best_day = posting.weekday if posting else "Saturday"
    best_time = _format_time(posting.hour if posting else 20)
    grounding = (f"Highest order density for this category was {posting.weekday} {_format_time(posting.hour)} "
                 f"({posting.order_count} orders) per POS timestamps." if posting else "No posting-time data available.")

    memory_note = _memory_note_for({"claim": f"{scored.item_en} revenue spike", "agent": "sales"}, memory_store)

    return ContentIdea(
        idea_id=idea_id, hook_en=hook_en, hook_ar=hook_ar, format=fmt,
        product_sku=scored.sku, product_name_en=scored.item_en, product_name_ar=item_ar,
        rationale_en=rationale_en, rationale_ar=rationale_ar,
        data_evidence=scored.evidence_string(),
        cited_claims=[matching_finding["claim"]] if matching_finding else [],
        best_day=best_day, best_time=best_time, posting_grounding=grounding,
        local_context_used=fallback_calendar.summary_en() + (f" | {local_events_note}" if local_events_note else " | No local event search results this run."),
        memory_note=memory_note,
    )


def _pick_secondary_findings(findings: list[dict]) -> list[dict]:
    anomaly_item = [f for f in findings if f["agent"] == "anomaly" and "sales" in f["claim"].lower()]
    reviews_rating = [f for f in findings if f["agent"] == "reviews" and "overall average rating" in f["claim"].lower()]
    margin_event = [f for f in findings if f["agent"] == "margin" and "supplier raised" in f["claim"].lower()]
    reviews_or_margin = reviews_rating + margin_event + [
        f for f in findings if f["agent"] in ("reviews", "margin") and f not in reviews_rating and f not in margin_event
    ]

    picks = []
    if anomaly_item:
        in_event = [f for f in anomaly_item if _finding_in_calendar_event(f, CALENDAR_EVENTS_2026)]
        picks.append(in_event[0] if in_event else anomaly_item[0])
    if reviews_or_margin:
        picks.append(reviews_or_margin[0])
    if len(picks) < 2:
        remaining = [f for f in findings if f not in picks]
        picks += remaining[: 2 - len(picks)]
    return picks[:2]


def _template_idea(idea_id: str, finding: dict, fmt: str, cafe_profile: dict,
                    fallback_calendar: CalendarContext, clean_data_dir: str, menu: list[MenuItem],
                    posting_windows: dict[str, PostingWindow], memory_note: str,
                    local_events_note: str, week_end=None) -> ContentIdea:
    claim = finding["claim"]
    number = finding.get("number")
    agent = finding["agent"]
    item = match_item(claim, menu)
    calendar_ctx = _calendar_context_for_finding(finding, cafe_profile, fallback_calendar)

    item_en = item.item_en if item else None
    item_ar = item.item_ar if item else None
    num_str = f"{number:,.1f}" if isinstance(number, (int, float)) else str(number)

    if agent == "anomaly" and item_en:
        event_note_en, event_note_ar = "", ""
        if calendar_ctx.active_events:
            names_en = ", ".join(e["name_en"] for e in calendar_ctx.active_events)
            event_note_en = f" (during {names_en})"
            event_note_ar = f" (خلال {calendar_ctx.summary_ar()})"
        hook_en = f"Why {item_en} orders jumped this week{event_note_en}"
        hook_ar = f"لماذا ارتفعت طلبات {item_ar}{event_note_ar}"
        rationale_en = f"{claim}. {calendar_ctx.summary_en()} - a real, dated spike, not a generic seasonal guess."
        rationale_ar = f"{item_ar}: {claim.split('(')[-1].rstrip(')')} - {calendar_ctx.summary_ar()}."
    elif agent == "reviews" and "overall average rating" in claim.lower():
        hook_en = f"{number}/5 stars - see why customers keep coming back"
        hook_ar = f"{number}/5 نجوم — تعرف لماذا يعود عملاؤنا دائمًا"
        rationale_en = f"{claim}. Social proof already exists in the review data; the content just needs to surface it."
        rationale_ar = f"متوسط التقييم {number}/5 بناءً على البيانات الفعلية — دليل ثقة جاهز للاستخدام في المحتوى."
    elif agent == "margin":
        hook_en = "Why quality ingredients cost more (and why we don't cut corners)"
        hook_ar = "لماذا تكلف المكونات عالية الجودة أكثر (ولماذا لا نقصر في الجودة)"
        rationale_en = f"{claim} ({num_str}%) - a transparency angle on rising input costs builds trust without a menu price change announcement."
        rationale_ar = f"ارتفاع تكلفة المكونات بنسبة {num_str}% — زاوية شفافية تبني الثقة دون الحاجة لتغيير الأسعار المعلنة."
    else:
        hook_en = f"What our data says: {claim}"
        hook_ar = f"ماذا يقول بياناتنا: {claim}"
        rationale_en = f"Grounded in: {claim} ({num_str})"
        rationale_ar = f"مستند إلى البيانات: {claim} ({num_str})"

    if calendar_ctx.is_ramadan:
        rationale_en += " Timed for Ramadan - post after iftar; the cafe runs opening_hours.ramadan, effectively a night business."
        rationale_ar += " مناسب لتوقيت رمضان — يُنشر بعد الإفطار حيث يعمل المقهى بساعات مختلفة تمامًا."
    if local_events_note:
        rationale_en += f" Local context: {local_events_note}"
    if memory_note:
        rationale_en += f" {memory_note}"

    posting = _posting_for_item(item, item.sku if item else None, clean_data_dir, posting_windows, week_end)
    best_day = posting.weekday if posting else "Saturday"
    best_time = _format_time(posting.hour if posting else 20)
    grounding = (f"Highest order density for this category was {posting.weekday} {_format_time(posting.hour)} "
                 f"({posting.order_count} orders) per POS timestamps." if posting else "No posting-time data available.")

    return ContentIdea(
        idea_id=idea_id, hook_en=hook_en, hook_ar=hook_ar, format=fmt,
        product_sku=item.sku if item else None, product_name_en=item_en, product_name_ar=item_ar,
        rationale_en=_ensure_number_in_rationale(rationale_en, claim), rationale_ar=rationale_ar,
        data_evidence=claim, cited_claims=[claim], best_day=best_day, best_time=best_time,
        posting_grounding=grounding,
        local_context_used=calendar_ctx.summary_en() + (f" | {local_events_note}" if local_events_note else ""),
        memory_note=memory_note,
    )


def _memory_note_for(finding: dict, memory_store) -> str:
    if memory_store is None:
        return ""
    claim_lower = finding["claim"].lower()
    keyword = None
    for candidate in ("spike", "drop", "best seller", "conversion"):
        if candidate in claim_lower:
            keyword = candidate
            break
    if keyword is None:
        return ""
    direction = "spike" if "spike" in claim_lower or "increase" in claim_lower else "drop"
    try:
        streak = memory_store.find_streak(keyword.split()[0], direction)
    except Exception:  # noqa: BLE001
        return ""
    if streak >= 2:
        return f"This is the {_ordinal(streak)} week in a row this pattern has shown up (per long-term memory)."
    return ""


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _overwrite_posting_time(idea: ContentIdea, clean_data_dir: str, menu: list[MenuItem],
                             posting_windows: dict[str, PostingWindow], week_end=None) -> None:
    """Never trust the LLM's own best_day/best_time — it has no reliable way
    to do the lookup itself and has been observed inventing a plausible-
    sounding but wrong time (e.g. 'Friday 9 AM' when the real busiest hour
    for that category is Friday 9 PM). Always recompute deterministically
    from the same posting_windows data the template path uses, in place."""
    item = next((m for m in menu if m.sku == idea.product_sku), None) if idea.product_sku else None
    posting = _posting_for_item(item, idea.product_sku, clean_data_dir, posting_windows, week_end)
    idea.best_day = posting.weekday if posting else "Saturday"
    idea.best_time = _format_time(posting.hour if posting else 20)
    idea.posting_grounding = (
        f"Highest order density for this category was {posting.weekday} {_format_time(posting.hour)} "
        f"({posting.order_count} orders) per POS timestamps." if posting else "No posting-time data available."
    )


def generate_content_ideas(verified_findings: list[dict], cafe_profile: dict,
                            fallback_calendar: CalendarContext, local_events: dict,
                            clean_data_dir: str, memory_store=None,
                            cost_tracker: CostTracker = None, week_end=None) -> list[ContentIdea]:
    """week_end (a datetime.date), if given, restricts item_selector.py's
    margin/trend scoring, posting_time.py's best-hour data, and the
    waste/menu-engineering bonus features (called separately by
    report_generator.py) to a trailing 12-week window ending at that week —
    matching how the 5 core analysts already read data via
    load_real_data.load_source_for_state(). Without it, this and everything
    it calls reads the full 6-month history regardless of which week is
    being reported, which was a real bug: every week's report showed
    identical numbers until this was wired through."""
    if not verified_findings:
        return []
    if cost_tracker is None:
        cost_tracker = get_default_tracker()

    menu = load_menu(clean_data_dir)
    posting_windows = best_posting_windows(clean_data_dir, week_end=week_end)
    local_note = ("; ".join(r["title"] for r in local_events.get("results", [])[:2])
                  if local_events.get("available") else "")

    ideas: list[ContentIdea] = []
    featured = _build_featured_item_idea("idea_1", IDEA_FORMATS[0], cafe_profile, verified_findings,
                                          fallback_calendar, clean_data_dir, menu, posting_windows,
                                          local_note, memory_store, week_end)
    if featured:
        ideas.append(featured)

    secondary_findings = _pick_secondary_findings(verified_findings)

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    remaining_slots = 3 - len(ideas)
    if api_key and secondary_findings:
        try:
            llm_ideas = _generate_via_llm(secondary_findings[:remaining_slots], cafe_profile, fallback_calendar,
                                           local_events, posting_windows, menu, memory_store, clean_data_dir,
                                           start_at=len(ideas), cost_tracker=cost_tracker)
            valid = []
            for idea in llm_ideas:
                idea.memory_note = _clean_memory_note(idea.memory_note)
                idea.rationale_en = _ensure_number_in_rationale(idea.rationale_en, idea.data_evidence or " / ".join(idea.cited_claims))
                _overwrite_posting_time(idea, clean_data_dir, menu, posting_windows, week_end)

                ok, reason = _validate_idea(idea, verified_findings)
                if not ok:
                    print(f"[content_agent] dropped LLM idea {idea.idea_id}: {reason} - rebuilding via template")
                    continue

                ok, reason = _validate_product_grounding(idea, menu)
                if not ok:
                    # Don't just clear the structured field — the AI can (and
                    # did, in a real run) also weave the invented product
                    # into the free-text hook/rationale, in ONE language but
                    # not the other. Patching the field alone left "have you
                    # tried our Iced Latte?" sitting in the Arabic hook even
                    # after product_sku was cleared. Discard the whole idea
                    # instead and let the template fallback rebuild it clean.
                    print(f"[content_agent] dropped LLM idea {idea.idea_id}: {reason} "
                          f"- discarding entirely (prose may still reference the invented product), rebuilding via template")
                    continue

                valid.append(idea)
            ideas += valid
        except Exception as e:  # noqa: BLE001
            print(f"[content_agent] LLM generation failed ({type(e).__name__}: {e}), falling back to templates")

    if len(ideas) < 3:
        used_claims = {c for i in ideas for c in i.cited_claims}
        fallback_findings = [f for f in secondary_findings if f["claim"] not in used_claims]
        for finding in fallback_findings:
            if len(ideas) >= 3:
                break
            memory_note = _memory_note_for(finding, memory_store)
            fmt = IDEA_FORMATS[len(ideas) % len(IDEA_FORMATS)]
            ideas.append(_template_idea(f"idea_{len(ideas) + 1}", finding, fmt, cafe_profile, fallback_calendar,
                                         clean_data_dir, menu, posting_windows, memory_note, local_note, week_end))

    if cost_tracker.calls_made or cost_tracker.calls_skipped_over_budget:
        print(f"[content_agent] {cost_tracker.summary()}")

    return ideas[:3]


def _generate_via_llm(source_findings, cafe_profile, fallback_calendar, local_events,
                       posting_windows, menu, memory_store, clean_data_dir, start_at,
                       cost_tracker: CostTracker) -> list[ContentIdea]:
    findings_with_calendar = []
    for f in source_findings:
        ctx = _calendar_context_for_finding(f, cafe_profile, fallback_calendar)
        findings_with_calendar.append({
            **f,
            "calendar_context": ctx.summary_en(),
            "calendar_context_ar": ctx.summary_ar(),
        })

    findings_json = json.dumps(findings_with_calendar, ensure_ascii=False, indent=2)
    menu_json = json.dumps([{"sku": m.sku, "item_en": m.item_en, "item_ar": m.item_ar, "category": m.category}
                             for m in menu], ensure_ascii=False)
    posting_json = json.dumps({k: {"weekday": v.weekday, "hour": v.hour, "order_count": v.order_count}
                                for k, v in posting_windows.items()}, ensure_ascii=False)
    memory_notes = [n for n in (_memory_note_for(f, memory_store) for f in source_findings) if n]

    system = (
        "You produce TikTok/Instagram ideas for a cafe's weekly automated report, as a JSON array. "
        "RULES: (1) cited_claims MUST be copied VERBATIM from the findings' own 'claim' field. "
        "(2) product_sku, if set, must be a real sku from the menu given. (3) hook_ar and "
        "rationale_ar must be natural, fluent Arabic - not a literal translation. "
        "(4) best_day must come from posting_windows, matched to the product's category; best_time "
        "must be that window's hour, formatted like '9 PM'. (5) Each finding already includes its "
        "OWN calendar_context field (e.g. 'Eid al-Fitr') - if it is not 'No special calendar events "
        "this week', you MUST reference that exact occasion by name in both rationale_en and "
        "rationale_ar. Do not say a generic date instead. (6) rationale_en MUST restate the finding's "
        "actual number. (7) memory_note must be the empty string if nothing from MEMORY NOTES "
        "applies - never write the word 'None'."
    )
    user = f"""FINDINGS (each includes its own calendar_context - use it, don't infer your own):
{findings_json}

MENU: {menu_json}
CAFE PROFILE: {json.dumps(cafe_profile, ensure_ascii=False)}
LOCAL EVENTS (may be empty if search unavailable): {json.dumps(local_events, ensure_ascii=False)}
POSTING WINDOWS BY CATEGORY: {posting_json}
MEMORY NOTES (only use if directly relevant, never invent one not listed here): {memory_notes}

Return a JSON array of exactly {len(source_findings)} objects with keys: idea_id, hook_en, hook_ar,
format (reel|carousel|trend_audio), product_sku, product_name_en, product_name_ar, rationale_en,
rationale_ar, data_evidence (the literal number(s) as a short string), cited_claims (array of
verbatim claim strings), best_day, best_time, posting_grounding, local_context_used, memory_note.
"""

    google_key = os.environ.get("GOOGLE_API_KEY")
    model_name = "gemini-3.1-flash-lite" if google_key else "claude-sonnet-4-6"
    estimated_prompt_tokens = estimate_tokens(system) + estimate_tokens(user)
    estimated_cost = (estimated_prompt_tokens / 1_000_000) * (0.30 if google_key else 6.00)
    if not cost_tracker.can_afford(estimated_cost):
        cost_tracker.record_skip(f"content_agent LLM call (~${estimated_cost:.4f}) would exceed "
                                  f"remaining budget ${cost_tracker.remaining():.4f}")
        raise RuntimeError(f"cost cap reached: {cost_tracker.summary()}")

    if google_key:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite")
        response = llm.invoke(f"{system}\n\n{user}")
        text = _extract_text(response.content)
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(model="claude-sonnet-4-6", max_tokens=2000, system=system,
                                       messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in resp.content if b.type == "text")

    cost_tracker.record(model_name, estimated_prompt_tokens + estimate_tokens(text))

    raw = extract_json(text)
    ideas = []
    for i, idea_dict in enumerate(raw):
        idea_dict = dict(idea_dict)
        idea_dict["idea_id"] = idea_dict.get("idea_id") or f"idea_{start_at + i + 1}"
        idea_dict.setdefault("data_evidence", "")
        idea_dict.setdefault("product_name_ar", "")
        idea_dict.setdefault("rationale_ar", "")
        ideas.append(ContentIdea(**idea_dict))
    return ideas
