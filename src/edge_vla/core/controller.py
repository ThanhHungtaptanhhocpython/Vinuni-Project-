"""Async orchestration, retry, and recovery logic for Edge VLA.

Sim mode keeps all LIBERO/MuJoCo calls on one worker thread because the GL
context must stay on the thread that created it. Real mode uses the same
single-owner worker model for the robot bus and cameras.
"""

from __future__ import annotations

import asyncio
import concurrent.futures as cf
import logging
import platform
import queue
import threading
import time
import uuid
from typing import Any

import numpy as np

from .. import config
from ..viewer import Viewer, encode_frames_b64
from .task_store import (
    ABORTED,
    DONE,
    FAILED_MAX_RETRY,
    ROBOT_BUSY,
    RUNNING,
    SAFETY_STOP,
    TIMEOUT,
    TaskStore,
)

logger = logging.getLogger(__name__)

_GRIPPER_OPEN = -1.0


class BaseController:
    backend_mode = "base"
    default_camera_ids: tuple[str, ...] = ()

    def __init__(self, viewer: Viewer, store: TaskStore):
        self.viewer = viewer
        self.store = store
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None

        if self._requires_main_thread_worker():
            self._worker: threading.Thread | None = None
        else:
            self._worker = threading.Thread(
                target=self._worker_loop, name=f"{self.backend_mode}-worker", daemon=True
            )
            self._worker.start()
            self._ready.wait()
            if self._startup_error is not None:
                raise RuntimeError(f"{self.backend_mode} backend startup failed") from self._startup_error

    def _requires_main_thread_worker(self) -> bool:
        return False

    def wait_until_ready(self) -> None:
        self._ready.wait()
        if self._startup_error is not None:
            raise RuntimeError(f"{self.backend_mode} backend startup failed") from self._startup_error

    def close(self) -> None:
        """Release hardware cleanly. Override where shutdown must reach real actuators."""

    def run_forever(self) -> None:
        self._worker_loop()

    def _startup(self) -> None:
        raise NotImplementedError

    def _worker_loop(self) -> None:
        try:
            self._startup()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            raise
        self.viewer.set_camera_ids(self.camera_ids())
        self.viewer.publish(self._frames())
        self.viewer.publish_servo(self._servo_state())
        self._ready.set()
        while True:
            fn, args, kwargs, fut = self._jobs.get()
            try:
                result = fn(*args, **kwargs)
                if fut is not None:
                    fut.set_result(result)
            except Exception as exc:  # noqa: BLE001
                if fut is not None:
                    fut.set_exception(exc)
                else:
                    logger.exception("Unhandled error in %s-worker job", self.backend_mode)

    def _submit(self, fn, *args, wait: bool = True, timeout: float | None = None, **kwargs):
        fut = cf.Future() if wait else None
        self._jobs.put((fn, args, kwargs, fut))
        if wait:
            return fut.result(timeout=timeout)
        return None

    def camera_ids(self) -> tuple[str, ...]:
        return self.default_camera_ids

    def get_robot_state(self) -> dict:
        return {
            "backend_mode": self.backend_mode,
            "current_task_id": self.store.current_task_id,
            "busy": self.store.busy,
            "retry_count": self.store.retry_count,
            **self._robot_state_extra(),
        }

    def _robot_state_extra(self) -> dict:
        return {}

    def capture(self) -> dict:
        frames = self.viewer.latest_frames()
        return {"images": encode_frames_b64(frames) if frames else {}, "backend_mode": self.backend_mode}

    def get_servo_state(self) -> dict:
        return self.viewer.latest_servo() or {}

    def take_action(self, subgoal: str, k: int, libero_task_id: int | None = None) -> dict:
        if self.store.busy:
            return {"status": ROBOT_BUSY, "current_task_id": self.store.current_task_id}

        k = int(np.clip(int(k), 1, config.MAX_K))
        resolved_task = self._resolve_task(subgoal, libero_task_id)

        new_task_id = str(uuid.uuid4())
        self.store.start_task(new_task_id, k)
        self._submit(self._run_task, new_task_id, resolved_task, k, wait=False)
        est_s = max(1, int(k)) / config.CONTROL_FREQ * 12 + 90
        return {
            "task_id": new_task_id,
            "status": RUNNING,
            "backend_mode": self.backend_mode,
            "estimated_duration_s": min(config.TASK_TIMEOUT_S - 60, est_s),
        }

    def _resolve_task(self, subgoal: str, libero_task_id: int | None) -> Any:
        return subgoal

    async def check_status(self, task_id: str, wait_s: float) -> dict:
        wait_s = float(np.clip(float(wait_s), *config.WAIT_CLAMP))

        self.store.poll_count += 1
        if self.store.poll_count > config.MAX_POLL:
            self.abort(task_id)
            self.store.update(task_id, status=TIMEOUT, reason="poll count exceeded MAX_POLL")
            return self.store.get(task_id) or {"status": TIMEOUT}

        elapsed = 0.0
        while elapsed < wait_s:
            task = self.store.get(task_id)
            if task is None or task["status"] != RUNNING:
                break
            await asyncio.sleep(0.2)
            elapsed += 0.2

        return self.store.get(task_id) or {"status": "UNKNOWN", "task_id": task_id}

    def check_success(self) -> dict:
        return self._submit(self._check_success_job)

    def go_home(self) -> dict:
        return self._submit(self._go_home_job)

    def abort(self, task_id: str | None = None) -> dict:
        self.store.abort_flag = True
        return {"status": "ABORTING", "backend_mode": self.backend_mode}

    def reset_episode(self) -> dict:
        self.store.abort_flag = True
        time.sleep(0.15)
        result = self._submit(self._reset_episode_job)
        self.store.retry_count = 0
        self.store.busy = False
        self.store.abort_flag = False
        return result

    def _check_success_job(self) -> dict:
        return {"supported": False, "reason": "check_success is not implemented for this backend"}

    def _go_home_job(self) -> dict:
        raise NotImplementedError

    def _reset_episode_job(self) -> dict:
        raise NotImplementedError

    def _run_task(self, task_id: str, resolved_task: Any, k: int) -> None:
        raise NotImplementedError

    def _frames(self) -> dict[str, np.ndarray]:
        raise NotImplementedError

    def _servo_state(self, step: int | None = None) -> dict:
        raise NotImplementedError


