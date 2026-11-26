"""Shared runtime system-prompt composition."""

from __future__ import annotations

from services.memory import render_long_term_memory
from stores.task_memory import render_task_memory

from .context import RunContext
from .context_builder import ContextLayers, allocate_context_budget
from .scene_memory import scene_memory_prompt


def build_runtime_context_layers(ctx: RunContext, state: dict) -> ContextLayers:
    """Build the seven context layers used by the runtime prompt."""
    return ContextLayers(
        policy=ctx.base_system_prompt,
        tool=_render_recent_tool_context(state),
        retrieval=_render_retrieval_context(state),
        memory=render_long_term_memory(state.get("long_term_memories")),
        user=_render_user_context(ctx),
        task=render_task_memory(state.get("task_memory")),
        system=scene_memory_prompt(state.get("scene_memory")),
    )


def build_runtime_system_prompt(ctx: RunContext, state: dict) -> str:
    """Render layers for callers that still need a plain string."""
    return build_runtime_context_layers(ctx, state).render(
        allocate_context_budget(ctx.settings.agent_context_token_budget)
    )


def _render_user_context(ctx: RunContext) -> str:
    return (
        f"User id: {ctx.user_id}\n"
        f"Timezone: {ctx.user_tz}\n"
        "Preferences are advisory only. Policy constraints always override user preferences."
    )


def _render_retrieval_context(state: dict) -> str:
    items = state.get("retrieval_context") or state.get("rag_context") or []
    if isinstance(items, str):
        return items
    if not items:
        return "No retrieved semantic knowledge."
    return "\n".join(f"- {item}" for item in items)


def _render_recent_tool_context(state: dict) -> str:
    """Recent tool calls: prior turns (persisted task memory) + the current run."""
    prior_calls = (state.get("task_memory") or {}).get("tool_calls") or []
    current_calls = state.get("tool_call_history") or []
    records = [*prior_calls, *current_calls]
    if not records:
        return "No recent tool calls yet."
    lines = []
    for record in records[-10:]:
        if not isinstance(record, dict):
            continue
        tool_name = record.get("tool_name") or "tool"
        status = record.get("status") or "UNKNOWN"
        error_code = record.get("error_code")
        suffix = f", error={error_code}" if error_code else ""
        lines.append(f"- {tool_name}: {status}{suffix}")
    return "\n".join(lines) if lines else "No recent tool calls yet."
