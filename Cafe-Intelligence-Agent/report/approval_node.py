"""
approval_node.py — the human breakpoint the assignment requires: "Human
breakpoint before anything goes out. The owner approves, edits, or rejects —
and the graph continues from there."

Uses LangGraph's `interrupt()` primitive: the graph pauses here and returns
control to the caller with the WhatsApp summary attached. Resuming requires
a checkpointer (see full_graph.py — MemorySaver) and a `thread_id` in the
invoke config, since a paused run has to survive between the two separate
`graph.invoke()` calls (one that hits the interrupt, one that resumes it).

The owner's reply is passed back via `Command(resume=<reply>)`:
    - "APPROVE"        -> report_approved = True, nothing else changes
    - "REJECT"         -> report_approved = False, whatsapp_summary unchanged
    - "EDIT: <text>"   -> report_approved = True, whatsapp_summary replaced
                           with the owner's edited text (what actually gets
                           "sent" downstream, e.g. by a real WhatsApp API
                           integration this project doesn't include)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.types import interrupt

if TYPE_CHECKING:
    from state import CafeState


def human_approval_node(state: "CafeState") -> dict:
    reply = interrupt({
        "prompt": "Reply APPROVE, REJECT, or EDIT: <new text> for this week's report.",
        "whatsapp_summary": state.get("whatsapp_summary", ""),
        "week_id": state.get("week_id", ""),
    })

    if not isinstance(reply, str):
        return {"report_approved": False}

    reply_stripped = reply.strip()
    upper = reply_stripped.upper()

    if upper.startswith("EDIT:"):
        edited_text = reply_stripped.split(":", 1)[1].strip()
        return {"report_approved": True, "whatsapp_summary": edited_text}
    if upper == "APPROVE":
        return {"report_approved": True}
    # Anything else (including explicit REJECT) is treated as a reject —
    # never send anything on an ambiguous reply.
    return {"report_approved": False}