class SimController(BaseController):
    backend_mode = "sim"
    default_camera_ids = ("agentview", "wrist")

    def __init__(self, viewer: Viewer, store: TaskStore):
        self.sim = None
        self.policy = None
        self.safety = None
        self.verifier = None
        super().__init__(viewer, store)

    def _requires_main_thread_worker(self) -> bool:
        return platform.system() == "Darwin"

    def _startup(self) -> None:
        from ..sim import safety, verifier
        from ..sim.policy_runner import PolicyRunner
        from ..sim.sim_env import SimHandle

        self.safety = safety
        self.verifier = verifier
        self.sim = SimHandle()
        self.policy = PolicyRunner()

    def _robot_state_extra(self) -> dict:
        assert self.sim is not None
        return {
            "current_libero_task_id": self.sim.task_id,
            "libero_tasks": self.sim.language_list(),
        }

    def _resolve_task(self, subgoal: str, libero_task_id: int | None) -> int:
        assert self.sim is not None
        return int(libero_task_id) if libero_task_id is not None else self.sim.resolve_task_id(subgoal)

    def _check_success_job(self) -> dict:
        assert self.sim is not None and self.verifier is not None
        return {
            "success": self.verifier.check_success(self.sim),
            "detections": self.verifier.detections(self.sim),
            "backend_mode": self.backend_mode,
        }

    def _go_home_job(self) -> dict:
        assert self.sim is not None
        self._go_home()
        self.viewer.publish(self._frames())
        self.viewer.publish_servo(self._servo_state())
        return {
            "status": "DONE",
            "eef_pos": list(self.sim.last_obs["robot_state"]["eef"]["pos"]),
            "backend_mode": self.backend_mode,
        }

    def _reset_episode_job(self) -> dict:
        assert self.sim is not None
        self.sim.reset_episode()
        self.viewer.publish(self._frames())
        self.viewer.publish_servo(self._servo_state())
        return {"status": "DONE", "backend_mode": self.backend_mode}

    def _run_task(self, task_id: str, libero_task_id: int, k: int) -> None:
        assert self.sim is not None and self.policy is not None
        assert self.safety is not None and self.verifier is not None

        if libero_task_id != self.sim.task_id:
            self.sim.switch_task(libero_task_id)
            self.store.retry_count = 0

        start = time.time()
        task_text = self.sim.tasks[self.sim.task_id].language
        action = None
        i = 0
        try:
            self.policy.reset()
            for i in range(k):
                if self.store.abort_flag:
                    task_now = self.store.get(task_id)
                    if task_now is not None and task_now["status"] == RUNNING:
                        self.store.update(task_id, status=ABORTED, step=i)
                    return
                if time.time() - start > config.TASK_TIMEOUT_S:
                    self.store.update(task_id, status=TIMEOUT, step=i)
                    return

                ok, reason = self.safety.check(self.sim.mj_state())
                if not ok:
                    self._emergency_stop()
                    self.store.update(
                        task_id,
                        status=SAFETY_STOP,
                        reason=reason,
                        step=i,
                        images=encode_frames_b64(self._frames()),
                        safe_at_home=False,
                    )
                    return

                action = self.policy.act(self.sim.last_obs, task_text)
                self.sim.step(action)
                self.viewer.publish(self._frames())
                self.viewer.publish_servo(self._servo_state(step=i + 1))
                self.viewer.set_overlay(f"{RUNNING} | {i + 1}/{k} | retry={self.store.retry_count}")
                self.store.update(task_id, step=i + 1)

            self._settle_after_steps(action)

            success = self.verifier.check_success(self.sim)
            images = encode_frames_b64(self._frames())
            detections = self.verifier.detections(self.sim)

            if success:
                self.store.retry_count = 0
                self.store.update(task_id, status=DONE, success=True, images=images, detections=detections)
                self.viewer.set_overlay(f"{DONE} success | retry={self.store.retry_count}")
                return

            self.store.retry_count += 1
            if self.store.retry_count >= config.MAX_RETRY:
                self._recovery()
                self.store.update(
                    task_id,
                    status=FAILED_MAX_RETRY,
                    success=False,
                    images=images,
                    detections=detections,
                    safe_at_home=True,
                )
                self.viewer.set_overlay(f"{FAILED_MAX_RETRY} | retry={self.store.retry_count}")
            else:
                self.store.update(task_id, status=DONE, success=False, images=images, detections=detections)
                self.viewer.set_overlay(f"{DONE} fail | retry={self.store.retry_count}")
        finally:
            self.store.finish_task()

    def _settle_after_steps(self, action: np.ndarray | None) -> None:
        """Let physics settle briefly after the k-step loop. Relative-mode sim
        sends a zero delta; absolute-mode backends must hold the current pose."""
        assert self.sim is not None
        last_gripper = float(action[-1]) if action is not None else _GRIPPER_OPEN
        noop = np.array([0, 0, 0, 0, 0, 0, last_gripper], dtype=np.float32)
        for _ in range(5):
            self.sim.step(noop)

    def _frames(self) -> dict[str, np.ndarray]:
        assert self.sim is not None
        return self.sim.frames()

    def _servo_state(self, step: int | None = None) -> dict:
        assert self.sim is not None
        return self.sim.servo_state(step=step)

    def _emergency_stop(self) -> None:
        self.store.abort_flag = True
        self.viewer.set_overlay(f"{SAFETY_STOP}")

    def _recovery(self) -> None:
        self._open_gripper()
        self._lift_z(0.10)
        self._go_home()

    def _open_gripper(self, steps: int = 10) -> None:
        assert self.sim is not None
        action = np.array([0, 0, 0, 0, 0, 0, _GRIPPER_OPEN], dtype=np.float32)
        for _ in range(steps):
            self.sim.step(action)

    def _lift_z(self, dz: float, steps: int = 15) -> None:
        assert self.sim is not None
        action = np.array([0, 0, float(np.clip(dz, -1, 1)), 0, 0, 0, _GRIPPER_OPEN], dtype=np.float32)
        for _ in range(steps):
            self.sim.step(action)

    def _go_home(self, max_steps: int = 40) -> None:
        assert self.sim is not None
        for _ in range(max_steps):
            eef = np.asarray(self.sim.last_obs["robot_state"]["eef"]["pos"], dtype=np.float64)
            delta = self.sim.home_eef_pos - eef
            dist = float(np.linalg.norm(delta))
            if dist < 0.01:
                break
            direction = delta / dist
            step = np.clip(direction * min(dist, 1.0) * 3.0, -1.0, 1.0)
            action = np.array([step[0], step[1], step[2], 0, 0, 0, _GRIPPER_OPEN], dtype=np.float32)
            self.sim.step(action)


