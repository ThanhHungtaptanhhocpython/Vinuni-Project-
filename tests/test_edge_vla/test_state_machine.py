import pytest

from src.edge_vla.mcp_server.state_machine import InvalidTransitionError, StateMachine
from src.edge_vla.models import RobotStatus


def test_starts_idle_with_no_episode():
    sm = StateMachine()
    assert sm.status == RobotStatus.IDLE
    assert sm.episode_id is None


def test_idle_to_busy_requires_episode_id():
    sm = StateMachine()
    with pytest.raises(InvalidTransitionError):
        sm.transition(RobotStatus.BUSY)


def test_valid_transition_sequence_keeps_invariant():
    sm = StateMachine()
    sm.transition(RobotStatus.BUSY, episode_id="epi_1")
    assert sm.status == RobotStatus.BUSY
    assert sm.episode_id == "epi_1"

    sm.transition(RobotStatus.STOPPING, episode_id="epi_1")
    assert sm.status == RobotStatus.STOPPING
    assert sm.episode_id == "epi_1"

    sm.transition(RobotStatus.IDLE)
    assert sm.status == RobotStatus.IDLE
    assert sm.episode_id is None


def test_stopping_to_error_clears_episode_id():
    sm = StateMachine()
    sm.transition(RobotStatus.BUSY, episode_id="epi_1")
    sm.transition(RobotStatus.STOPPING, episode_id="epi_1")
    sm.transition(RobotStatus.ERROR)
    assert sm.status == RobotStatus.ERROR
    assert sm.episode_id is None


def test_error_to_idle_via_reset_error():
    sm = StateMachine()
    sm.transition(RobotStatus.BUSY, episode_id="epi_1")
    sm.transition(RobotStatus.STOPPING, episode_id="epi_1")
    sm.transition(RobotStatus.ERROR)
    sm.transition(RobotStatus.IDLE)
    assert sm.status == RobotStatus.IDLE


def test_invalid_transition_rejected():
    sm = StateMachine()
    with pytest.raises(InvalidTransitionError):
        sm.transition(RobotStatus.STOPPING)


def test_disconnected_reachable_from_any_state():
    sm = StateMachine()
    sm.transition(RobotStatus.BUSY, episode_id="epi_1")
    sm.transition(RobotStatus.DISCONNECTED)
    assert sm.status == RobotStatus.DISCONNECTED
    assert sm.episode_id is None


def test_busy_transition_rejects_mismatched_episode_id_presence():
    sm = StateMachine()
    with pytest.raises(InvalidTransitionError):
        sm.transition(RobotStatus.CALIBRATING, episode_id="epi_1")
