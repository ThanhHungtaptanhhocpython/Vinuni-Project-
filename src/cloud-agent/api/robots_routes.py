from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

from agents.errors import AppError
from agents.tools.http_transport import HttpMcpTransport
from agents.tools.mcp_client import McpClient, ToolResult
from agents.tools.mock_transport import MockEdgeTransport
from auth.deps import require_user_if_configured
from auth.store import User
from config import get_settings
from fastapi import APIRouter, Depends, Query, status
from services.robot_status import mark_busy, refresh_live_status, release_if_idle
from stores.robots import robot_store
from stores.runs import run_store

from api.common import api_error, current_user_id, envelope

router = APIRouter(tags=["robots"])


def _mcp_client() -> McpClient:
    """Same transport selection as chat runs (real MCP or mock)."""
    settings = get_settings()
    transport = (
        HttpMcpTransport(settings)
        if settings.mcp_endpoint_url and settings.app_env != "test"
        else MockEdgeTransport()
    )
    return McpClient(transport)


def _raise_mcp(exc: AppError) -> None:
    raise api_error(exc.code, exc.message, exc.http_status) from exc


async def _call_robot_tool(
    robot_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout_s: float | None = None,
) -> ToolResult:
    if robot_store.get(robot_id) is None:
        raise api_error("SCHED_004", "Robot đã chọn không còn tồn tại.", 404)
    mcp = _mcp_client()
    try:
        result = await mcp.call_tool(
            robot_id=robot_id,
            tool_name=tool_name,
            arguments=arguments or {},
            trace_id=f"trc_ui_{uuid4().hex[:12]}",
            timeout_s=timeout_s,
        )
    except AppError as exc:
        _raise_mcp(exc)
    if not result.ok:
        message = (result.error.message if result.error else None) or "Lệnh trên Edge thất bại."
        code = (result.error.code if result.error else None) or "MCP_004"
        raise api_error(code, message, 502)
    return result


@router.get("/robots")
async def list_robots(
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    # Overlay live BUSY from active chat/schedule runs + Edge busy flag.
    # Registry alone stays IDLE for chat runs (only the scheduler used to write it).
    robots = [await refresh_live_status(robot) for robot in robot_store.list()]
    return envelope(robots)


@router.post("/robots", status_code=status.HTTP_201_CREATED)
async def create_robot(
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    """Manual pairing is disabled — robots are seeded at cloud-agent startup
    (and refreshed when Edge self-registers)."""
    raise api_error(
        "ROBOT_004",
        "Ghép nối robot qua API đã tạm tắt. Robot được tạo tự động khi Cloud/Edge khởi động.",
        403,
    )


@router.get("/robots/{robot_id}")
async def get_robot(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    robot = robot_store.get(robot_id)
    if robot is None:
        raise api_error("SCHED_004", "Robot đã chọn không còn tồn tại.", 404)
    return envelope(await refresh_live_status(robot))


@router.get("/robots/{robot_id}/stats")
async def get_robot_stats(
    robot_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
    days: int = Query(default=14, ge=1, le=90),
) -> dict:
    if robot_store.get(robot_id) is None:
        raise api_error("SCHED_004", "Robot đã chọn không còn tồn tại.", 404)
    stats = run_store.stats(user_id=current_user_id(user), robot_id=robot_id, days=days)
    # 404 ở đây nghĩa là "chưa có lượt chạy nào" — frontend đọc đúng mã này để
    # phân biệt "chưa có dữ liệu" với lỗi thật (xem `fetchRobotStats`), nên
    # KHÔNG được đổi thành trả 200 kèm số 0 nếu không muốn đổi luôn tiêu đó.
    if stats["total_runs"] == 0:
        raise api_error("AGENT_013", "Chưa có lượt chạy nào trong khoảng thời gian này.", 404)
    return envelope(stats)


@router.get("/robots/{robot_id}/health")
async def get_robot_health(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    result = await _call_robot_tool(robot_id, "health_check")
    return envelope(result.model_dump())


@router.post("/robots/{robot_id}/stop")
async def stop_robot(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
    mode: str = Query(default="GRACEFUL"),
) -> dict:
    if robot_store.get(robot_id) is None:
        raise api_error("SCHED_004", "Robot đã chọn không còn tồn tại.", 404)
    mcp = _mcp_client()
    try:
        names = {tool.get("name") for tool in await mcp.list_tools(robot_id)}
    except AppError as exc:
        _raise_mcp(exc)
    # Native edge_vla exposes `abort`; mock/DOC naming still uses `stop_robot`.
    if "abort" in names:
        result = await _call_robot_tool(
            robot_id, "abort", {"task_id": ""}, timeout_s=3.0
        )
    else:
        result = await _call_robot_tool(
            robot_id, "stop_robot", {"mode": mode}, timeout_s=3.0
        )
    return envelope({"ok": True, "tool": result.tool_name, "data": result.data})


@router.post("/robots/{robot_id}/go-home")
async def go_home_robot(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    # Edge go_home does not flip store.busy — registry must carry BUSY so
    # dashboard / other sessions see motion while this call blocks.
    mark_busy(robot_id)
    try:
        result = await _call_robot_tool(robot_id, "go_home")
    except Exception:
        release_if_idle(robot_id)
        raise
    release_if_idle(robot_id)
    return envelope({"ok": True, "tool": result.tool_name, "data": result.data})


@router.post("/robots/{robot_id}/reset-error")
async def reset_robot_error(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    result = await _call_robot_tool(robot_id, "reset_error")
    cleared: list[Any] = []
    if isinstance(result.robot_state, dict):
        flags = result.robot_state.get("error_flags")
        if isinstance(flags, list):
            cleared = flags
    return envelope({"ok": True, "cleared_flags": cleared, "data": result.data})


@router.post("/robots/{robot_id}/reset-episode")
async def reset_robot_episode(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    """Reset the sim scene (demo/dev). Real mode only clears the policy queue."""
    result = await _call_robot_tool(robot_id, "reset_episode", timeout_s=30.0)
    return envelope({"ok": True, "tool": result.tool_name, "data": result.data})
