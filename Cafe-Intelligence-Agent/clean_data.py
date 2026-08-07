"""
clean_data.py — Person 1 deliverable entry point.

Runs ingestion (Module 1) then cleaning (Module 2) end-to-end and writes:
  clean_data/<source>.csv     — one clean file per source, for Person 2 & 3
  clean_data/quality_report.json / .md

Usage:
    python clean_data.py [--config config/sources_config.json]

Tries the LangGraph ingestion pipeline first (ingestion_graph.py). If
langgraph isn't installed in the current environment, falls back to
calling the same parser functions directly in a loop — same normalized
output, same "one bad source doesn't take down the rest" guarantee,
just without the graph wrapper. Either path produces identical clean_data/.
"""

import argparse
import json
import os
from dataclasses import asdict

import pandas as pd

from parsers.base import ParseResult
from parsers.csv_parser import parse_csv_source
from parsers.excel_parser import parse_excel_source
from parsers.email_parser import parse_email_source
from parsers.json_parser import parse_json_source
from cleaning.cleaner import (
    clean_pos_transactions, clean_menu_items, clean_foot_traffic,
    clean_staff_shifts, clean_inventory_weekly, clean_supplier_emails,
    clean_customer_reviews,
)

PARSER_DISPATCH = {
    "csv": lambda cfg, path: parse_csv_source(cfg["name"], path),
    "excel": lambda cfg, path: parse_excel_source(cfg["name"], path, sheet=cfg.get("sheet", "Sheet1")),
    "email": lambda cfg, path: parse_email_source(cfg["name"], path),
    "json": lambda cfg, path: parse_json_source(cfg["name"], path),
}

# source name -> output filename, used to clear stale output on a failed parse (see A7 fix below)
OUTPUT_FILENAMES = {
    "menu_items": "menu_items.csv",
    "pos_transactions": "pos_transactions.csv",
    "foot_traffic": "foot_traffic.csv",
    "staff_shifts": "staff_shifts.csv",
    "inventory_weekly": "inventory_weekly.csv",
    "supplier_emails": "supplier_emails.csv",
    "customer_reviews": "customer_reviews.csv",
}


def clear_stale_output(source_name: str, out_dir: str) -> None:
    """A7 fix: if a source fails to parse this run, delete any clean_data/<source>.csv
    left over from a previous successful run. Without this, a corrupted source file
    silently makes every downstream reader (analysts, Waste-to-Riyals, Menu Engineering)
    keep working off last week's data with nothing in the report distinguishing
    fresh output from stale output."""
    fname = OUTPUT_FILENAMES.get(source_name)
    if not fname:
        return
    stale_path = os.path.join(out_dir, fname)
    if os.path.exists(stale_path):
        os.remove(stale_path)
        print(f"[clean_data] removed stale {fname} (source failed to parse this run)")


def run_ingestion_fallback(config: dict) -> dict:
    """Same contract as ingestion_graph.run_ingestion(), no LangGraph required."""
    results = {}
    for source_cfg in config["sources"]:
        file_path = os.path.join(config["data_dir"], source_cfg["file"])
        try:
            parse_fn = PARSER_DISPATCH[source_cfg["parser"]]
            results[source_cfg["name"]] = parse_fn(source_cfg, file_path)
        except Exception as e:
            results[source_cfg["name"]] = ParseResult(source=source_cfg["name"], fatal_error=str(e))
    return {"results": results}


