"""load_context — dựng system prompt + áp chiến lược trim (DOC 1 §2.1, §2.5).

Production: node này còn nạp N message gần nhất của session từ DB. Ở skeleton,
lịch sử được seed sẵn vào state["messages"] khi tạo run.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from ..context import get_run_context
from ..context_builder import build_context
from ..errors import AppError
from ..prompt_context import build_runtime_context_layers
from ..prompts import build_system_prompt


async def load_context(state: dict, config: RunnableConfig) -> dict:
    ctx = get_run_context(config)
    robot_state = state.get("robot_state") or {}

    ctx.base_system_prompt = build_system_prompt(
        robot_id=ctx.robot_id,
        robot_model=robot_state.get("model", "SO-101"),
        robot_status=robot_state.get("status", "UNKNOWN"),
        user_tz=ctx.user_tz,
        source=state.get("source", "CHAT"),
        max_take_action=ctx.settings.agent_max_take_action,
    )
    layers = build_runtime_context_layers(ctx, state)
    ctx.system_prompt = layers.render()

    # Chạy trim một lần để phát hiện sớm AGENT_010 (context vượt ngân sách).
    # agent_node sẽ tự build lại context mỗi vòng lặp.
    try:
        await build_context(ctx, layers, state.get("messages", []))
    except AppError as exc:
        return {"error": exc.to_object()}
    return {}


def route_after_load_context(state: dict) -> str:
    return "fail_fast" if state.get("error") else "agent"
