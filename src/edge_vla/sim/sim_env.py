"""LIBERO env for Edge VLA: disables auto-reset (the controller needs the
scene to persist across the whole K-step loop) and provides the helpers
(frames, mj_state, detections, switch_task) used by controller/safety/verifier.

EGL constraint: the thread that creates the EGL context must be the one that
steps it. `SimHandle` does not spawn its own thread — controller.py is
responsible for running all `step()`/`reset()`/`switch_task()` calls on a
single worker thread.
"""

from __future__ import annotations

import numpy as np
from robosuite.utils.transform_utils import quat2axisangle

from .. import config
from ..scripts.prepare_libero_runtime import ensure_libero_config

# hf-libero prompts on stdin if ~/.libero/config.yaml is missing — fatal in
# Docker/Cloud Run. Write a default config before importing libero.
ensure_libero_config()

from lerobot.envs.libero import LiberoEnv, _get_suite  # noqa: E402


class MvpLiberoEnv(LiberoEnv):
    """LiberoEnv that does not auto-`reset()` on termination.

    The base implementation (`lerobot/envs/libero.py:step`) resets immediately
    on `terminated`, which fits an episode-based eval loop. Here the agent
    needs to observe the scene right after `take_action` stops (whether it
    succeeded or not) to decide on go_home/retry/replan — an auto-reset would
    teleport the scene and break that flow.
    """

    def step(self, action: np.ndarray):
        self._ensure_env()
        assert self._env is not None
        if action.ndim != 1:
            raise ValueError(
                f"Expected action to be 1-D (shape (action_dim,)), "
                f"but got shape {action.shape} with ndim={action.ndim}"
            )
        raw_obs, reward, done, info = self._env.step(action)

        is_success = self._env.check_success()
        terminated = done or is_success
        info.update(
            {
                "task": self.task,
                "task_id": self.task_id,
                "done": done,
                "is_success": is_success,
            }
        )
        observation = self._format_raw_obs(raw_obs)
        truncated = False
        return observation, reward, terminated, truncated, info


_PREFIX = "pick_up_the_"
_SUFFIX = "_and_place_it_in_the_basket"


