"""Ground-truth pick-and-place state machine — the "ik" policy backend.

Duck-types PolicyRunner's reset()/act() interface so SimIkController can reuse
SimController's control loop (safety check, viewer publish, retry/MAX_RETRY)
unchanged. No torch, no model weights: reads object/basket coordinates
straight from `SimHandle.detections()` and drives the robosuite OSC
controller with absolute end-effector poses.
"""

from __future__ import annotations

import numpy as np

from .. import config
from .sim_env import SimHandle

_GRIPPER_OPEN = -1.0
_GRIPPER_CLOSE = 1.0

APPROACH = "APPROACH"
DESCEND = "DESCEND"
GRASP = "GRASP"
LIFT = "LIFT"
TRANSPORT = "TRANSPORT"
RELEASE = "RELEASE"
RETREAT = "RETREAT"
DONE = "DONE"

_STATE_ORDER = [APPROACH, DESCEND, GRASP, LIFT, TRANSPORT, RELEASE, RETREAT, DONE]


def absolute_pose_action(
    cur_pos: np.ndarray, target_pos: np.ndarray, axisangle: np.ndarray, gripper: float
) -> np.ndarray:
    """Build a 7-d absolute-pose OSC action. Clamps the position step to
    IK_MAX_STEP per tick so the controller doesn't get a jump large enough to
    trip safety.check() or make OSC overshoot."""
    delta = np.clip(target_pos - cur_pos, -config.IK_MAX_STEP, config.IK_MAX_STEP)
    step_target = cur_pos + delta
    return np.concatenate([step_target, axisangle, [gripper]]).astype(np.float32)


class IkPolicyRunner:
    def __init__(self, sim: SimHandle):
        self.sim = sim
        self.state = APPROACH
        self.state_step = 0
        self._frozen_object_pos: np.ndarray | None = None

    def reset(self) -> None:
        self.state = APPROACH
        self.state_step = 0
        self._frozen_object_pos = None
        det = self.sim.detections()
        if det.get("object_pos") is None:
            raise ValueError(
                f"IkPolicyRunner.reset(): no ground-truth position for object "
                f"{det.get('object')!r} (missing '{det.get('object')}_1_pos' in sim obs)"
            )

    def act(self, observation: dict, task_text: str) -> np.ndarray:
        eef_pos = np.asarray(observation["robot_state"]["eef"]["pos"], dtype=np.float64)
        axisangle = self.sim.home_eef_axisangle
        assert axisangle is not None, "IkPolicyRunner.act() called before SimHandle built home pose"

        det = self.sim.detections()
        object_pos = (
            self._frozen_object_pos
            if self._frozen_object_pos is not None
            else np.asarray(det["object_pos"], dtype=np.float64)
        )
        basket_pos = np.asarray(det["basket_pos"], dtype=np.float64)

        target, gripper, reached = self._state_target(eef_pos, object_pos, basket_pos)
        action = absolute_pose_action(eef_pos, target, axisangle, gripper)

        self.state_step += 1
        timed_out = self.state_step >= config.IK_STATE_TIMEOUT_STEPS
        if reached or timed_out:
            self._advance(object_pos)

        return action

    def _state_target(
        self, eef_pos: np.ndarray, object_pos: np.ndarray, basket_pos: np.ndarray
    ) -> tuple[np.ndarray, float, bool]:
        if self.state == APPROACH:
            target = object_pos + np.array([0.0, 0.0, config.IK_APPROACH_HEIGHT])
            return target, _GRIPPER_OPEN, self._within_tol(eef_pos, target)
        if self.state == DESCEND:
            target = object_pos + np.array([0.0, 0.0, config.IK_GRASP_Z_OFFSET])
            return target, _GRIPPER_OPEN, self._within_tol(eef_pos, target)
        if self.state == GRASP:
            target = eef_pos.copy()
            return target, _GRIPPER_CLOSE, self.state_step >= config.IK_GRIPPER_SETTLE_STEPS
        if self.state == LIFT:
            target = object_pos + np.array([0.0, 0.0, config.IK_LIFT_HEIGHT])
            return target, _GRIPPER_CLOSE, self._within_tol(eef_pos, target)
        if self.state == TRANSPORT:
            target = basket_pos + np.array([0.0, 0.0, config.IK_DROP_HEIGHT])
            return target, _GRIPPER_CLOSE, self._within_tol(eef_pos, target)
        if self.state == RELEASE:
            target = eef_pos.copy()
            return target, _GRIPPER_OPEN, self.state_step >= config.IK_GRIPPER_SETTLE_STEPS
        if self.state == RETREAT:
            target = self.sim.home_eef_pos
            assert target is not None
            return target, _GRIPPER_OPEN, self._within_tol(eef_pos, target)
        # DONE: hold current pose so _run_task can run out the remaining k steps.
        return eef_pos.copy(), _GRIPPER_OPEN, False

    def _within_tol(self, eef_pos: np.ndarray, target: np.ndarray) -> bool:
        return bool(np.linalg.norm(target - eef_pos) < config.IK_POS_TOL)

    def _advance(self, object_pos: np.ndarray) -> None:
        idx = _STATE_ORDER.index(self.state)
        if idx + 1 < len(_STATE_ORDER):
            self.state = _STATE_ORDER[idx + 1]
        if self.state == GRASP:
            # Object is about to enter the gripper — freeze its pose now
            # (last live reading) so it doesn't drift under the closing claw.
            self._frozen_object_pos = object_pos.copy()
        self.state_step = 0
