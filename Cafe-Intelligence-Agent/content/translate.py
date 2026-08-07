"""
translate.py — Arabic rendering for Person 2's findings.

`verified_findings` entries (state.py: `{agent, claim, number, evidence}`) are
English-only — Person 2's agents don't produce a claim_ar. The report/WhatsApp
summary is a hard bilingual requirement (Arabic-first audience per
cafe_profile.json's "notes" field), so this module fills that gap.

Same lazy-LLM pattern as agents/_code_runner.py and agents/margin.py — one
batched call for ALL findings' claims at once (not one call per claim), using
whichever of GOOGLE_API_KEY (matches Person 2's langchain_google_genai stack)
or ANTHROPIC_API_KEY is available. If neither is set, or the call fails, falls
back to `_template_translate()` — a deterministic, non-LLM Arabic rendering
that keeps the actual number and item name exact (just less fluent prose) so
the report is never blocked on an API key existing.
"""

from __future__ import annotations

import json
import os
import re

from content.item_matcher import MenuItem, match_item
from content.json_utils import extract_json
from content.cost_tracker import CostTracker, get_default_tracker, estimate_tokens

AGENT_LABELS_AR = {
    "sales": "المبيعات",
    "margin": "الهامش والتكلفة",
    "operations": "التشغيل",
    "reviews": "آراء العملاء",
    "anomaly": "رصد الحالات الشاذة",
}


def _extract_text(content) -> str:
    """See content_agent.py's identical helper for why this is needed —
    Gemini can return `.content` as a list of blocks, not a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


def _template_translate_one(finding: dict, menu: list[MenuItem]) -> str:
    """No-LLM fallback: a short, correct-but-plain Arabic sentence built from
    the finding's own fields, not a translation of the English prose. The
    number and any matched item name are exact; only the surrounding
    language is templated."""
    claim = finding["claim"]
    number = finding.get("number")
    agent = finding.get("agent", "")
    label = AGENT_LABELS_AR.get(agent, agent)

    item = match_item(claim, menu)
    item_ar = item.item_ar if item else None

    num_str = f"{number:,.1f}" if isinstance(number, (int, float)) else str(number)

    if item_ar:
        return f"[{label}] {item_ar} — {claim} ({num_str})"
    return f"[{label}] {claim} ({num_str})"


def _template_translate(findings: list[dict], menu: list[MenuItem]) -> dict[int, str]:
    return {i: _template_translate_one(f, menu) for i, f in enumerate(findings)}


def _ensure_numbers_present(result: dict[int, str], findings: list[dict]) -> dict[int, str]:
    """Safety net for the LLM path: the translation prompt ASKS the model to
    weave the number naturally into the Arabic sentence (so the HTML report
    doesn't append a redundant duplicate — see report.html.j2's claim div,
    which only appends a number span for lang=='en' since Arabic already
    has it in-line). If the model ever drops the number, this catches it
    and appends it, rather than silently shipping an Arabic finding with no
    number at all — the 'no number = horoscope' rule applies in both
    languages equally."""
    for i, finding in enumerate(findings):
        number = finding.get("number")
        if number is None:
            continue
        text = result.get(i, "")
        num_str = f"{number:,.1f}" if isinstance(number, (int, float)) else str(number)
        if num_str not in text and num_str.replace(",", "") not in text:
            result[i] = f"{text} ({num_str})"
    return result

def _fmt_number(n) -> str:
    """Same formatting the English side uses (report_generator.py's
    _fmt_number) — duplicated here (small, matches this codebase's existing
    pattern of small shared helpers) so the LLM is only ever shown a clean
    number, never a raw Python float. Feeding it the raw float previously
    caused the model to echo '6257.772972972973' straight into the Arabic
    text instead of translating it into something readable."""
    if isinstance(n, float):
        return f"{n:,.1f}"
    if isinstance(n, int):
        return f"{n:,}"
    return str(n) if n is not None else ""


def translate_findings_to_arabic(findings: list[dict], clean_data_dir: str,
                                  cost_tracker: CostTracker = None) -> dict[int, str]:
    """Returns {index_in_findings_list: arabic_text}. Tries one batched LLM
    call first (matching Person 2's cost-conscious pattern in margin.py /
    reviews.py); degrades to the template renderer on any failure so the
    report is never blocked on a missing/expired API key."""
    from content.item_matcher import load_menu
    menu = load_menu(clean_data_dir)

    if not findings:
        return {}
    if cost_tracker is None:
        cost_tracker = get_default_tracker()

    google_key = os.environ.get("GOOGLE_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    if not (google_key or anthropic_key):
        return _template_translate(findings, menu)

    numbered = "\n".join(f"{i}: {f['claim']} (the number in this finding is {_fmt_number(f.get('number'))})"
                          for i, f in enumerate(findings))
    prompt = (
        "Translate each of these cafe business findings into natural, fluent "
        "Arabic (not a literal word-for-word translation), written as a normal "
        "sentence a person would read on WhatsApp — not a technical log line. "
        "Weave the given number naturally into the Arabic sentence, using EXACTLY "
        "the number as given (already rounded — do not add more decimal places, "
        "do not use a raw unrounded value, do not write it as 'value=' or "
        "'القيمة=' or any bracket/tag notation). Respond with ONLY a "
        'JSON object mapping the index (as a string) to the Arabic text, e.g. '
        '{"0": "...", "1": "..."}. No prose, no markdown fences.\n\n'
        f"{numbered}"
    )

    model_name = "gemini-3.1-flash-lite" if google_key else "claude-sonnet-4-6"
    estimated_prompt_tokens = estimate_tokens(prompt)
    estimated_cost = (estimated_prompt_tokens / 1_000_000) * (0.30 if google_key else 6.00)
    if not cost_tracker.can_afford(estimated_cost):
        cost_tracker.record_skip(f"translate LLM call (~${estimated_cost:.4f}) would exceed "
                                  f"remaining budget ${cost_tracker.remaining():.4f}")
        print(f"[translate] {cost_tracker.summary()} — using template fallback")
        return _template_translate(findings, menu)

    try:
        if google_key:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite")
            response = llm.invoke(prompt)
            text = _extract_text(response.content)
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            resp = client.messages.create(model="claude-sonnet-4-6", max_tokens=2000,
                                           messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in resp.content if b.type == "text")

        cost_tracker.record(model_name, estimated_prompt_tokens + estimate_tokens(text))

        parsed = extract_json(text)
        result = {int(k): v for k, v in parsed.items()}
        # Any index the model dropped still needs a value — fill from template.
        template_fallback = _template_translate(findings, menu)
        for i in range(len(findings)):
            result.setdefault(i, template_fallback[i])
        return _ensure_numbers_present(result, findings)
    except Exception as e:  # noqa: BLE001 — must never block the report
        print(f"[translate] LLM translation failed ({type(e).__name__}: {e}), using template fallback")
        return _template_translate(findings, menu)
