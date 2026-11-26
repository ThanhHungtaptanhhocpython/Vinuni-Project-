"""agent_node — gọi LLM với tools, stream token (DOC 1 §2.1).

route_decision — conditional edge: tool_calls & chưa vượt limit → tool_node;
vượt limit → guard_reject; không có tool call → END.
"""

from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END

from ..context import get_run_context
from ..context_builder import build_context
from ..errors import AppError, RunCancelledError
from ..llm import invoke_llm
from ..prompt_context import build_runtime_context_layers


def _text_of(message) -> str:
    text = getattr(message, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return " ".join(parts)
    return str(content or "")


def _cancelled_update(ctx, partial_content: str | None = None) -> dict:
    # Lưu message dở dang (nếu có) với truncated = true (DOC 1 §2.8)
    update: dict = {"cancel_requested": True, "final_status": "CANCELLED"}
    if partial_content:
        update["messages"] = [
            AIMessage(content=partial_content, additional_kwargs={"truncated": True})
        ]
    ctx.emitter.emit("run.cancelled", {"run_id": ctx.run_id})
    return update


async def agent_node(state: dict, config: RunnableConfig) -> dict:
    ctx = get_run_context(config)
    iteration = int(state.get("iteration", 0)) + 1
    ctx.verbose.think_loop(iteration)

    # Điểm kiểm tra cancel trước node (DOC 1 §2.8)
    if ctx.cancel_event.is_set() or state.get("cancel_requested"):
        return _cancelled_update(ctx)

    try:
        if ctx.base_system_prompt:
            layers = build_runtime_context_layers(ctx, state)
            ctx.system_prompt = layers.render()
            system_prompt = layers
        else:
            system_prompt = ctx.system_prompt
        llm_input = await build_context(ctx, system_prompt, state.get("messages", []))
        message = await invoke_llm(ctx, llm_input, ctx.lc_tools)

        # Tool call arguments không parse được → retry 1 lần với thông báo sửa lỗi (§2.9)
        if message.invalid_tool_calls and not message.tool_calls:
            errors = "; ".join(
                str(bad.get("error")) for bad in message.invalid_tool_calls
            )
            correction = HumanMessage(
                "Tool call trước đó có arguments không hợp lệ "
                f"({errors}). Hãy gọi lại tool với arguments đúng schema."
            )
            message = await invoke_llm(ctx, [*llm_input, message, correction], ctx.lc_tools)
            if message.invalid_tool_calls and not message.tool_calls:
                raise AppError("AGENT_004", detail={"reason": "invalid_tool_calls", "errors": errors})
    except RunCancelledError as exc:
        return _cancelled_update(ctx, exc.partial_content)
    except AppError as exc:
        return {"error": exc.to_object()}

    tool_calls = list(message.tool_calls or [])

    content = _text_of(message)
    metadata: dict = {
        "run_id": ctx.run_id,
        "message_id": message.additional_kwargs.get("message_id"),
        "tool_calls": len(tool_calls),
    }
    if content:
        metadata["content"] = content

    ctx.emitter.emit("message.completed", metadata)

    update: dict = {"messages": [message], "iteration": state.get("iteration", 0) + 1}
    if not tool_calls:
        update["final_status"] = "COMPLETED"
        ctx.verbose.final_answer(_text_of(message))
    return update


def route_decision(state: dict, config: RunnableConfig) -> str:
    """Conditional edge sau agent_node (DOC 1 §2.1)."""
    if state.get("error"):
        return "fail_fast"
    if state.get("cancel_requested"):
        return END

    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    tool_calls = getattr(last, "tool_calls", None) or []
    if not tool_calls:
        return END

    ctx = get_run_context(config)
    settings = ctx.settings

    deadline = datetime.fromisoformat(state["deadline_at"])
    if datetime.now(UTC) >= deadline:
        return "guard_reject"

    new_take_actions = sum(1 for c in tool_calls if c["name"] == "take_action")
    over_limit = (
        state.get("iteration", 0) >= settings.agent_max_iterations
        or state.get("tool_call_count", 0) + len(tool_calls) > settings.agent_max_tool_calls
        or state.get("take_action_count", 0) + new_take_actions > settings.agent_max_take_action
    )
    return "guard_reject" if over_limit else "tools"
