"""Keep `robots.robot_status` aligned with live work.

Chat/agent runs used to leave the registry stuck at IDLE — only the schedule
path called `robot_store.set_status`. The dashboard badge reads that field, so
a running turn still showed "Rảnh" while stats counted "Đang chạy".
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from stores.robots import robot_store

logger = logging.getLogger(__name__)

# Do not clobber operator/halt states with BUSY/IDLE overlays.
_PROTECTED = frozenset({"ERROR", "STOPPING", "DISCONNECTED"})


def mark_busy(robot_id: str) -> None:
    robot = robot_store.get(robot_id)
    if robot is None:
        return
    if robot.get("robot_status") in _PROTECTED:
        return
    robot_store.set_status(robot_id, "BUSY")


def release_if_idle(robot_id: str) -> None:
    """Clear BUSY after take_action/go_home finishes. Agent runs may continue while IDLE."""
    robot = robot_store.get(robot_id)
    if robot is None:
        return
    if robot.get("robot_status") in _PROTECTED:
        return
    robot_store.set_status(robot_id, "IDLE")


def _edge_reports_busy(data: dict[str, Any] | None, robot_state: dict[str, Any] | None) -> bool:
    payload = data or {}
    state = robot_state or {}
    if payload.get("busy") or state.get("busy"):
        return True
    status = payload.get("status") or state.get("status")
    return status in {"RUNNING", "BUSY"}


def ui_status_from_edge(robot_state: dict[str, Any] | None) -> str:
    """Map Edge `get_robot_state` (busy/RUNNING) onto the UI robot_status enum."""
    if _edge_reports_busy(None, robot_state):
        return "BUSY"
    status = (robot_state or {}).get("status")
    if status in {"IDLE", "BUSY", "STOPPING", "ERROR", "DISCONNECTED"}:
        return str(status)
    return "IDLE"


def is_robot_busy(robot_id: str) -> bool:
    """True while take_action or go_home is moving the arm (registry BUSY/STOPPING)."""
    robot = robot_store.get(robot_id)
    if robot is None:
        return False
    return robot.get("robot_status") in {"BUSY", "STOPPING"}


async def refresh_live_status(robot: dict[str, Any]) -> dict[str, Any]:
    """Overlay Edge busy onto the registry for list/detail pages.

    Registry BUSY from `mark_busy` (take_action / go_home) is authoritative —
    never downgrade it to IDLE from a probe. The HTTP transport used to
    short-circuit `get_robot_state` with a fresh synthetic IDLE, which wiped
    BUSY on every dashboard poll and made other pages look idle mid-motion.
    Only upgrade IDLE → BUSY when Edge reports busy.
    """
    robot_id = robot["id"]
    stored = robot.get("robot_status") or "IDLE"
    if stored in _PROTECTED:
        return robot
    # Cloud-agent already marked motion in flight — keep it for other pages.
    if stored == "BUSY":
        return robot

    try:
        from agents.tools.http_transport import HttpMcpTransport
        from agents.tools.mcp_client import McpClient
        from agents.tools.mock_transport import MockEdgeTransport
        from config import get_settings

        settings = get_settings()
        transport = (
            HttpMcpTransport(settings)
            if settings.mcp_endpoint_url and settings.app_env != "test"
            else MockEdgeTransport()
        )
        mcp = McpClient(transport)
        result = await mcp.call_tool(
            robot_id=robot_id,
            tool_name="get_robot_state",
            arguments={},
            trace_id=f"trc_status_{uuid4().hex[:10]}",
            timeout_s=2.0,
        )
        if result.ok and _edge_reports_busy(result.data, result.robot_state):
            robot_store.set_status(robot_id, "BUSY")
            return {**robot, "robot_status": "BUSY"}
    except Exception:
        logger.debug("edge status probe failed for %s", robot_id, exc_info=True)

    return robot
