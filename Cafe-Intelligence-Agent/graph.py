"""
graph.py — Person 2's LangGraph: parallel analysis + critic loop-back.

    START -> fan_out (all 5 analysts run in parallel)
          -> sales_agent / margin_agent / operations_agent / reviews_agent / anomaly_agent
          -> critic_agent
          -> (critic_target != "none") -> loop back to that ONE analyst -> critic_agent again
          -> (critic_target == "none") -> END (Person 3's content_agent plugs in here)

State carries clean_data_dir (a path string), not loaded DataFrames --
either write_mock_clean_data_dir() for testing, or "clean_data" for the
real dataset. Every analyst loads only the source(s) it needs via
load_source(clean_data_dir, key), so state stays small.
"""
from typing import Literal

from langgraph.graph import StateGraph, START, END

from state import CafeState
from agents.sales import sales_agent
from agents.margin import margin_agent
from agents.operations import operations_agent
from agents.reviews import reviews_agent
from agents.anomaly import anomaly_agent
from agents.critic import critic_agent

ANALYST_NODES = ["sales_agent", "margin_agent", "operations_agent", "reviews_agent", "anomaly_agent"]

# Maps critic_target values (state.py) to actual graph node names
TARGET_TO_NODE = {
    "sales": "sales_agent",
    "margin": "margin_agent",
    "operations": "operations_agent",
    "reviews": "reviews_agent",
    "anomaly": "anomaly_agent",
}


def fan_out_to_analysts(state: CafeState) -> list[str]:
    """All 5 analysts are required by the assignment (unlike Task 2's
    conditionally-run Budget agent) -- always fan out to all of them."""
    return ANALYST_NODES


def route_after_critic(state: CafeState) -> Literal[
    "sales_agent", "margin_agent", "operations_agent", "reviews_agent", "anomaly_agent", "__end__"
]:
    """Loop-back to exactly the analyst the critic flagged, or finish."""
    target = state.get("critic_target", "none")
    return TARGET_TO_NODE.get(target, END)


def build_graph():
    builder = StateGraph(CafeState)

    builder.add_node("sales_agent", sales_agent)
    builder.add_node("margin_agent", margin_agent)
    builder.add_node("operations_agent", operations_agent)
    builder.add_node("reviews_agent", reviews_agent)
    builder.add_node("anomaly_agent", anomaly_agent)
    builder.add_node("critic_agent", critic_agent)

    # Parallel fan-out: all 5 analysts run at once
    builder.add_conditional_edges(START, fan_out_to_analysts, ANALYST_NODES)

    # All 5 converge on the critic
    for node in ANALYST_NODES:
        builder.add_edge(node, "critic_agent")

    # Critic either loops back to ONE specific analyst, or the run is done
    # (Person 3's content_agent node attaches after END here once merged in).
    builder.add_conditional_edges(
        "critic_agent", route_after_critic, ANALYST_NODES + [END]
    )

    # NOTE: no checkpointer here. This graph runs start-to-finish in a
    # single call (no human-in-the-loop pause here, unlike Task 2), so
    # step-by-step persistence isn't needed at this layer. Cross-week
    # long-term memory (the assignment's actual persistence requirement)
    # is handled separately via memory/store.py, which saves only small
    # serializable weekly summaries, not raw analysis data.
    return builder.compile()


graph = build_graph()


if __name__ == "__main__":
    import sys

    use_real_data = "--real" in sys.argv

    if use_real_data:
        from load_real_data import load_quality_log
        clean_data_dir = "clean_data"
        quality_log = load_quality_log(clean_data_dir)
        print(f"Using REAL data from: {clean_data_dir}")
    else:
        from mock_data.mock_cleaned_data import write_mock_clean_data_dir
        clean_data_dir = write_mock_clean_data_dir()
        quality_log = ["(mock data — no real quality report)"]
        print(f"Using MOCK data from: {clean_data_dir}")

    config = {"recursion_limit": 30}

    # clean_data_dir is a short path string, not raw DataFrames -- each
    # analyst loads only what it needs via load_source(). Keeping state
    # small like this is what makes each node's LangSmith trace fast to
    # upload, even with a 66k-row source dataset on disk.
    initial_state = {
        "clean_data_dir": clean_data_dir,
        "data_quality_log": quality_log,
        "findings": [],
        "revision_count": 0,
        "critic_target": "",
        "critic_feedback": "",
    }

    final_state = graph.invoke(initial_state, config=config)

    print("\n=== Verified findings ===")
    for f in final_state.get("verified_findings", []):
        print(f"[{f['agent']}] {f['claim']} -> {f['number']}")

    print("\n=== Critic feedback ===")
    print(final_state.get("critic_feedback"))

    print("\n=== Rejection log ===")
    for line in final_state.get("rejection_log", []):
        print(" ", line)

    print(f"\nRevisions used: {final_state.get('revision_count', 0)}")