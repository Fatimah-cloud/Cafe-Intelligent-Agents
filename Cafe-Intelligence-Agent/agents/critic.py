"""
Critic Agent — non-negotiable per the assignment.

Every claim from every analyst must trace to an actually-computed number
before it reaches the owner. A claim with no number/evidence gets cut.
Numbers outside a plausible range for their metric type (e.g. a
conversion rate over 100%) get cut too, via deterministic sanity checks --
not just an LLM's judgment call. The critic can send work back to a
specific analyst; revisions are capped so it can't argue forever. "The
data doesn't support a conclusion here" is a valid output.
"""
import json
from typing import TYPE_CHECKING

from agents._code_runner import _get_llm, _extract_text  # reuse the same lazy LLM getter

if TYPE_CHECKING:
    from state import CafeState


MAX_REVISIONS = 3


def _hard_filter(findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Deterministic, non-LLM check first: a finding with no evidence field,
    or a number of None with no explicit "no data" justification, is cut
    automatically -- this is a rule, not a judgment call, so it doesn't
    need a model call and can never be argued around.
    """
    kept, cut = [], []
    for f in findings:
        has_evidence = bool(f.get("evidence"))
        has_number_or_explicit_null = f.get("number") is not None or "no data" in f.get("claim", "").lower() or "failed" in f.get("claim", "").lower()
        if has_evidence and has_number_or_explicit_null:
            kept.append(f)
        else:
            cut.append(f)
    return kept, cut


# Agent name is inferred from the finding's own "agent" field when
# available, so a sanity-check failure routes back to the RIGHT analyst
# (e.g. a bad conversion rate goes back to "operations", not generic).
def _sanity_checks(findings: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """
    Deterministic logical range checks -- catches numbers that are
    mathematically impossible regardless of what the LLM would say about
    them. This is what actually gives the critic teeth: the earlier
    _hard_filter only checks a number EXISTS, not that it's plausible.
    """
    ok, problems = [], []
    for f in findings:
        num = f.get("number")
        claim = f.get("claim", "").lower()

        if num is None:
            ok.append(f)
            continue

        problem = None
        if "conversion rate" in claim and (num > 100 or num < 0):
            problem = f"conversion rate {num}% is outside the valid 0-100% range"
        elif "% of orders" in claim or "waste as a percentage" in claim:
            if num > 100 or num < 0:
                problem = f"waste percentage {num}% is outside the valid 0-100% range"
        elif ("rating" in claim or "rated" in claim) and "percentage" not in claim:
            if num > 5 or num < 1:
                problem = f"rating {num} is outside the valid 1-5 star range"
        elif "margin" in claim and "naive baseline" in claim:
            if num > 100 or num < -100:
                problem = f"margin {num}% is implausible (outside -100% to 100%)"

        if problem:
            problems.append((f, problem))
        else:
            ok.append(f)

    return ok, problems


def critic_agent(state: "CafeState") -> dict:
    """
    Reviews state["findings"] (merged from all 5 parallel analysts).
    Cuts anything with no real evidence, cuts anything that fails a
    logical sanity check, then asks the model to spot any remaining
    inconsistencies among what's left. Sets critic_target to route a
    revision back to a specific analyst, or "none" once satisfied.
    """
    revision_count = state.get("revision_count", 0)
    all_findings = state.get("findings", [])

    kept_evidence, cut_evidence = _hard_filter(all_findings)
    kept, cut_sanity = _sanity_checks(kept_evidence)

    rejection_log = [
        f"CUT (no evidence/number): {f.get('claim', '(no claim)')}" for f in cut_evidence
    ] + [
        f"CUT (failed sanity check — {reason}): {f.get('claim', '(no claim)')}"
        for f, reason in cut_sanity
    ]

    if revision_count >= MAX_REVISIONS:
        return {
            "critic_feedback": (
                f"Max revisions ({MAX_REVISIONS}) reached; accepting "
                f"{len(kept)} verified findings as-is. "
                f"{len(cut_evidence) + len(cut_sanity)} unverifiable/implausible "
                f"claims were cut."
            ),
            "critic_target": "none",
            "verified_findings": kept,
            "rejection_log": rejection_log,
        }

    if not kept:
        return {
            "critic_feedback": "No findings had verifiable, plausible evidence; the data does not support any conclusions this run.",
            "critic_target": "none",
            "verified_findings": [],
            "rejection_log": rejection_log,
        }

    # A sanity-check failure is a concrete, known problem -- route it back
    # to the specific agent that produced it immediately, without waiting
    # on an LLM judgment call for something we've already proven is wrong.
    if cut_sanity:
        first_bad_agent = cut_sanity[0][0].get("agent", "none")
        return {
            "critic_feedback": f"Sanity check failed: {cut_sanity[0][1]}",
            "critic_target": first_bad_agent if first_bad_agent in
                {"sales", "margin", "operations", "reviews", "anomaly"} else "none",
            "verified_findings": kept,
            "rejection_log": rejection_log,
            "revision_count": revision_count + 1,
        }

    # Soft check: ask the model if any of the SURVIVING findings
    # contradict each other (e.g. two different avg ratings for the same
    # thing). This is a genuine judgment call, unlike the hard filter.
    findings_text = "\n".join(f"[{f['agent']}] {f['claim']} (number={f['number']})" for f in kept)
    prompt = (
        "Review these business analysis findings for internal contradictions "
        "only (not missing detail -- only flag if two findings directly "
        "conflict with each other). Respond with JSON only:\n"
        '{"ok": <true/false>, "target": "<sales|margin|operations|reviews|anomaly|none>", '
        '"feedback": "<short reason>"}\n\n'
        f"Findings:\n{findings_text}"
    )
    response = _get_llm().invoke(prompt)
    try:
        parsed = json.loads(_extract_text(response.content).strip())
    except json.JSONDecodeError:
        parsed = {"ok": True, "target": "none", "feedback": ""}

    if parsed.get("ok", True):
        total_cut = len(cut_evidence) + len(cut_sanity)
        feedback = f"{len(kept)} findings verified."
        if total_cut:
            feedback += f" {total_cut} claim(s) cut for lacking evidence or failing a sanity check."
        return {
            "critic_feedback": feedback,
            "critic_target": "none",
            "verified_findings": kept,
            "rejection_log": rejection_log,
        }

    return {
        "critic_feedback": parsed.get("feedback", ""),
        "critic_target": parsed.get("target", "none"),
        "verified_findings": kept,
        "rejection_log": rejection_log,
        "revision_count": revision_count + 1,
    }


if __name__ == "__main__":
    # Standalone test: well-evidenced, unevidenced, contradictory, AND
    # one deliberately implausible number to prove the sanity check
    # actually rejects something (not just passes everything through).
    fake_state = {
        "revision_count": 0,
        "findings": [
            {"agent": "sales", "claim": "Spanish Latte is the best seller", "number": 172.0, "evidence": "sum of line_total_sar"},
            {"agent": "anomaly", "claim": "Something felt off this week", "number": None, "evidence": ""},
            {"agent": "margin", "claim": "Coffee cost rose 9.1%", "number": 9.1, "evidence": "supplier email dated 2026-04-01"},
            {"agent": "operations", "claim": "Overall conversion rate (transactions / footfall)", "number": 145.0, "evidence": "buggy calc, impossible value"},
        ],
    }
    output = critic_agent(fake_state)
    for key, value in output.items():
        print(f"{key}: {value}")