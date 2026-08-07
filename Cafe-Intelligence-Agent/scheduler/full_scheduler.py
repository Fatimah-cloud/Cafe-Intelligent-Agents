"""
scheduler/full_scheduler.py — Person 3's fix for a real gap: Person 2's
`scheduler/scheduler.py` fires `graph.py` (analysts + critic only). It never
runs content_agent, report generation, the human breakpoint, or memory
saving — so the "prove it fires on its own" requirement wasn't actually
being proven for the part of the pipeline the owner receives.

This fires `full_graph.py` on the same APScheduler pattern, with one
necessary difference: `full_graph.py` PAUSES at the human-approval
`interrupt()`. A real weekly cron can't sit there waiting for the owner's
WhatsApp reply — so this scheduler:

1. Runs the graph up to the interrupt (fully automatic, no human involved).
2. Writes the pending approval (thread_id + WhatsApp summary) to
   `pending_approvals.json` so a separate, later step can resume it once
   the owner actually replies.
3. `resume_pending_approval()` is that later step — call it (from a CLI,
   a webhook handler, whatever receives the owner's WhatsApp reply) with
   the week_id and their decision, and it resumes the SAME paused run.

This is the honest shape of "scheduled + human breakpoint" together: the
trigger is unattended, the approval genuinely is not.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

# Same Windows console encoding fix as run_full_pipeline.py — see that
# file's comment for why this is needed.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from apscheduler.schedulers.blocking import BlockingScheduler
from langgraph.types import Command

from full_graph import full_graph
from load_real_data import load_quality_log

PENDING_APPROVALS_PATH = "pending_approvals.json"


def current_week_id() -> str:
    iso_year, iso_week, _ = datetime.now().isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _load_pending() -> dict:
    if os.path.exists(PENDING_APPROVALS_PATH):
        with open(PENDING_APPROVALS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_pending(data: dict) -> None:
    with open(PENDING_APPROVALS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def run_weekly_full_cycle(clean_data_dir: str = "clean_data") -> dict:
    """The scheduled job. Runs unattended up to the human-approval
    interrupt, then stops and records the pending decision — this half
    needs zero human involvement, matching 'the owner does nothing.'"""
    week_id = current_week_id()
    print(f"[{datetime.now().isoformat()}] Starting FULL weekly cycle for {week_id}")

    with open("data_raw/cafe_profile.json", encoding="utf-8") as f:
        cafe_profile = json.load(f)

    quality_log = load_quality_log(clean_data_dir)
    thread_id = f"scheduled-{week_id}"
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

    try:
        result = full_graph.invoke(initial_state, config=config)
    except Exception as e:
        # A scheduled run must never crash silently — log it and stop this
        # cycle, but don't take down the scheduler process (next week's
        # run should still fire).
        print(f"[{datetime.now().isoformat()}] Weekly cycle FAILED: {e}")
        return {"error": str(e), "week_id": week_id}

    if "__interrupt__" not in result:
        print(f"[{datetime.now().isoformat()}] WARNING: graph did not reach the "
              f"approval breakpoint for {week_id} — nothing pending to approve.")
        return result

    pending = _load_pending()
    pending[week_id] = {
        "thread_id": thread_id,
        "whatsapp_summary": result["__interrupt__"][0].value["whatsapp_summary"],
        "triggered_at": datetime.now().isoformat(),
        "resolved": False,
    }
    _save_pending(pending)

    print(f"[{datetime.now().isoformat()}] {week_id} paused for approval — "
          f"WhatsApp summary ready, awaiting owner reply. "
          f"Resume with: python -m scheduler.full_scheduler --resume {week_id} --decision APPROVE")
    return result


def resume_pending_approval(week_id: str, decision: str) -> dict:
    """Call this once the owner's WhatsApp reply comes in (from whatever
    receives it — a webhook, a manual CLI call, a support inbox). Resumes
    the SAME paused graph run via its thread_id, so all the state from the
    original scheduled run (findings, content ideas, report) carries
    forward untouched."""
    pending = _load_pending()
    if week_id not in pending:
        raise ValueError(f"No pending approval found for {week_id}. "
                          f"Known pending weeks: {list(pending.keys())}")

    thread_id = pending[week_id]["thread_id"]
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    final = full_graph.invoke(Command(resume=decision), config=config)

    pending[week_id]["resolved"] = True
    pending[week_id]["decision"] = decision
    pending[week_id]["resolved_at"] = datetime.now().isoformat()
    _save_pending(pending)

    print(f"[{datetime.now().isoformat()}] {week_id} resolved: "
          f"report_approved={final.get('report_approved')}")
    return final


def start_scheduler(day_of_week: str = "sun", hour: int = 8, minute: int = 0) -> None:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_weekly_full_cycle,
        trigger=CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute),
        id="weekly_full_cafe_pipeline",
    )
    print(f"Full-pipeline scheduler started. Fires every {day_of_week} at "
          f"{hour:02d}:{minute:02d}. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="run one cycle immediately (up to the approval pause)")
    parser.add_argument("--resume", metavar="WEEK_ID", help="resume a pending approval, e.g. --resume 2026-W31")
    parser.add_argument("--decision", default="APPROVE", help="APPROVE | REJECT | 'EDIT: <text>' (used with --resume)")
    args = parser.parse_args()

    if args.resume:
        resume_pending_approval(args.resume, args.decision)
    elif args.now:
        run_weekly_full_cycle()
    else:
        start_scheduler()
