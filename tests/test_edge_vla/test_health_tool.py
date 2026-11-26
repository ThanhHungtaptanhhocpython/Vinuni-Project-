import asyncio

from src.edge_vla.mcp_server.registry import TOOL_REGISTRY
from src.edge_vla.mcp_server.shared_state import SharedState
from src.edge_vla.models import RobotStatus
from tests.test_edge_vla.fakes import set_test_context


def _set_test_context(shared_state: SharedState) -> None:
    set_test_context(shared_state=shared_state)


def test_health_check_reports_critical_when_cameras_not_available_yet():
    shared_state, embodiment = set_test_context()
    # Camera chưa sẵn sàng — mô phỏng bằng cách cho read_frames() không trả frame nào.
    embodiment.read_frames = lambda: {}

    result = asyncio.run(TOOL_REGISTRY["health_check"]({}, "trc_test"))

    assert result["ok"] is True
    data = result["data"]
    # Phase 5 (camera) chưa implement -> latest_frames rỗng -> camera check fail thật.
    assert data["overall"] == "CRITICAL"
    assert data["checks"]["robot_bus"]["ok"] is True
    assert data["checks"]["camera_top"]["ok"] is False
    assert data["checks"]["camera_wrist"]["ok"] is False
    assert data["edge_version"]
    assert data["uptime_s"] >= 0


def test_health_check_robot_bus_fails_when_disconnected():
    shared_state = SharedState()
    _set_test_context(shared_state)
    shared_state.state_machine.transition(RobotStatus.DISCONNECTED)

    result = asyncio.run(TOOL_REGISTRY["health_check"]({}, "trc_test"))

    assert result["data"]["checks"]["robot_bus"]["ok"] is False
    assert result["data"]["overall"] == "CRITICAL"
