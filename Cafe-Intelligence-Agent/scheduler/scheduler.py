"""
Scheduler — proves the whole pipeline runs on its own, weekly, with no
human typing a question. Per the assignment: "Nobody is going to open a
dashboard... It runs in the background on a schedule."

Uses APScheduler (simple, pure-Python, no external service required).
Each scheduled run:
    1. Runs the analysis graph (graph.py) against the current clean_data.
    2. Builds a week_id (e.g. "2026-W27") and saves a summary to long-term
       memory (memory/store.py), so next week's run can reference streaks
       and past content ideas.
    3. Hands off verified_findings to wherever Person 3's content/report
       pipeline picks up (their node attaches after this in the full graph).

This file only owns the "when does it run" part. What happens each run
is graph.py + memory/store.py, already built and tested separately.
"""
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from graph import graph
from memory.store import WeeklyMemoryStore, build_week_summary


def current_week_id() -> str:
    """e.g. '2026-W27' — used as both the log label and the memory key."""
    iso_year, iso_week, _ = datetime.now().isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def run_weekly_cycle(clean_data_dir: str = "clean_data") -> dict:
    """
    The actual weekly job. Runs the full analysis graph, saves a summary
    to long-term memory, and returns the final state for whatever picks
    up next (report generation, in Person 3's part of the pipeline).
    """
    week_id = current_week_id()
    print(f"[{datetime.now().isoformat()}] Starting weekly cycle for {week_id}")

    initial_state = {
        "clean_data_dir": clean_data_dir,
        "data_quality_log": [],
        "findings": [],
        "revision_count": 0,
        "critic_target": "",
        "critic_feedback": "",
    }

    try:
        final_state = graph.invoke(initial_state, config={"recursion_limit": 30})
    except Exception as e:
        # A scheduled run must never crash silently -- log it and stop
        # this cycle, but don't take down the scheduler process itself
        # (next week's run should still fire).
        print(f"[{datetime.now().isoformat()}] Weekly cycle FAILED: {e}")
        return {"error": str(e), "week_id": week_id}

    verified = final_state.get("verified_findings", [])
    rejection_log = final_state.get("rejection_log", [])

    store = WeeklyMemoryStore()
    store.save_week(week_id, build_week_summary(verified, rejection_log))

    print(
        f"[{datetime.now().isoformat()}] Completed {week_id}: "
        f"{len(verified)} findings verified, {len(rejection_log)} claims rejected"
    )
    return final_state


def start_scheduler(day_of_week: str = "sun", hour: int = 8, minute: int = 0) -> None:
    """
    Starts a blocking scheduler that fires run_weekly_cycle() automatically
    at the given time every week -- no manual trigger, matching the
    assignment's "the owner does nothing" requirement.
    """
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_weekly_cycle,
        trigger=CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute),
        id="weekly_cafe_analysis",
    )
    print(
        f"Scheduler started. Weekly cycle will fire every "
        f"{day_of_week} at {hour:02d}:{minute:02d}. Press Ctrl+C to stop."
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


if __name__ == "__main__":
    # Two modes:
    #   python scheduler.py --now   -> runs one cycle immediately (for
    #                                  testing/demo, proves the pipeline works)
    #   python scheduler.py         -> starts the real recurring schedule
    if "--now" in sys.argv:
        run_weekly_cycle()
    else:
        start_scheduler()