class SimHandle:
    """A single LIBERO env plus the state controller/safety/verifier need."""

    def __init__(self, control_mode: str = config.CONTROL_MODE):
        self.control_mode = control_mode
        self.suite = _get_suite(config.SUITE)
        self.tasks = [self.suite.get_task(i) for i in range(len(self.suite.tasks))]
        self.task_id: int = 0
        self.env: MvpLiberoEnv | None = None
        self.home_eef_pos: np.ndarray | None = None
        self.home_eef_axisangle: np.ndarray | None = None
        self.last_obs: dict | None = None
        self._build(0)

    # -- lifecycle --------------------------------------------------------
    def _build(self, task_id: int) -> dict:
        if self.env is not None:
            self.env.close()
        self.env = MvpLiberoEnv(
            task_suite=self.suite,
            task_id=task_id,
            task_suite_name=config.SUITE,
            obs_type="pixels_agent_pos",
            observation_height=config.OBS_HEIGHT,
            observation_width=config.OBS_WIDTH,
            control_mode=self.control_mode,
            control_freq=config.CONTROL_FREQ,
        )
        self.task_id = task_id
        obs, _info = self.env.reset()
        self.last_obs = obs
        self.home_eef_pos = np.asarray(obs["robot_state"]["eef"]["pos"], dtype=np.float64).copy()
        self.home_eef_axisangle = quat2axisangle(
            np.asarray(obs["robot_state"]["eef"]["quat"], dtype=np.float64)
        ).copy()
        return obs

    def switch_task(self, task_id: int) -> dict:
        return self._build(task_id)

    def reset_episode(self) -> dict:
        return self._build(self.task_id)

    def step(self, action: np.ndarray):
        obs, reward, terminated, truncated, info = self.env.step(action)
        self.last_obs = obs
        return obs, reward, terminated, truncated, info

    # -- task text <-> task_id ---------------------------------------------
    def language_list(self) -> list[dict]:
        return [{"task_id": i, "language": t.language} for i, t in enumerate(self.tasks)]

    def target_object_key(self, task_id: int | None = None) -> str:
        name = self.tasks[self.task_id if task_id is None else task_id].name
        obj = name
        if obj.startswith(_PREFIX):
            obj = obj[len(_PREFIX) :]
        if obj.endswith(_SUFFIX):
            obj = obj[: -len(_SUFFIX)]
        return obj

    def resolve_task_id(self, subgoal: str) -> int:
        """Fuzzy-match an English subgoal description against each task.language."""
        text = subgoal.lower()
        best_id, best_score = self.task_id, -1
        for i in range(len(self.tasks)):
            obj_words = self.target_object_key(i).replace("_", " ").split()
            score = sum(1 for w in obj_words if w in text)
            if any(w in text for w in obj_words) and score > best_score:
                best_id, best_score = i, score
        if best_score <= 0:
            # fallback: match against the full task.language sentence
            for i, t in enumerate(self.tasks):
                if t.language.lower() in text or text in t.language.lower():
                    return i
        return best_id

    # -- observation helpers ------------------------------------------------
    def frames(self) -> dict[str, np.ndarray]:
        """agentview + wrist, uint8, flipped H/W to match LiberoEnv.render()."""
        raw_obs = self.env._env.env._get_observations()
        agent = raw_obs["agentview_image"][::-1, ::-1]
        wrist = raw_obs["robot0_eye_in_hand_image"][::-1, ::-1]
        return {"agentview": np.ascontiguousarray(agent), "wrist": np.ascontiguousarray(wrist)}

    def mj_state(self) -> dict:
        """Used by safety.check() — reads the sim directly, does not re-render."""
        assert self.last_obs is not None
        rs = self.last_obs["robot_state"]
        sim = self.env._env.sim
        return {
            "eef_pos": np.asarray(rs["eef"]["pos"], dtype=np.float64),
            "gripper_qpos": np.asarray(rs["gripper"]["qpos"], dtype=np.float64),
            "joint_pos": np.asarray(rs["joints"]["pos"], dtype=np.float64),
            "joint_vel": np.asarray(rs["joints"]["vel"], dtype=np.float64),
            "actuator_force": np.asarray(sim.data.actuator_force, dtype=np.float64),
            "jnt_range": np.asarray(sim.model.jnt_range[:7], dtype=np.float64),
        }

    def servo_state(self, step: int | None = None) -> dict:
        """JSON-serializable per-joint servo state, built from mj_state(), for
        external streaming (viewer.publish_servo / MCP get_servo_state /
        /servo-stream). Units are MuJoCo-native: rad, rad/s, N*m."""
        st = self.mj_state()
        n = st["joint_pos"].shape[0]
        joints = [
            {
                "id": i,
                "name": f"joint_{i}",
                "pos": round(float(st["joint_pos"][i]), 6),
                "vel": round(float(st["joint_vel"][i]), 6),
                "actuator_force": round(float(st["actuator_force"][i]), 6),
                "range": [round(float(st["jnt_range"][i][0]), 6), round(float(st["jnt_range"][i][1]), 6)],
            }
            for i in range(n)
        ]
        return {
            "step": step,
            "joints": joints,
            "gripper_qpos": [round(float(x), 6) for x in st["gripper_qpos"]],
            "eef_pos": [round(float(x), 6) for x in st["eef_pos"]],
        }

    def detections(self) -> dict:
        """Target object + basket coordinates, used by the agent when replanning."""
        raw_obs = self.env._env.env._get_observations()
        obj = self.target_object_key()

        def _get(key: str):
            v = raw_obs.get(key)
            return None if v is None else [round(float(x), 4) for x in np.asarray(v).reshape(-1)]

        return {
            "object": obj,
            "object_pos": _get(f"{obj}_1_pos"),
            "basket_pos": _get("basket_1_pos"),
            "object_to_eef_pos": _get(f"{obj}_1_to_robot0_eef_pos"),
        }

    def check_success(self) -> bool:
        return bool(self.env._env.check_success())

    def close(self):
        if self.env is not None:
            self.env.close()
