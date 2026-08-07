"""
ingestion_graph.py — Module 1: Data Ingestion.

One sub-graph node per source, fanned out with the Send API so they run
in parallel and fail independently. If inventory_weekly.xlsx is corrupt,
the other five sources still make it through, and the ingestion report
says exactly which source was missing and why.

Adding a 7th source = add an entry to config/sources_config.json and,
if it's a genuinely new file *type* (not just a new file), one parser
function. Nothing else in this file changes.

Run standalone:  python ingestion_graph.py
"""

import json
import os
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from parsers.base import ParseResult
from parsers.csv_parser import parse_csv_source
from parsers.excel_parser import parse_excel_source
from parsers.email_parser import parse_email_source
from parsers.json_parser import parse_json_source

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "sources_config.json")

PARSER_DISPATCH = {
    "csv": lambda cfg, path: parse_csv_source(cfg["name"], path),
    "excel": lambda cfg, path: parse_excel_source(cfg["name"], path, sheet=cfg.get("sheet", "Sheet1")),
    "email": lambda cfg, path: parse_email_source(cfg["name"], path),
    "json": lambda cfg, path: parse_json_source(cfg["name"], path),
}


def merge_results(left: dict, right: dict) -> dict:
    """Reducer: parallel source nodes each write one key. Plain merge is safe
    because no two nodes ever write the same source name."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class IngestionState(TypedDict):
    config: dict
    results: Annotated[dict, merge_results]   # source_name -> ParseResult


class SourceTask(TypedDict):
    config: dict
    source_cfg: dict


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fan_out_sources(state: IngestionState):
    """Conditional edge from START: one Send per configured source."""
    cfg = state["config"]
    return [
        Send("parse_source", {"config": cfg, "source_cfg": source_cfg})
        for source_cfg in cfg["sources"]
    ]


def parse_source_node(task: SourceTask) -> dict:
    """Runs for exactly one source. Never raises — any failure becomes a
    fatal_error on the ParseResult so the rest of the fan-out is unaffected."""
    cfg = task["config"]
    source_cfg = task["source_cfg"]
    data_dir = cfg["data_dir"]
    parser_type = source_cfg["parser"]
    file_path = os.path.join(data_dir, source_cfg["file"])

    try:
        parse_fn = PARSER_DISPATCH[parser_type]
        result = parse_fn(source_cfg, file_path)
    except Exception as e:
        result = ParseResult(source=source_cfg["name"], fatal_error=f"unhandled parser error: {e}")

    return {"results": {source_cfg["name"]: result}}


def ingestion_report_node(state: IngestionState) -> dict:
    """Reduce step: turn per-source ParseResults into a human-readable report."""
    lines = ["=== Ingestion report ==="]
    for name, res in state["results"].items():
        if res.ok:
            lines.append(
                f"[OK]    {name:20s} rows_in={res.rows_in:<6} rows_out={res.rows_out:<6} "
                f"errors={len(res.errors)}"
            )
        else:
            lines.append(f"[FAIL]  {name:20s} {res.fatal_error}")
    return {"report_text": "\n".join(lines)}


def build_ingestion_graph():
    graph = StateGraph(IngestionState)
    graph.add_node("parse_source", parse_source_node)
    graph.add_node("ingestion_report", ingestion_report_node)

    graph.add_conditional_edges(START, fan_out_sources, ["parse_source"])
    graph.add_edge("parse_source", "ingestion_report")
    graph.add_edge("ingestion_report", END)

    return graph.compile()


def run_ingestion(config_path: str = CONFIG_PATH) -> dict:
    cfg = load_config(config_path)
    app = build_ingestion_graph()
    final_state = app.invoke({"config": cfg, "results": {}})
    return final_state


if __name__ == "__main__":
    state = run_ingestion()
    print(state.get("report_text", "(no report generated)"))
    ok_sources = [n for n, r in state["results"].items() if r.ok]
    failed_sources = [n for n, r in state["results"].items() if not r.ok]
    print(f"\n{len(ok_sources)} sources OK, {len(failed_sources)} failed: {failed_sources}")
