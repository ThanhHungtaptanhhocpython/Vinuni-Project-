"""guard_reject & fail_fast — các node kết thúc run (DOC 1 §2.1)."""

from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..context import get_run_context
from ..errors import AppError


async def guard_reject(state: dict, config: RunnableConfig) -> dict:
    """Vượt limit vòng lặp / tool call / take_action hoặc quá hạn run."""
    ctx = get_run_context(config)
    settings = ctx.settings

    deadline = datetime.fromisoformat(state["deadline_at"])
    if datetime.now(UTC) >= deadline:
        error = AppError("AGENT_020", detail={"deadline_at": state["deadline_at"]})
        final_status = "TIMEOUT"
        explanation = (
            "Mình phải dừng tại đây vì run đã vượt quá thời gian tối đa cho phép "
            f"({settings.agent_max_run_duration_s}s). "
            "Bạn có thể gửi lại yêu cầu để tiếp tục phần còn dang dở."
        )
    else:
        error = AppError(
            "AGENT_011",
            detail={
                "iteration": state.get("iteration", 0),
                "tool_call_count": state.get("tool_call_count", 0),
                "take_action_count": state.get("take_action_count", 0),
            },
        )
        final_status = "FAILED"
        explanation = (
            "Mình phải dừng tại đây vì đã chạm giới hạn an toàn của một lượt "
            f"(tối đa {settings.agent_max_iterations} vòng suy luận, "
            f"{settings.agent_max_tool_calls} lần gọi công cụ, "
            f"{settings.agent_max_take_action} lần take_action). "
            "Nhiệm vụ có thể chưa hoàn tất — bạn hãy kiểm tra hiện trạng và "
            "gửi yêu cầu tiếp theo nếu muốn tiếp tục."
        )

    ctx.emitter.emit("error", {"run_id": ctx.run_id, "error": error.to_object()})
    return {
        "messages": [AIMessage(content=explanation)],
        "final_status": final_status,
        "error": error.to_object(),
    }


async def fail_fast(state: dict, config: RunnableConfig) -> dict:
    """Kết thúc run với lỗi từ preflight / load_context / agent_node."""
    ctx = get_run_context(config)
    error = state.get("error")
    ctx.emitter.emit("run.failed", {"run_id": ctx.run_id, "error": error})
    message = ""
    if isinstance(error, dict):
        raw = error.get("message")
        if isinstance(raw, str):
            message = raw.strip()
    return {
        "messages": [AIMessage(content=message or "Không thể hoàn thành yêu cầu.")],
        "final_status": "FAILED",
    }
