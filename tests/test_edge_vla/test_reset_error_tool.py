import asyncio
import dataclasses

from src.edge_vla.mcp_server.registry import TOOL_REGISTRY
from src.edge_vla.mcp_server.shared_state import SharedState
from src.edge_vla.models import RobotStatus
from tests.test_edge_vla.fakes import FakeEmbodiment, set_test_context


def _set_test_context(shared_state: SharedState, embodiment: FakeEmbodiment | None = None) -> FakeEmbodiment:
    _shared_state, embodiment = set_test_context(shared_state=shared_state, embodiment=embodiment)
    return embodiment


def _put_in_error(shared_state: SharedState, flags: set[str]) -> None:
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_test")
    shared_state.state_machine.transition(RobotStatus.STOPPING, episode_id="epi_test")
    shared_state.state_machine.transition(RobotStatus.ERROR)
    shared_state.safety_flags.update(flags)


def test_reset_error_idle_is_idempotent_noop():
    shared_state = SharedState()
    _set_test_context(shared_state)

    result = asyncio.run(TOOL_REGISTRY["reset_error"]({"flags": None}, "trc_test"))

    assert result["ok"] is True
    assert result["data"]["previous_status"] == "IDLE"
    assert result["data"]["current_status"] == "IDLE"
    assert result["data"]["cleared_flags"] == []


def test_reset_error_busy_returns_robot_010():
    shared_state = SharedState()
    _set_test_context(shared_state)
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_test")

    result = asyncio.run(TOOL_REGISTRY["reset_error"]({"flags": None}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_010"


def test_reset_error_clears_overload_flag_and_returns_to_idle():
    shared_state = SharedState()
    _set_test_context(shared_state)
    _put_in_error(shared_state, {"OVERLOAD"})

    result = asyncio.run(TOOL_REGISTRY["reset_error"]({"flags": None}, "trc_test"))

    assert result["ok"] is True
    assert result["data"]["previous_status"] == "ERROR"
    assert result["data"]["current_status"] == "IDLE"
    assert result["data"]["cleared_flags"] == ["OVERLOAD"]
    assert result["data"]["remaining_flags"] == []
    assert shared_state.robot_status == RobotStatus.IDLE


def test_reset_error_overheat_still_hot_try_all_stays_in_error_softly():
    shared_state = SharedState()
    embodiment = _set_test_context(shared_state)
    _put_in_error(shared_state, {"OVERHEAT"})
    embodiment.joints = [
        dataclasses.replace(j, present_temperature=70.0)  # trên temperature_warn_c=55
        for j in embodiment.joints
    ]

    result = asyncio.run(TOOL_REGISTRY["reset_error"]({"flags": None}, "trc_test"))

    assert result["ok"] is True
    assert result["data"]["current_status"] == "ERROR"
    assert result["data"]["remaining_flags"] == ["OVERHEAT"]
    assert shared_state.robot_status == RobotStatus.ERROR


def test_reset_error_explicit_overheat_request_still_hot_raises_robot_021():
    shared_state = SharedState()
    embodiment = _set_test_context(shared_state)
    _put_in_error(shared_state, {"OVERHEAT"})
    embodiment.joints = [dataclasses.replace(j, present_temperature=70.0) for j in embodiment.joints]

    result = asyncio.run(TOOL_REGISTRY["reset_error"]({"flags": ["OVERHEAT"]}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_021"


def test_reset_error_encoder_error_explicit_request_raises_robot_025():
    shared_state = SharedState()
    _set_test_context(shared_state)
    _put_in_error(shared_state, {"ENCODER_ERROR"})

    result = asyncio.run(TOOL_REGISTRY["reset_error"]({"flags": ["ENCODER_ERROR"]}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_025"
