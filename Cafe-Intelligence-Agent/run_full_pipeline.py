"""
run_full_pipeline.py — the 10-minute demo script: trigger a real run, walk
the graph to the human breakpoint, show a content suggestion traced back to
the finding that caused it, simulate the owner's WhatsApp reply, resume, and
confirm memory was saved.

    python run_full_pipeline.py --real            # against clean_data/
    python run_full_pipeline.py --real --decision REJECT
    python run_full_pipeline.py --real --decision "EDIT: custom text here"
    python run_full_pipeline.py                   # mock data, fast smoke test

Sandbox note: agents/margin.py and agents/reviews.py always call an LLM
(Gemini, via GOOGLE_API_KEY) to extract price-change events / complaint
themes from free text — that's real analysis, not a self-correction retry,
so it can't be skipped. If neither GOOGLE_API_KEY nor ANTHROPIC_API_KEY is
set, this script stubs exactly those two calls (and the critic's soft
contradiction check) with the same values verified by hand against
clean_data/ in docs_content_report_details.md's provenance section, and prints a loud
warning so this is never mistaken for a real run. Set GOOGLE_API_KEY (or
ANTHROPIC_API_KEY, content_agent.py accepts either) to run for real.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# Windows' PowerShell/cmd console defaults to the cp1252 codepage, which
# can't print emoji (📊, 🎬, etc.) used throughout the report — without
# this, the script crashes mid-run on Windows the moment it tries to print
# the WhatsApp summary, AFTER the analysis completes but BEFORE the
# approval/memory-save steps run. Forcing UTF-8 output fixes this on every
# platform; Linux/Mac already default to UTF-8 so this is a no-op there.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv()  # must happen BEFORE the API-key check below, or a key that's
                # only in a .env file (not a real shell environment variable)
                # looks "missing" here even though agents/_code_runner.py
                # would have found it a moment later — that mismatch is what
                # caused the mixed stubbed/real run.

from langgraph.types import Command


def _install_llm_stubs():
    """Sandbox-only fallback — see module docstring. Values here are the
    exact ones hand-verified in docs_content_report_details.md against the real
    clean_data/ dataset, not arbitrary placeholders."""
    from unittest.mock import patch, MagicMock
    import agents.margin as margin_mod
    import agents.reviews as reviews_mod
    import agents.critic as critic_mod

    fake_events = [
        {"item": "roasted_coffee", "old_value": 88, "new_value": 96, "effective_date": "2026-04-15"},
        {"item": "full_fat_milk", "old_value": 7.10, "new_value": 8.40, "effective_date": "2026-05-01"},
    ]
    fake_themes = [
        {"theme": "V60/filter coffee quality dropped", "review_count": 30, "example_quote": "V60 quality dropped recently"},
        {"theme": "Long wait times, especially Friday evenings", "review_count": 10, "example_quote": "Waited 25 minutes on Friday evening, too crowded"},
        {"theme": "Perceived as expensive for portion size", "review_count": 4, "example_quote": "A bit expensive for the size"},
    ]

    patches = [
        patch.object(margin_mod, "_get_llm", return_value=MagicMock(
            invoke=lambda *_a, **_k: MagicMock(content=json.dumps(fake_events)))),
        patch.object(reviews_mod, "_get_llm", return_value=MagicMock(
            invoke=lambda *_a, **_k: MagicMock(content=json.dumps(fake_themes)))),
        patch.object(critic_mod, "_get_llm", return_value=MagicMock(
            invoke=lambda *_a, **_k: MagicMock(content='{"ok": true, "target": "none", "feedback": ""}'))),
    ]
    for p in patches:
        p.start()
    return patches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="use clean_data/ instead of mock data")
    parser.add_argument("--decision", default="APPROVE", help="APPROVE | REJECT | 'EDIT: <text>'")
    parser.add_argument("--week-id", default=None)
    args = parser.parse_args()

    if os.environ.get("LANGCHAIN_TRACING_V2", "").lower() == "true" and os.environ.get("LANGCHAIN_API_KEY"):
        project = os.environ.get("LANGCHAIN_PROJECT", "default")
        print(f"LangSmith tracing: ENABLED (project '{project}') — this run will appear in your LangSmith dashboard.")
    else:
        print("LangSmith tracing: NOT enabled this run.")
        print("  To enable: set LANGCHAIN_TRACING_V2=true, LANGCHAIN_API_KEY=<your key>, "
              "and optionally LANGCHAIN_PROJECT=<name> in your .env file, then re-run.")
        print("  Get a key at https://smith.langchain.com — the deliverable requires a full trace of a real run.")

    stubs_active = []
    if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        print("=" * 70)
        print("WARNING: no GOOGLE_API_KEY or ANTHROPIC_API_KEY set.")
        print("Stubbing margin/reviews/critic LLM calls with hand-verified values")
        print("(see docs_content_report_details.md provenance section). Set a real key to run")
        print("this end-to-end for real.")
        print("=" * 70)
        stubs_active = _install_llm_stubs()

    from full_graph import full_graph
    from load_real_data import load_quality_log
    from mock_data.mock_cleaned_data import write_mock_clean_data_dir

    if args.real:
        clean_data_dir = "clean_data"
        if not os.path.exists(os.path.join(clean_data_dir, "quality_report.json")):
            print(f"{clean_data_dir}/ not found — running Person 1's ingestion+cleaning step first "
                  f"(python clean_data.py) so this is genuinely a single command...")
            import subprocess
            subprocess.run([sys.executable, "clean_data.py"], check=True)
        quality_log = load_quality_log(clean_data_dir)
    else:
        clean_data_dir = write_mock_clean_data_dir()
        quality_log = ["(mock data — no real quality report)"]

    with open("data_raw/cafe_profile.json", encoding="utf-8") as f:
        cafe_profile = json.load(f)

    week_id = args.week_id or datetime.now().strftime("%Y-W%W")
    thread_id = f"pipeline-{week_id}-{datetime.now().timestamp()}"
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    initial_state = {
        "clean_data_dir": clean_data_dir,
        "cafe_profile": cafe_profile,
        "data_quality_log": quality_log,
        "findings": [],
        "revision_count": 0,
        "critic_target": "",
        "critic_feedback": "",
        "week_id": week_id,
    }

    print(f"\n=== Triggering run for week {week_id} (clean_data_dir={clean_data_dir}) ===\n")
    result = full_graph.invoke(initial_state, config=config)

    print(f"Verified findings: {len(result.get('verified_findings', []))}")
    print(f"Critic feedback: {result.get('critic_feedback')}")
    print(f"Claims rejected: {len(result.get('rejection_log', []))}")
    print(f"Revisions used: {result.get('revision_count', 0)}")

    if "__interrupt__" not in result:
        print("\nGraph did not hit the human-approval interrupt — something's wrong upstream.")
        for p in stubs_active:
            p.stop()
        sys.exit(1)

    interrupt_payload = result["__interrupt__"][0].value
    print("\n=== HUMAN BREAKPOINT — awaiting owner approval ===")
    print(interrupt_payload["whatsapp_summary"])

    print(f"\n=== Content idea traced back to its finding ===")
    ideas = result.get("content_ideas", [])
    if ideas:
        idea = ideas[0]
        print(f"Idea: {idea['hook_en']}")
        if idea.get("cited_claims"):
            print(f"Cites: {idea['cited_claims']}")
            matching = [f for f in result.get("verified_findings", []) if f["claim"] in idea["cited_claims"]]
            for f in matching:
                print(f"  -> traced to verified finding: [{f['agent']}] {f['claim']} (number={f['number']})")
                print(f"     evidence: {f['evidence']}")
        else:
            print(f"Grounded in Person-3-computed data_evidence (not a critic-reviewed finding, same status as posting_time.py):")
            print(f"  -> {idea.get('data_evidence')}")

    print(f"\n=== Simulating owner reply: {args.decision!r} ===")
    final = full_graph.invoke(Command(resume=args.decision), config=config)

    print(f"\nreport_approved = {final.get('report_approved')}")
    print(f"Final whatsapp_summary (first 200 chars): {final.get('whatsapp_summary', '')[:200]}")

    from memory.store import WeeklyMemoryStore
    store = WeeklyMemoryStore()
    saved = store.get_week(week_id)
    print(f"\nMemory saved for {week_id}: {saved is not None}")
    if saved:
        print(f"  verified_finding_count={saved['verified_finding_count']}, "
              f"critic_rejection_count={saved['critic_rejection_count']}")

    for p in stubs_active:
        p.stop()

    print("\n=== Done. Full HTML report + WhatsApp summary are in output/ ===")


if __name__ == "__main__":
    main()
