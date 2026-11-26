"""Unit tests for IkPolicyRunner's state machine, using a fake SimHandle (no
MuJoCo/robosuite required). New test module — `tests/test_edge_vla` is stale
(imports deleted `mcp_server.registry` / `embodiment.spec` / `vla_runtime.policy`)
and out of scope here."""

from __future__ import annotations

import numpy as np
import pytest

from src.edge_vla import config
from src.edge_vla.sim.ik_policy import (
    APPROACH,
    DESCEND,
    DONE,
    GRASP,
    LIFT,
    RELEASE,
    RETREAT,
    TRANSPORT,
    IkPolicyRunner,
    absolute_pose_action,
)


class FakeSimHandle:
    def __init__(self, object_pos, basket_pos, home_eef_pos):
        self._object_pos = list(object_pos)
        self._basket_pos = list(basket_pos)
        self.home_eef_pos = np.asarray(home_eef_pos, dtype=np.float64)
        self.home_eef_axisangle = np.array([np.pi, 0.0, 0.0], dtype=np.float64)

    def detections(self) -> dict:
        return {
            "object": "milk",
            "object_pos": None if self._object_pos is None else list(self._object_pos),
            "basket_pos": list(self._basket_pos),
            "object_to_eef_pos": None,
        }

    def set_object_pos(self, pos):
        self._object_pos = list(pos)


def _obs(eef_pos: np.ndarray) -> dict:
    return {"robot_state": {"eef": {"pos": eef_pos}}}


def _drive_to_target(policy: IkPolicyRunner, eef_pos: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Step the fake eef towards `target` (as OSC would) until the policy
    transitions away from its current state, or safety-fail after too many ticks."""
    state_before = policy.state
    for _ in range(200):
        action = policy.act(_obs(eef_pos), "pick up the milk and place it in the basket")
        eef_pos = action[:3].astype(np.float64)  # perfect tracking assumption
        if policy.state != state_before:
            return eef_pos
    raise AssertionError(f"policy stuck in state {state_before}")


@pytest.fixture
def sim():
    return FakeSimHandle(object_pos=[0.0, 0.0, 0.05], basket_pos=[0.3, 0.3, 0.05], home_eef_pos=[0.0, 0.0, 0.3])


def test_absolute_pose_action_clamps_step():
    cur = np.array([0.0, 0.0, 0.0])
    target = np.array([1.0, 0.0, 0.0])
    axisangle = np.array([np.pi, 0.0, 0.0])
    action = absolute_pose_action(cur, target, axisangle, -1.0)
    assert action.shape == (7,)
    assert action[0] == pytest.approx(config.IK_MAX_STEP)
    assert action[-1] == -1.0


def test_reset_raises_when_object_pos_missing(sim):
    sim._object_pos = None  # type: ignore[assignment]
    policy = IkPolicyRunner(sim)
    with pytest.raises(ValueError):
        policy.reset()


def test_full_state_order_and_gripper_values(sim):
    policy = IkPolicyRunner(sim)
    policy.reset()
    assert policy.state == APPROACH

    eef_pos = sim.home_eef_pos.copy()

    # APPROACH -> DESCEND
    target = np.asarray(sim._object_pos) + np.array([0, 0, config.IK_APPROACH_HEIGHT])
    eef_pos = _drive_to_target(policy, eef_pos, target)
    assert policy.state == DESCEND

    # DESCEND -> GRASP
    target = np.asarray(sim._object_pos) + np.array([0, 0, config.IK_GRASP_Z_OFFSET])
    eef_pos = _drive_to_target(policy, eef_pos, target)
    assert policy.state == GRASP

    # GRASP holds pose and closes the gripper for IK_GRIPPER_SETTLE_STEPS, then -> LIFT
    action = policy.act(_obs(eef_pos), "task")
    assert action[-1] == 1.0  # gripper closed
    for _ in range(config.IK_GRIPPER_SETTLE_STEPS):
        action = policy.act(_obs(eef_pos), "task")
    assert policy.state == LIFT

    # object_pos is frozen from GRASP onward, even if the fake sim "moves" it
    sim.set_object_pos([9.0, 9.0, 9.0])

    target = np.asarray([0.0, 0.0, 0.05]) + np.array([0, 0, config.IK_LIFT_HEIGHT])
    eef_pos = _drive_to_target(policy, eef_pos, target)
    assert policy.state == TRANSPORT

    target = np.asarray(sim._basket_pos) + np.array([0, 0, config.IK_DROP_HEIGHT])
    eef_pos = _drive_to_target(policy, eef_pos, target)
    assert policy.state == RELEASE

    action = policy.act(_obs(eef_pos), "task")
    assert action[-1] == -1.0  # gripper open
    for _ in range(config.IK_GRIPPER_SETTLE_STEPS):
        action = policy.act(_obs(eef_pos), "task")
    assert policy.state == RETREAT

    eef_pos = _drive_to_target(policy, eef_pos, sim.home_eef_pos)
    assert policy.state == DONE

    # DONE holds current pose forever (gripper open) and never advances further.
    for _ in range(50):
        action = policy.act(_obs(eef_pos), "task")
        assert policy.state == DONE
        assert action[-1] == -1.0
        assert np.allclose(action[:3], eef_pos)


def test_state_timeout_forces_advance(sim):
    policy = IkPolicyRunner(sim)
    policy.reset()
    # eef never moves (stuck) -> should still force-advance after IK_STATE_TIMEOUT_STEPS.
    stuck_eef = np.array([100.0, 100.0, 100.0])
    for _ in range(config.IK_STATE_TIMEOUT_STEPS):
        policy.act(_obs(stuck_eef), "task")
    assert policy.state == DESCEND
