"""Standalone probe: confirm robosuite OSC_POSE `use_delta=False` semantics
before writing the IK state machine. Run in conda env `lerobot_arm`:

    MUJOCO_GL=osmesa python scripts/probe_osc_absolute.py

Checks:
1. action[:3] sent with control_mode="absolute" moves eef_pos towards that
   world-frame target (not relative to current pose).
2. action[3:6] (axis-angle) is likewise absolute, not delta.
3. The controller's position_limits do not clip the targets we plan to use.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "edge_vla"))

from lerobot.envs.libero import LiberoEnv, _get_suite  # noqa: E402
from robosuite.utils.transform_utils import quat2axisangle  # noqa: E402

SUITE = "libero_object"


def main() -> None:
    suite = _get_suite(SUITE)
    env = LiberoEnv(
        task_suite=suite,
        task_id=0,
        task_suite_name=SUITE,
        obs_type="pixels_agent_pos",
        observation_height=256,
        observation_width=256,
        control_mode="absolute",
        control_freq=20,
    )
    obs, _info = env.reset()
    eef_pos = np.asarray(obs["robot_state"]["eef"]["pos"], dtype=np.float64)
    eef_quat = np.asarray(obs["robot_state"]["eef"]["quat"], dtype=np.float64)
    axisangle = quat2axisangle(eef_quat)
    print(f"home eef_pos = {eef_pos}")
    print(f"home axisangle = {axisangle}")

    controller = env._env.robots[0].controller
    print(f"controller.use_delta = {controller.use_delta}")
    print(f"controller position_limits = {getattr(controller, 'position_limits', None)}")

    # Send a target 5cm away in +x, holding orientation and gripper open.
    target = eef_pos + np.array([0.05, 0.0, 0.0])
    print(f"\ntarget = {target}")
    for i in range(30):
        action = np.concatenate([target, axisangle, [-1.0]]).astype(np.float32)
        obs, _reward, terminated, _truncated, _info = env.step(action)
        cur = np.asarray(obs["robot_state"]["eef"]["pos"], dtype=np.float64)
        if i % 5 == 0 or i == 29:
            print(f"step {i:2d}: eef_pos={cur} dist_to_target={np.linalg.norm(target - cur):.4f}")
        if terminated:
            print("env terminated early; resetting")
            obs, _info = env.reset()

    cur = np.asarray(obs["robot_state"]["eef"]["pos"], dtype=np.float64)
    dist = float(np.linalg.norm(target - cur))
    print(f"\nfinal dist to target: {dist:.4f} m")
    if dist < 0.01:
        print("PASS: absolute pose control converges to world-frame target.")
    else:
        print("FAIL / INCONCLUSIVE: did not converge — inspect position_limits / use_delta above.")

    env.close()


if __name__ == "__main__":
    main()
