"""Thin SO-101 rollout backend built on lerobot's rollout primitives."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np


class _StepStrategy:
    def __init__(self, config):
        from lerobot.rollout.strategies.base import BaseStrategy

        class StepStrategy(BaseStrategy):
            def run_steps(
                self,
                ctx,
                k: int,
                should_abort: Callable[[], bool],
                on_obs: Callable[[dict, int], None] | None = None,
            ) -> int:
                from lerobot.rollout.strategies.core import send_next_action
                from lerobot.utils.robot_utils import precise_sleep

                engine = self._engine
                cfg = ctx.runtime.cfg
                robot = ctx.hardware.robot_wrapper
                interpolator = self._interpolator
                control_interval = interpolator.get_control_interval(cfg.fps)

                engine.resume()
                completed = 0
                for _ in range(k):
                    if ctx.runtime.shutdown_event.is_set() or should_abort():
                        break

                    loop_start = time.perf_counter()
                    obs = robot.get_observation()
                    # Publish the frame we just fetched for this control tick, on this
                    # same worker thread — not a separate poller — so the camera stream
                    # tracks real robot motion step-by-step instead of freezing for the
                    # whole task like it did when only the final frame got published.
                    if on_obs is not None:
                        on_obs(obs, completed + 1)
                    obs_processed = self._process_observation_and_notify(ctx.processors, obs)

                    if self._handle_warmup(cfg.use_torch_compile, loop_start, control_interval):
                        completed += 1
                        continue

                    action_dict = send_next_action(obs_processed, obs, ctx, interpolator)
                    self._log_telemetry(obs_processed, action_dict, ctx.runtime)

                    dt = time.perf_counter() - loop_start
                    if (sleep_t := control_interval - dt) > 0:
                        precise_sleep(sleep_t)
                    completed += 1
                return completed

        self.impl = StepStrategy(config)


class RealHandle:
    def __init__(self, cfg):
        from lerobot.rollout.context import build_rollout_context

        self.cfg = cfg
        self.shutdown_event = threading.Event()
        self.ctx = build_rollout_context(cfg, self.shutdown_event)
        self.strategy = _StepStrategy(cfg.strategy).impl
        self.strategy.setup(self.ctx)

    @property
    def task_text(self) -> str:
        return self.cfg.task

    @property
    def camera_ids(self) -> tuple[str, ...]:
        cameras = getattr(self.cfg.robot, "cameras", None) or {}
        return tuple(cameras.keys())

    def step(
        self,
        k: int,
        should_abort: Callable[[], bool],
        on_obs: Callable[[dict[str, Any], int], None] | None = None,
    ) -> int:
        return self.strategy.run_steps(self.ctx, k, should_abort, on_obs)

    def reset(self) -> None:
        self.ctx.policy.inference.reset()
        if self.strategy._interpolator is not None:
            self.strategy._interpolator.reset()

    def _observation(self) -> dict[str, Any]:
        return self.ctx.hardware.robot_wrapper.get_observation()

    def frames_from_obs(self, obs: dict[str, Any]) -> dict[str, np.ndarray]:
        frames: dict[str, np.ndarray] = {}
        for camera_id in self.camera_ids:
            value = obs.get(camera_id)
            if value is None:
                continue
            frames[camera_id] = np.ascontiguousarray(np.asarray(value))
        return frames

    def frames(self) -> dict[str, np.ndarray]:
        return self.frames_from_obs(self._observation())

    def servo_state_from_obs(self, obs: dict[str, Any], step: int | None = None) -> dict:
        joints = []
        for idx, key in enumerate(sorted(k for k in obs if k.endswith(".pos"))):
            value = np.asarray(obs[key]).reshape(-1)
            pos = float(value[0]) if value.size else 0.0
            name = key.removeprefix("observation.state.").removesuffix(".pos")
            joints.append({"id": idx, "name": name, "pos": round(pos, 6)})
        return {"step": step, "joints": joints}

    def servo_state(self, step: int | None = None) -> dict:
        return self.servo_state_from_obs(self._observation(), step=step)

    def go_home(self) -> None:
        from lerobot.rollout.strategies.core import RolloutStrategy

        hw = self.ctx.hardware
        self._open_gripper_before_home(hw)
        RolloutStrategy._return_to_initial_position(hw)

    @staticmethod
    def _open_gripper_before_home(hw, duration_s: float = 1.0, fps: int = 50) -> None:
        """Mở kẹp hoàn toàn TRƯỚC khi cánh tay di chuyển về home.

        Nếu go_home nội suy toàn bộ khớp (bao gồm kẹp) cùng lúc, vật thể còn
        kẹt trong kẹp có thể bị kéo lê/rơi giữa chừng khi cánh tay di chuyển.
        Mở kẹp trước, giữ nguyên các khớp còn lại, đảm bảo vật được thả ra ở
        vị trí hiện tại trước khi cánh tay bắt đầu di chuyển.
        """
        from lerobot.utils.robot_utils import precise_sleep

        target = hw.initial_position
        if not target or "gripper.pos" not in target:
            return

        robot = hw.robot_wrapper
        current_obs = robot.get_observation()
        current_gripper = current_obs.get("gripper.pos")
        if current_gripper is None:
            return

        open_pos = target["gripper.pos"]
        steps = max(int(duration_s * fps), 1)
        for step in range(1, steps + 1):
            t = step / steps
            robot.send_action({"gripper.pos": current_gripper * (1 - t) + open_pos * t})
            precise_sleep(1 / fps)

    def close(self) -> None:
        self.shutdown_event.set()
        self.strategy.teardown(self.ctx)
