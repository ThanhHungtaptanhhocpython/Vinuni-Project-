"""Agent package exports.

Keep imports lazy so importing a leaf module such as `agents.db_trace_repo`
does not initialize the graph or connect the trace repository at import time.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AgentState",
    "RunContext",
    "agent",
    "build_graph",
    "build_default_context",
    "run_agent",
]


def __getattr__(name: str) -> Any:
    if name == "AgentState":
        from .state import AgentState

        return AgentState
    if name == "RunContext":
        from .context import RunContext

        return RunContext
    if name in {"agent", "build_graph"}:
        from .graph import agent, build_graph

        return {"agent": agent, "build_graph": build_graph}[name]
    if name in {"build_default_context", "run_agent"}:
        from .runner import build_default_context, run_agent

        return {"build_default_context": build_default_context, "run_agent": run_agent}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
