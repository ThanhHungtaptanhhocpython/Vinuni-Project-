"""preflight — kiểm tra robot online, quyền, nạp capability tool (DOC 1 §2.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langchain_core.runnables import RunnableConfig

from ..context import get_run_context
from ..errors import AppError
from ..scene_memory import empty_scene_memory
from ..tools.registry import load_tools


async def preflight(state: dict, config: RunnableConfig) -> dict:
    ctx = get_run_context(config)
    now = datetime.now(UTC)
    started_at = now.isoformat(timespec="milliseconds")
    deadline_at = (
        now + timedelta(seconds=ctx.settings.agent_max_run_duration_s)
    ).isoformat(timespec="milliseconds")

    base = {
        "started_at": started_at,
        "deadline_at": deadline_at,
        "iteration": 0,
        "tool_call_count": 0,
        "take_action_count": 0,
        "tool_call_history": [],
        "artifacts": [],
        "scene_memory": empty_scene_memory(),
        "cancel_requested": False,
        "final_status": None,
        "error": None,
    }

    try:
        # Production còn kiểm tra: robot thuộc user (DB), không có run khác đang
        # chạy trên cùng robot (khoá phân tán Redis). Skeleton chưa có 2 lớp đó.
        if not await ctx.mcp.is_robot_online(ctx.robot_id):
            raise AppError("MCP_001", detail={"robot_id": ctx.robot_id})

        capabilities, lc_tools = await load_tools(ctx)
        snapshot = await ctx.mcp.call_tool(
            robot_id=ctx.robot_id,
            tool_name="get_robot_state",
            arguments={},
            trace_id=ctx.trace_id,
        )
    except AppError as exc:
        return {**base, "error": exc.to_object()}

    ctx.lc_tools = lc_tools
    robot_status = (snapshot.robot_state or {}).get("status")
    ctx.emitter.emit(
        "run.started",
        {
            "run_id": ctx.run_id,
            "robot_id": ctx.robot_id,
            "tool_count": len(lc_tools),
            "robot_status": robot_status,
        },
    )
    ctx.verbose.preflight(len(lc_tools), robot_status)
    return {
        **base,
        "available_tools": capabilities,
        "robot_state": snapshot.robot_state,
    }


def route_after_preflight(state: dict) -> str:
    return "fail_fast" if state.get("error") else "load_context"
