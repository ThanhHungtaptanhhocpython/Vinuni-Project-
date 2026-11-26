"""Single-slot frame buffer, lock-protected: written by the worker thread
(controller) and read by the main thread (cv2.imshow, which must run on the
main thread) and by MCP request threads (MJPEG stream)."""

from __future__ import annotations

import base64
import json
import threading
import time

import cv2
import numpy as np


def encode_jpeg_b64(frame: np.ndarray, quality: int = 70, resize: int | None = 256) -> str:
    img = frame
    if resize is not None and (img.shape[0] != resize or img.shape[1] != resize):
        img = cv2.resize(img, (resize, resize))
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def encode_frames_b64(frames: dict[str, np.ndarray]) -> dict[str, str]:
    return {name: encode_jpeg_b64(frame) for name, frame in frames.items()}


class Viewer:
    def __init__(
        self, window_name: str = "MVP VLA - LIBERO", camera_ids: tuple[str, ...] = ("agentview", "wrist")
    ):
        self.window_name = window_name
        self._camera_ids = camera_ids
        self._lock = threading.Lock()
        self._frames: dict[str, np.ndarray] | None = None
        self._overlay = ""
        self._servo_lock = threading.Lock()
        self._servo: dict | None = None

    def set_camera_ids(self, camera_ids: tuple[str, ...]) -> None:
        with self._lock:
            self._camera_ids = camera_ids

    def camera_ids(self) -> tuple[str, ...]:
        with self._lock:
            return self._camera_ids

    def publish(self, frames: dict[str, np.ndarray]) -> None:
        with self._lock:
            self._frames = frames

    def publish_servo(self, servo_state: dict) -> None:
        with self._servo_lock:
            self._servo = {**servo_state, "ts": time.time()}

    def latest_servo(self) -> dict | None:
        with self._servo_lock:
            return dict(self._servo) if self._servo is not None else None

    def set_overlay(self, text: str) -> None:
        with self._lock:
            self._overlay = text

    def latest_frames(self) -> dict[str, np.ndarray] | None:
        with self._lock:
            return dict(self._frames) if self._frames is not None else None

    @staticmethod
    def _resolve_camera_names(
        camera: str | None, available: dict[str, np.ndarray], default_ids: tuple[str, ...]
    ) -> list[str]:
        """Map UI names to frame keys. ``top`` aliases sim ``agentview``."""
        key = (camera or "both").strip().lower()
        if key in ("", "both", "all"):
            ordered = [name for name in default_ids if name in available]
            return ordered or list(available.keys())

        aliases = {
            "top": ("top", "agentview"),
            "agentview": ("agentview", "top"),
            "wrist": ("wrist",),
        }.get(key, (key,))
        for name in aliases:
            if name in available:
                return [name]
        return []

    def _latest_bgr(self, camera: str | None = None):
        with self._lock:
            frames = self._frames
            overlay = self._overlay
            camera_ids = self._camera_ids
        if frames is None:
            return None
        ordered_names = self._resolve_camera_names(camera, frames, camera_ids)
        ordered = [frames[name] for name in ordered_names]
        if not ordered:
            return None
        if len(ordered) == 1:
            stacked = ordered[0]
        else:
            target_h = min(frame.shape[0] for frame in ordered)
            normalized = []
            for frame in ordered:
                if frame.shape[0] == target_h:
                    normalized.append(frame)
                    continue
                scale = target_h / frame.shape[0]
                width = max(1, int(round(frame.shape[1] * scale)))
                normalized.append(cv2.resize(frame, (width, target_h)))
            stacked = np.hstack(normalized)
        bgr = cv2.cvtColor(stacked, cv2.COLOR_RGB2BGR)
        if overlay:
            cv2.putText(bgr, overlay, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        return bgr

    def run_forever(self, poll_hz: float = 30.0) -> None:
        """Must run on the main thread: cv2.imshow does not work correctly elsewhere."""
        period = 1.0 / poll_hz
        while True:
            frame = self._latest_bgr()
            if frame is not None:
                cv2.imshow(self.window_name, frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            time.sleep(period)

    def mjpeg_generator(self, fps: float = 15.0, camera: str | None = None):
        period = 1.0 / fps
        while True:
            frame = self._latest_bgr(camera=camera)
            if frame is not None:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n")
            time.sleep(period)

    def servo_sse_generator(self, hz: float = 10.0):
        period = 1.0 / hz
        last_ts = None
        while True:
            state = self.latest_servo()
            if state is not None and state["ts"] != last_ts:
                last_ts = state["ts"]
                yield f"data: {json.dumps(state)}\n\n"
            time.sleep(period)

