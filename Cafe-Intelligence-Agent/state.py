from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages
import operator


class CafeState(TypedDict):
    """Shared state for the Cafe Intelligence Agent. This is the contract
    Person 1 (ingestion/cleaning), Person 2 (analysis/critic/memory), and
    Person 3 (content/report) all code against."""

    messages: Annotated[list, add_messages]
    cafe_profile: dict  # from cafe_profile.json

    # Person 1 delivers this — a PATH to the clean_data/ folder, not the
    # DataFrames themselves. Keeping raw DataFrames (66k+ rows) in graph
    # state made every node's LangSmith trace huge (17MB+) and caused
    # upload timeouts. Each analyst loads only the CSV(s) it actually
    # needs via this path, keeping state small and traces fast.
    clean_data_dir: str
    data_quality_log: Annotated[list[str], operator.add]

    # Person 2 (analysts) write here; merged from parallel branches
    findings: Annotated[list[dict], operator.add]

    # Person 2 (critic) controls the revision loop
    critic_feedback: str
    critic_target: str  # "sales" | "margin" | "operations" | "reviews" | "anomaly" | "none"
    revision_count: int
    verified_findings: list[dict]  # latest critic-approved subset of findings (overwritten each pass)
    rejection_log: Annotated[list[str], operator.add]  # accumulates across revisions, for the report's critic-rejection-count requirement

   # Person 3 owns these
    content_ideas: list[dict]
    report_html: str
    whatsapp_summary: str
    report_approved: bool
    run_archive_id: str  # ties report_node's run_history.json row to memory_save_node's later update

    week_id: str  # e.g. "2026-W23" — used as the long-term memory key