class SimIkController(SimController):
    """Sim backend chạy IK state machine thay VLA — numpy thuần, không torch."""

    backend_mode = "sim_ik"

    def _startup(self) -> None:
        from ..sim import safety, verifier
        from ..sim.ik_policy import IkPolicyRunner
        from ..sim.sim_env import SimHandle

        self.safety, self.verifier = safety, verifier
        self.sim = SimHandle(control_mode=config.IK_CONTROL_MODE)
        self.policy = IkPolicyRunner(self.sim)

    def _robot_state_extra(self) -> dict:
        extra = super()._robot_state_extra()
        extra["policy_backend"] = "ik"
        return extra

    def _settle_after_steps(self, action: np.ndarray | None) -> None:
        from ..sim.ik_policy import absolute_pose_action

        assert self.sim is not None and self.sim.home_eef_axisangle is not None
        eef = np.asarray(self.sim.last_obs["robot_state"]["eef"]["pos"], dtype=np.float64)
        gripper = float(action[-1]) if action is not None else _GRIPPER_OPEN
        hold = absolute_pose_action(eef, eef, self.sim.home_eef_axisangle, gripper)
        for _ in range(5):
            self.sim.step(hold)

    def _open_gripper(self, steps: int = 10) -> None:
        from ..sim.ik_policy import absolute_pose_action

        assert self.sim is not None and self.sim.home_eef_axisangle is not None
        eef = np.asarray(self.sim.last_obs["robot_state"]["eef"]["pos"], dtype=np.float64)
        action = absolute_pose_action(eef, eef, self.sim.home_eef_axisangle, _GRIPPER_OPEN)
        for _ in range(steps):
            self.sim.step(action)

    def _lift_z(self, dz: float, steps: int = 15) -> None:
        from ..sim.ik_policy import absolute_pose_action

        assert self.sim is not None and self.sim.home_eef_axisangle is not None
        eef = np.asarray(self.sim.last_obs["robot_state"]["eef"]["pos"], dtype=np.float64)
        target = eef + np.array([0.0, 0.0, dz])
        for _ in range(steps):
            eef = np.asarray(self.sim.last_obs["robot_state"]["eef"]["pos"], dtype=np.float64)
            action = absolute_pose_action(eef, target, self.sim.home_eef_axisangle, _GRIPPER_OPEN)
            self.sim.step(action)

    def _go_home(self, max_steps: int = 40) -> None:
        from ..sim.ik_policy import absolute_pose_action

        assert self.sim is not None and self.sim.home_eef_axisangle is not None
        target = self.sim.home_eef_pos
        assert target is not None
        for _ in range(max_steps):
            eef = np.asarray(self.sim.last_obs["robot_state"]["eef"]["pos"], dtype=np.float64)
            if float(np.linalg.norm(target - eef)) < config.IK_POS_TOL:
                break
            action = absolute_pose_action(eef, target, self.sim.home_eef_axisangle, _GRIPPER_OPEN)
            self.sim.step(action)


