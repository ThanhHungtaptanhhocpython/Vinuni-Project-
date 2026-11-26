"""In-process control plane for currently running agent runs."""

from __future__ import annotations

import asyncio
from typing import Literal

from .context import RunContext
from .errors import AppError

_ACTIVE_RUNS: dict[str, RunContext] = {}
_LOCK = asyncio.Lock()

MOTION_TOOLS = {"take_action", "go_home"}


async def register_run(ctx: RunContext) -> None:
    async with _LOCK:
        _ACTIVE_RUNS[ctx.run_id] = ctx


async def unregister_run(run_id: str) -> None:
    async with _LOCK:
        _ACTIVE_RUNS.pop(run_id, None)


async def get_active_run(run_id: str) -> RunContext | None:
    async with _LOCK:
        return _ACTIVE_RUNS.get(run_id)


async def request_cancel(ctx: RunContext) -> dict[str, str | bool | None]:
    """Request cancellation and run an emergency-safe stop if motion is active."""
    active_tool = ctx.active_tool_name
    active_episode_id = ctx.active_episode_id
    dangerous = active_episode_id is not None or active_tool in MOTION_TOOLS
    safety_action: Literal["none", "abort", "stop_robot"] = "none"
    safety_error: str | None = None

    ctx.emitter.emit(
        "emergency.stop.requested",
        {
            "run_id": ctx.run_id,
            "dangerous_activity": dangerous,
            "active_tool": active_tool,
            "active_episode_id": active_episode_id,
        },
    )

    if dangerous and ctx.mcp is not None and not ctx.safety_stop_attempted:
        ctx.safety_stop_attempted = True
        ctx.emitter.emit(
            "emergency.stop.safety_started",
            {
                "run_id": ctx.run_id,
                "active_tool": active_tool,
                "active_episode_id": active_episode_id,
            },
        )
        try:
            if active_episode_id:
                safety_action = "abort"
                await ctx.mcp.call_tool(
                    robot_id=ctx.robot_id,
                    tool_name="abort",
                    arguments={"task_id": active_episode_id},
                    trace_id=ctx.trace_id,
                    timeout_s=3.0,
                    priority=1,
                )
            else:
                safety_action = "stop_robot"
                await ctx.mcp.call_tool(
                    robot_id=ctx.robot_id,
                    tool_name="stop_robot",
                    arguments={"mode": "GRACEFUL"},
                    trace_id=ctx.trace_id,
                    timeout_s=3.0,
                    priority=1,
                )
        except AppError as exc:
            if active_episode_id and safety_action == "abort":
                try:
                    safety_action = "stop_robot"
                    await ctx.mcp.call_tool(
                        robot_id=ctx.robot_id,
                        tool_name="stop_robot",
                        arguments={"mode": "GRACEFUL"},
                        trace_id=ctx.trace_id,
                        timeout_s=3.0,
                        priority=1,
                    )
                except AppError as fallback_exc:
                    safety_error = fallback_exc.message
                except Exception as fallback_exc:
                    safety_error = str(fallback_exc)
            else:
                safety_error = exc.message
        except Exception as exc:
            safety_error = str(exc)

        robot_state = None
        try:
            state_result = await ctx.mcp.call_tool(
                robot_id=ctx.robot_id,
                tool_name="get_robot_state",
                arguments={},
                trace_id=ctx.trace_id,
                timeout_s=5.0,
                priority=1,
            )
            robot_state = state_result.robot_state or state_result.data
        except Exception:
            robot_state = None

        ctx.emitter.emit(
            "emergency.stop.safety_completed",
            {
                "run_id": ctx.run_id,
                "active_tool": active_tool,
                "active_episode_id": active_episode_id,
                "safety_action": safety_action,
                "safety_error": safety_error,
                "robot_state": robot_state,
            },
        )
        if isinstance(robot_state, dict):
            ctx.emitter.emit(
                "robot.state.updated",
                {
                    "run_id": ctx.run_id,
                    "status": "STOPPING" if robot_state.get("busy") else "IDLE",
                    "connection_status": "ONLINE",
                    "robot_state": robot_state,
                    "source": "emergency_stop",
                },
            )

    ctx.cancel_event.set()
    ctx.emitter.emit("emergency.stop.cancel_forwarded", {"run_id": ctx.run_id})
    return {
        "cancel_requested": True,
        "dangerous_activity": dangerous,
        "active_tool": active_tool,
        "active_episode_id": active_episode_id,
        "safety_action": safety_action,
        "safety_error": safety_error,
    }
