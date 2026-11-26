import dataclasses

from src.edge_vla.mcp_server import context
from src.edge_vla.mcp_server.tools.stop_robot import handle_stop_robot, stop_robot_threaded
from src.edge_vla.models import RobotStatus, StopMode
from tests.test_edge_vla.fakes import FakeEmbodiment, set_test_context


class _SlowStopEmbodiment(FakeEmbodiment):
    """Mô phỏng độ trễ vật lý thật của việc dừng: sau `apply_stop`, vài lần `read_state()` đầu
    tiên vẫn báo `moving=True` (control loop thật cần vài chu kỳ mới ghi lại được trạng thái
    đã dừng), rồi mới `False`."""

    def __init__(self, stop_delay_reads: int = 2) -> None:
        super().__init__()
        self._stop_delay_reads = stop_delay_reads
        self._reads_since_stop = 0

    def apply_stop(self, mode: StopMode) -> None:
        super().apply_stop(mode)
        self._reads_since_stop = 0

    def read_state(self):
        state = super().read_state()
        if self._stopped and state.joints is not None:
            self._reads_since_stop += 1
            moving = self._reads_since_stop <= self._stop_delay_reads
            joints = [dataclasses.replace(j, moving=moving) for j in state.joints]
            return dataclasses.replace(state, joints=joints)
        return state


def test_handle_stop_robot_idle_is_idempotent():
    shared_state, _embodiment = set_test_context()
    ctx = context.get_context()

    result = handle_stop_robot(ctx, StopMode.EMERGENCY, "user_request")

    assert result["previous_status"] == RobotStatus.IDLE
    assert result["current_status"] == RobotStatus.IDLE
    assert result["stopped_episode_id"] is None
    assert result["mode_applied"] == StopMode.EMERGENCY
    assert not shared_state.stop_flag.is_set()


def test_handle_stop_robot_busy_transitions_through_stopping_to_idle():
    shared_state, _embodiment = set_test_context()
    ctx = context.get_context()
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_1")

    result = handle_stop_robot(ctx, StopMode.GRACEFUL, "user_request")

    assert result["previous_status"] == RobotStatus.BUSY
    assert result["current_status"] == RobotStatus.IDLE
    assert result["stopped_episode_id"] == "epi_1"
    assert shared_state.current_episode_id is None


def test_handle_stop_robot_confirm_timeout_goes_to_error():
    shared_state, embodiment = set_test_context()
    ctx = context.get_context()
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_2")
    # Khớp không bao giờ báo hết moving trong lúc chờ -> hết timeout xác nhận dừng.
    embodiment.joints = [dataclasses.replace(j, moving=True) for j in embodiment.joints]

    import src.edge_vla.mcp_server.tools.stop_robot as stop_robot_module

    original_timeout = stop_robot_module._CONFIRM_TIMEOUT_S
    stop_robot_module._CONFIRM_TIMEOUT_S = 0.05
    try:
        result = handle_stop_robot(ctx, StopMode.EMERGENCY, "user_request")
    finally:
        stop_robot_module._CONFIRM_TIMEOUT_S = original_timeout

    assert result["current_status"] == RobotStatus.ERROR


def test_stop_robot_never_disables_torque():
    shared_state, _embodiment = set_test_context()
    ctx = context.get_context()
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_3")

    result = handle_stop_robot(ctx, StopMode.EMERGENCY, "user_request")

    assert all(j["torque_enabled"] for j in result["final_state"]["joints"])


def test_stop_robot_latency_p95_under_500ms_gate():
    """Gate bắt buộc trước mọi release (DOC 3 §4.4 mục 5): latency stop <= 500ms P95."""
    embodiment = _SlowStopEmbodiment()
    shared_state, _embodiment = set_test_context(embodiment=embodiment)
    ctx = context.get_context()

    latencies = []
    for i in range(20):
        shared_state.state_machine.transition(RobotStatus.BUSY, episode_id=f"epi_{i}")

        result = stop_robot_threaded(ctx, StopMode.EMERGENCY, "latency_test")

        assert result["current_status"] == RobotStatus.IDLE
        latencies.append(result["latency_ms"])

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    assert p95 <= 500, f"P95 latency {p95}ms vượt gate 500ms: {latencies}"