def run_ingestion(config: dict) -> dict:
    try:
        from ingestion_graph import run_ingestion as graph_ingestion
        return graph_ingestion()
    except ImportError:
        print("[clean_data] langgraph not installed — running ingestion without the graph wrapper.")
        return run_ingestion_fallback(config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/sources_config.json")
    parser.add_argument("--out", default="clean_data")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    os.makedirs(args.out, exist_ok=True)

    state = run_ingestion(config)
    results = state["results"]

    missing = [name for name, r in results.items() if not r.ok]
    for name in missing:
        cfg = next(c for c in config["sources"] if c["name"] == name)
        status = "REQUIRED SOURCE MISSING" if cfg.get("required") else "optional source missing"
        print(f"[clean_data] {status}: {name} — {results[name].fatal_error}")
        clear_stale_output(name, args.out)   # A7 fix: don't leave last run's file looking current

    quality_reports = []

    # menu_items first — pos_transactions cleaning needs it for the SKU join.
    if results["menu_items"].ok:
        menu_df, menu_qr = clean_menu_items(results["menu_items"])
        quality_reports.append(menu_qr)
        menu_df.to_csv(os.path.join(args.out, "menu_items.csv"), index=False)
    else:
        menu_df = pd.DataFrame(columns=["sku", "item_en", "item_ar"])  # empty fallback so pos_transactions join doesn't crash

    if "pos_transactions" in results and results["pos_transactions"].ok:
        pos_df, pos_qr = clean_pos_transactions(results["pos_transactions"], menu_df)
        quality_reports.append(pos_qr)
        pos_df.to_csv(os.path.join(args.out, "pos_transactions.csv"), index=False)

    if "foot_traffic" in results and results["foot_traffic"].ok:
        traffic_df, traffic_qr = clean_foot_traffic(results["foot_traffic"])
        quality_reports.append(traffic_qr)
        traffic_df.to_csv(os.path.join(args.out, "foot_traffic.csv"), index=False)

    if "staff_shifts" in results and results["staff_shifts"].ok:
        shifts_df, shifts_qr = clean_staff_shifts(results["staff_shifts"])
        quality_reports.append(shifts_qr)
        shifts_df.to_csv(os.path.join(args.out, "staff_shifts.csv"), index=False)

    if "inventory_weekly" in results and results["inventory_weekly"].ok:
        inv_df, inv_qr = clean_inventory_weekly(results["inventory_weekly"])
        quality_reports.append(inv_qr)
        inv_df.to_csv(os.path.join(args.out, "inventory_weekly.csv"), index=False)

    if "supplier_emails" in results and results["supplier_emails"].ok:
        email_df, email_qr = clean_supplier_emails(results["supplier_emails"])
        quality_reports.append(email_qr)
        email_df.to_csv(os.path.join(args.out, "supplier_emails.csv"), index=False)

    if "customer_reviews" in results and results["customer_reviews"].ok:
        rev_df, rev_qr = clean_customer_reviews(results["customer_reviews"])
        quality_reports.append(rev_qr)
        rev_df.to_csv(os.path.join(args.out, "customer_reviews.csv"), index=False)

    write_quality_report(quality_reports, missing, results, args.out)
    print(f"\n[clean_data] done. Clean files + quality report written to {args.out}/")


def write_quality_report(quality_reports, missing_sources, results, out_dir):
    report_dict = {
        "missing_sources": missing_sources,
        "sources": [
            {
                "source": qr.source,
                "rows_in": qr.rows_in,
                "rows_out": qr.rows_out,
                "rows_dropped": qr.rows_dropped,
                "rows_repaired": qr.rows_repaired,
                "issues": [asdict(i) for i in qr.issues],
            }
            for qr in quality_reports
        ],
    }
    with open(os.path.join(out_dir, "quality_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_dict, f, ensure_ascii=False, indent=2)

    lines = ["# Data quality report\n"]
    if missing_sources:
        lines.append("## Missing sources\n")
        for name in missing_sources:
            lines.append(f"- **{name}**: {results[name].fatal_error}")
        lines.append("")
    for qr in quality_reports:
        lines.append(f"## {qr.source}")
        lines.append(f"- rows in: {qr.rows_in} | rows out: {qr.rows_out} | "
                      f"dropped: {qr.rows_dropped} | repaired: {qr.rows_repaired}")
        for issue in qr.issues:
            lines.append(f"  - `{issue.issue}`: {issue.count}" + (f" — {issue.detail}" if issue.detail else ""))
        lines.append("")
    with open(os.path.join(out_dir, "quality_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
