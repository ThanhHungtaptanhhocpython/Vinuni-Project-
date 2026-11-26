"""State Graph của VLM Agent — theo DOC 1 §2.1.

START → preflight ─┬→ load_context → agent ⇄ (tools → observe) → END
                   └→ fail_fast → END
agent → guard_reject → END (khi vượt limit / quá hạn)

Persistence: production dùng LangGraph checkpointer PostgreSQL
(AsyncPostgresSaver, thread_id = run_id) để khôi phục run khi worker crash;
skeleton này compile không checkpointer.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import (
    agent_node,
    fail_fast,
    guard_reject,
    load_context,
    observe_node,
    preflight,
    route_after_load_context,
    route_after_preflight,
    route_decision,
    tool_node,
)
from .state import AgentState


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("preflight", preflight)
    graph.add_node("load_context", load_context)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("observe", observe_node)
    graph.add_node("guard_reject", guard_reject)
    graph.add_node("fail_fast", fail_fast)

    graph.add_edge(START, "preflight")
    graph.add_conditional_edges(
        "preflight", route_after_preflight, ["load_context", "fail_fast"]
    )
    graph.add_conditional_edges(
        "load_context", route_after_load_context, ["agent", "fail_fast"]
    )
    graph.add_conditional_edges(
        "agent", route_decision, ["tools", "guard_reject", "fail_fast", END]
    )
    graph.add_edge("tools", "observe")
    graph.add_edge("observe", "agent")
    graph.add_edge("guard_reject", END)
    graph.add_edge("fail_fast", END)

    return graph.compile()


agent = build_graph()