class RealController(BaseController):
    backend_mode = "real"
    default_camera_ids = ("top", "wrist")

    def __init__(self, viewer: Viewer, store: TaskStore, rollout_config_path: str | None = None):
        self.rollout_config_path = rollout_config_path
        self.real = None
        super().__init__(viewer, store)

    def _startup(self) -> None:
        from ..real.rollout_backend import RealHandle
        from ..robot_config import load_rollout_config

        cfg = load_rollout_config(self.rollout_config_path)
        self.real = RealHandle(cfg)

    def camera_ids(self) -> tuple[str, ...]:
        assert self.real is not None
        return self.real.camera_ids or self.default_camera_ids

    def get_robot_state(self) -> dict:
        if not self.store.busy:
            self._publish_fresh_state()
        return super().get_robot_state()

    def capture(self) -> dict:
        if not self.store.busy:
            self._submit(self._publish_frames_job)
        return super().capture()

    def get_servo_state(self) -> dict:
        if not self.store.busy:
            self._submit(self._publish_servo_job)
        return super().get_servo_state()

    def _robot_state_extra(self) -> dict:
        return {
            "task": self.real.task_text if self.real is not None else "",
            "joints": (self.viewer.latest_servo() or {}).get("joints", []),
        }

    def _resolve_task(self, subgoal: str, libero_task_id: int | None) -> str:
        assert self.real is not None
        return self.real.task_text or subgoal

    def _check_success_job(self) -> dict:
        return {
            "supported": False,
            "reason": "Real SO-101 mode has no oracle success predicate; inspect camera images.",
            "backend_mode": self.backend_mode,
        }

    def _go_home_job(self) -> dict:
        assert self.real is not None
        self.real.go_home()
        self.viewer.publish(self._frames())
        self.viewer.publish_servo(self._servo_state())
        return {"status": "DONE", "backend_mode": self.backend_mode}

    def _reset_episode_job(self) -> dict:
        assert self.real is not None
        self.real.reset()
        self.viewer.publish(self._frames())
        self.viewer.publish_servo(self._servo_state())
        return {
            "status": "DONE",
            "backend_mode": self.backend_mode,
            "note": "Cleared policy action queue; real hardware pose was not reset.",
        }

    def _run_task(self, task_id: str, task_text: str, k: int) -> None:
        assert self.real is not None

        start = time.time()
        completed = 0
        try:
            self.real.reset()

            def should_abort() -> bool:
                return self.store.abort_flag or (time.time() - start > config.TASK_TIMEOUT_S)

            def on_obs(obs: dict, step: int) -> None:
                # Publishes each control-tick observation as it's fetched, so the camera
                # feed stays live during the run instead of freezing until the whole task
                # (k steps) completes — see rollout_backend.StepStrategy.run_steps.
                frames = self.real.frames_from_obs(obs)
                if frames:
                    self.viewer.publish(frames)
                self.viewer.publish_servo(self.real.servo_state_from_obs(obs, step=step))
                self.store.update(task_id, step=step)

            completed = self.real.step(k, should_abort, on_obs=on_obs)
            self.viewer.publish(self._frames())
            self.viewer.publish_servo(self._servo_state(step=completed))

            task_now = self.store.get(task_id)
            if task_now is not None and task_now["status"] == RUNNING:
                if self.store.abort_flag:
                    self.store.update(task_id, status=ABORTED, step=completed)
                    return
                if time.time() - start > config.TASK_TIMEOUT_S:
                    self.store.update(task_id, status=TIMEOUT, step=completed)
                    return

                images = encode_frames_b64(self._frames())
                self.store.update(
                    task_id,
                    status=DONE,
                    step=completed,
                    success=None,
                    success_supported=False,
                    images=images,
                    backend_mode=self.backend_mode,
                    task=task_text,
                )
                self.viewer.set_overlay(f"{DONE} | {completed}/{k} | real")
        except Exception as exc:  # noqa: BLE001
            self.store.update(task_id, status=SAFETY_STOP, reason=str(exc), step=completed)
            self.viewer.set_overlay(f"{SAFETY_STOP} | real")
            raise
        finally:
            self.store.finish_task()

    def _publish_fresh_state(self) -> None:
        self._submit(self._publish_frames_and_servo_job)

    def _publish_frames_and_servo_job(self) -> None:
        self.viewer.publish(self._frames())
        self.viewer.publish_servo(self._servo_state())

    def _publish_frames_job(self) -> None:
        self.viewer.publish(self._frames())

    def _publish_servo_job(self) -> None:
        self.viewer.publish_servo(self._servo_state())

    def _frames(self) -> dict[str, np.ndarray]:
        assert self.real is not None
        return self.real.frames()

    def _servo_state(self, step: int | None = None) -> dict:
        assert self.real is not None
        return self.real.servo_state(step=step)

    def close(self) -> None:
        """Disconnect the robot bus so servos go limp instead of holding their
        last commanded position after the process exits."""
        if self.real is not None:
            self._submit(self.real.close)


Controller = SimController


def make_controller(mode: str, viewer: Viewer, store: TaskStore) -> BaseController:
    if mode == "sim":
        if config.POLICY_BACKEND == "ik":
            return SimIkController(viewer, store)
        return SimController(viewer, store)
    if mode == "real":
        return RealController(viewer, store)
    raise ValueError(f"Unsupported robot mode: {mode!r}")
