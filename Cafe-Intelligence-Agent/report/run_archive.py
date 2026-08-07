"""
run_archive.py — keeps every run, not just the latest one.

Before this: report_node.py wrote `output/report_{week_id}.html` and
`output/whatsapp_summary_{week_id}.txt`, both keyed only by week_id. Running
the SAME week twice (which happens constantly during testing — re-running
after a fix, or the assignment's 10-cycle test matrix genuinely re-hitting a
week) silently overwrote the earlier result with no trace it ever existed.

This adds a permanent, timestamped archive on top of those "latest" files
(which are kept as-is, for convenience — always the most recent view of a
given week) without changing their behavior:

    output/report_{week_id}.html            <- still the latest, still overwritten
    output/whatsapp_summary_{week_id}.txt    <- still the latest, still overwritten
    output/runs/{run_id}/report.html         <- NEVER overwritten, one per run
    output/runs/{run_id}/whatsapp.txt        <- NEVER overwritten, one per run
    output/run_history.json                  <- one row per run, append-only

`run_history.json` is exactly the source to build the assignment's "test
results table (10 cycles)" deliverable from — see
`scripts/print_run_history.py`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

OUTPUT_DIR = "output"
RUNS_DIR = os.path.join(OUTPUT_DIR, "runs")
HISTORY_PATH = os.path.join(OUTPUT_DIR, "run_history.json")


def _make_run_id(week_id: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{timestamp}_{week_id}"


def archive_run_files(week_id: str, html_content: str, whatsapp_text: str) -> str:
    """Writes this run's report+whatsapp into their own never-overwritten
    folder. Returns the run_id (also the folder name under output/runs/)."""
    run_id = _make_run_id(week_id)
    run_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)

    with open(os.path.join(run_dir, "report.html"), "w", encoding="utf-8") as fh:
        fh.write(html_content)
    with open(os.path.join(run_dir, "whatsapp.txt"), "w", encoding="utf-8") as fh:
        fh.write(whatsapp_text)

    return run_id


def _load_history() -> list[dict]:
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return []


def _save_history(history: list[dict]) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=2, ensure_ascii=False)


def append_run_history(run_id: str, **fields) -> None:
    """One row per run, appended — never overwrites a prior run's row even
    if week_id repeats. `fields` is whatever's useful for the 10-cycle test
    table: verified_finding_count, rejection_count, revision_count, etc."""
    history = _load_history()
    history.append({
        "run_id": run_id,
        "recorded_at": datetime.now().isoformat(),
        **fields,
    })
    _save_history(history)


def update_run_history(run_id: str, **fields) -> None:
    """Called later (memory_save_node, once the human decision is known) to
    add report_approved to the SAME row created by append_run_history —
    matched by run_id, so this never creates a duplicate or touches any
    other run's row."""
    history = _load_history()
    for row in history:
        if row["run_id"] == run_id:
            row.update(fields)
            break
    _save_history(history)


def get_run_history() -> list[dict]:
    return _load_history()