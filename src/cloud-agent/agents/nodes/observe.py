"""observe_node — chuẩn hoá ToolResult thành ToolMessage, cập nhật state (DOC 1 §2.1).

Kết quả tool được bọc <tool_result untrusted="true"> [DOC 0 §6.7], robot_state
và counter được cập nhật, ảnh mới được ghi vào artifacts.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from services.robot_status import ui_status_from_edge

from ..context import get_run_context
from ..scene_memory import update_scene_memory
from ..tools.serialize import build_image_message, build_tool_message


async def observe_node(state: dict, config: RunnableConfig) -> dict:
    ctx = get_run_context(config)
    results = state.get("pending_tool_results") or []

    messages = []
    robot_state = state.get("robot_state")
    new_artifacts: list[dict] = []
    scene_memory = state.get("scene_memory")
    take_action_count = 0
    current_episode_id = state.get("current_episode_id")

    for call, result in results:
        messages.append(build_tool_message(result, call.get("id") or ""))

        img_msg = build_image_message(result)
        if img_msg:
            messages.append(img_msg)
        if result.robot_state is not None:
            robot_state = result.robot_state
        if call["name"] == "take_action":
            take_action_count += 1
            if result.ok and isinstance(result.data, dict):
                current_episode_id = (
                    result.data.get("task_id")
                    or result.data.get("episode_id")
                    or current_episode_id
                )
        elif call["name"] == "check_status" and isinstance(result.data, dict):
            status = result.data.get("status")
            if status and status != "RUNNING":
                current_episode_id = None
        new_artifacts.extend(img.model_dump(exclude_none=True) for img in result.images)
        scene_memory = update_scene_memory(
            scene_memory,
            tool_name=call["name"],
            data=result.data,
            images=result.images,
        )

    if robot_state is not None:
        status = ui_status_from_edge(robot_state)
        ctx.emitter.emit(
            "robot.state.updated",
            {
                "run_id": ctx.run_id,
                "status": status,
                "busy": bool(robot_state.get("busy")),
                "robot_status": status,
            },
        )

    return {
        "messages": messages,
        "pending_tool_results": [],
        "robot_state": robot_state,
        "current_episode_id": current_episode_id,
        "scene_memory": scene_memory,
        "artifacts": state.get("artifacts", []) + new_artifacts,
        "tool_call_count": state.get("tool_call_count", 0) + len(results),
        "take_action_count": state.get("take_action_count", 0) + take_action_count,
    }
