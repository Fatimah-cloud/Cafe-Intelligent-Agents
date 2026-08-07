"""
full_graph.py — Person 3's contribution to wiring the complete pipeline:
Person 2's analysts+critic graph.py, extended with content_agent -> report ->
human_approval (interrupt) -> memory_save, per person2.md's integration note
("graph.py's END is where your content_agent node should attach").

Doesn't modify graph.py — reuses the exact same node functions (imported,
not copy-pasted) so a change to an analyst in agents/*.py is picked up here
automatically. Adds ONE thing graph.py deliberately didn't need: a
checkpointer (MemorySaver), required for the human_approval_node's
interrupt()/resume to work — a paused run has to survive between two
separate `graph.invoke()` calls.

    START -> [5 analysts in parallel] -> critic_agent
          -> (critic_target != "none") -> loop back to that analyst
          -> (critic_target == "none") -> content_agent -> report_node
          -> human_approval (INTERRUPTS HERE) -> memory_save -> END

See run_full_pipeline.py for the two-call invoke/resume pattern this requires.
"""

from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from state import CafeState
from agents.sales import sales_agent
from agents.margin import margin_agent
from agents.operations import operations_agent
from agents.reviews import reviews_agent
from agents.anomaly import anomaly_agent
from agents.critic import critic_agent
from content.content_node import content_agent_node
from report.report_node import report_node
from report.approval_node import human_approval_node
from memory.memory_node import memory_save_node

ANALYST_NODES = ["sales_agent", "margin_agent", "operations_agent", "reviews_agent", "anomaly_agent"]

TARGET_TO_NODE = {
    "sales": "sales_agent",
    "margin": "margin_agent",
    "operations": "operations_agent",
    "reviews": "reviews_agent",
    "anomaly": "anomaly_agent",
}


def fan_out_to_analysts(state: CafeState) -> list[str]:
    return ANALYST_NODES


def route_after_critic(state: CafeState) -> Literal[
    "sales_agent", "margin_agent", "operations_agent", "reviews_agent", "anomaly_agent", "content_agent"
]:
    """Same routing as graph.py, except critic_target == 'none' now goes to
    content_agent instead of END — this IS the attachment point person2.md
    described."""
    target = state.get("critic_target", "none")
    return TARGET_TO_NODE.get(target, "content_agent")


def build_full_graph(with_checkpointer: bool = True):
    """with_checkpointer=True (default) is what run_full_pipeline.py and
    scheduler/full_scheduler.py need — they invoke the graph directly via
    Python, outside any platform, so THEY have to supply persistence for
    the human_approval interrupt to survive between the pause and the
    resume call.

    with_checkpointer=False is what LangGraph Studio needs: it refuses to
    load a graph that already has a checkpointer compiled in (its dev
    server manages persistence itself and errors out otherwise — this
    isn't a style choice, `langgraph dev` raises a ValueError on load if
    you don't do this). See langgraph.json, which points at
    `full_graph_for_studio`, the with_checkpointer=False instance below."""
    builder = StateGraph(CafeState)

    builder.add_node("sales_agent", sales_agent)
    builder.add_node("margin_agent", margin_agent)
    builder.add_node("operations_agent", operations_agent)
    builder.add_node("reviews_agent", reviews_agent)
    builder.add_node("anomaly_agent", anomaly_agent)
    builder.add_node("critic_agent", critic_agent)
    builder.add_node("content_agent", content_agent_node)
    builder.add_node("report_node", report_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_node("memory_save", memory_save_node)

    builder.add_conditional_edges(START, fan_out_to_analysts, ANALYST_NODES)
    for node in ANALYST_NODES:
        builder.add_edge(node, "critic_agent")

    builder.add_conditional_edges(
        "critic_agent", route_after_critic, ANALYST_NODES + ["content_agent"]
    )

    builder.add_edge("content_agent", "report_node")
    builder.add_edge("report_node", "human_approval")
    builder.add_edge("human_approval", "memory_save")
    builder.add_edge("memory_save", END)

    # A checkpointer is required for human_approval's interrupt()/resume —
    # graph.py doesn't need one (runs start-to-finish, no pause), but this
    # extended graph does: MemorySaver persists state between the invoke()
    # that hits the interrupt and the later invoke() that resumes it.
    # Swap for a persistent backend (SqliteSaver, PostgresSaver) in
    # production so a pause survives a process restart, not just this run.
    if with_checkpointer:
        return builder.compile(checkpointer=MemorySaver())
    return builder.compile()


full_graph = build_full_graph(with_checkpointer=True)              # CLI use: run_full_pipeline.py, scheduler/full_scheduler.py
full_graph_for_studio = build_full_graph(with_checkpointer=False)  # LangGraph Studio use: see langgraph.json


if __name__ == "__main__":
    # See run_full_pipeline.py for the full two-call demo (trigger, hit the
    # breakpoint, simulate the owner's reply, resume, save memory). This
    # standalone entry just proves the graph compiles and the interrupt fires.
    import sys
    from datetime import datetime
    from langgraph.types import Command

    use_real_data = "--real" in sys.argv
    if use_real_data:
        from load_real_data import load_quality_log
        clean_data_dir = "clean_data"
        quality_log = load_quality_log(clean_data_dir)
    else:
        from mock_data.mock_cleaned_data import write_mock_clean_data_dir
        clean_data_dir = write_mock_clean_data_dir()
        quality_log = ["(mock data)"]

    import json
    with open("data_raw/cafe_profile.json", encoding="utf-8") as f:
        cafe_profile = json.load(f)

    week_id = datetime.now().strftime("%Y-W%W")
    config = {"configurable": {"thread_id": f"demo-{week_id}"}, "recursion_limit": 40}

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

    result = full_graph.invoke(initial_state, config=config)

    if "__interrupt__" in result:
        print("=== INTERRUPTED — awaiting owner approval ===")
        print(result["__interrupt__"][0].value["whatsapp_summary"])
        print("\nResuming with APPROVE...")
        final = full_graph.invoke(Command(resume="APPROVE"), config=config)
        print(f"\nreport_approved={final.get('report_approved')}")
    else:
        print("Graph completed without interrupting (unexpected) — final keys:", list(result.keys()))