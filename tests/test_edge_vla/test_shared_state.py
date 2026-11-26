from src.edge_vla.mcp_server.shared_state import SharedState
from src.edge_vla.models import RobotStatus, StopMode


def test_initial_state_is_idle_no_stop_requested():
    state = SharedState()
    assert state.robot_status == RobotStatus.IDLE
    assert state.current_episode_id is None
    assert not state.stop_flag.is_set()


def test_request_stop_sets_flag_and_reason():
    state = SharedState()
    state.request_stop(StopMode.EMERGENCY, "safety violation")
    assert state.stop_flag.is_set()
    assert state.get_stop_request() == (StopMode.EMERGENCY, "safety violation")


def test_clear_stop_resets_flag_and_reason():
    state = SharedState()
    state.request_stop(StopMode.GRACEFUL, "user requested")
    state.clear_stop()
    assert not state.stop_flag.is_set()
    assert state.get_stop_request() == (None, None)


def test_status_reflects_state_machine_transitions():
    state = SharedState()
    state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_1")
    assert state.robot_status == RobotStatus.BUSY
    assert state.current_episode_id == "epi_1"
