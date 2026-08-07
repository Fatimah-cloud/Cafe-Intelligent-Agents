"""
scripts/print_run_history.py — turns output/run_history.json into the
assignment's "test results table (10 cycles)" deliverable.

Usage:
    python scripts/print_run_history.py
    python scripts/print_run_history.py --markdown   # for pasting into your README
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report.run_archive import get_run_history


def status_for(row: dict) -> str:
    """Fully succeeded / partially / failed, per the assignment's testing
    requirement — a simple, defensible rule: any critic rejection or a
    missing approval decision is "partial," not "succeeded.""" 
    if row.get("report_approved") is None:
        return "partial (no approval recorded)"
    if row.get("rejection_count", 0) > 0:
        return f"partial ({row['rejection_count']} claim(s) rejected)"
    if row.get("data_quality_log") and any("MISSING SOURCE" in line for line in row["data_quality_log"]):
        return "partial (source missing)"
    return "succeeded"


def main():
    markdown = "--markdown" in sys.argv
    history = get_run_history()

    if not history:
        print("No runs recorded yet in output/run_history.json — run the pipeline first.")
        return

    if markdown:
        print("| Run | Week | Findings | Rejected | Revisions | Approved | Status |")
        print("|---|---|---|---|---|---|---|")
        for row in history:
            print(f"| {row['run_id']} | {row['week_id']} | {row.get('verified_finding_count', '?')} "
                  f"| {row.get('rejection_count', 0)} | {row.get('revision_count', 0)} "
                  f"| {row.get('report_approved', '—')} | {status_for(row)} |")
    else:
        print(f"{'week_id':<14} {'findings':<9} {'rejected':<9} {'revisions':<10} {'approved':<9} status")
        print("-" * 80)
        for row in history:
            print(f"{row['week_id']:<14} {row.get('verified_finding_count', '?'):<9} "
                  f"{row.get('rejection_count', 0):<9} {row.get('revision_count', 0):<10} "
                  f"{str(row.get('report_approved', '—')):<9} {status_for(row)}")

    print(f"\n{len(history)} run(s) total.")
    succeeded = sum(1 for r in history if status_for(r) == "succeeded")
    print(f"{succeeded}/{len(history)} fully succeeded.")


if __name__ == "__main__":
    main()