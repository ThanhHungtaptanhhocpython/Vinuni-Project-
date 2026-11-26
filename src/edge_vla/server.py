"""MCP server (FastMCP, streamable-http transport) for Edge VLA.

`uvicorn`/MCP runs in a background thread; the main thread owns the
`cv2.imshow` window (required by OpenCV) via `viewer.run_forever()`.
`GET /mjpeg` and `GET /servo-stream` are registered as custom routes directly
on the FastMCP app being served (rather than a separate FastAPI app) to avoid
managing nested lifespan mounts.

On Darwin, the main thread is needed for something else instead: MuJoCo's
CGL renderer (see `core/controller.py`'s docstring) — and `cv2.imshow` has
that same main-thread-only rule on macOS, so the two can't share the one real
main thread. `main()` gives it to the sim there and skips the local
`cv2.imshow` debug window entirely (MCP server moves to a background thread).
This only drops the local debug window, not functionality: `GET /mjpeg` and
`GET /servo-stream` still work and are how the frontend actually consumes
frames — `cv2.imshow` was always just a standalone dev convenience.
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import threading

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from . import config
from .cloud_registration import register_with_cloud_background
from .core.controller import make_controller
from .core.task_store import TaskStore
from .robot_config import resolve_robot_mode
from .viewer import Viewer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("mvp-vla-edge", host="0.0.0.0", port=config.MCP_PORT)

viewer = Viewer()
store = TaskStore()
robot_mode = resolve_robot_mode()
controller = make_controller(robot_mode, viewer, store)


@mcp.tool()
def get_robot_state() -> dict:
    """SYNC, non-blocking. Returns the edge's current state (busy, retry_count,
    backend_mode). In sim mode it also returns the selected LIBERO task and
    available LIBERO tasks. In real mode it returns live joint positions and
    the configured rollout task. Call this before planning."""
    return controller.get_robot_state()


@mcp.tool()
def capture() -> dict:
    """SYNC, non-blocking. Returns the latest camera images (base64 JPEG) from
    the published frame buffer — never re-renders. In real mode, use these
    images to judge outcome because check_success is unsupported."""
    return controller.capture()


@mcp.tool()
def get_servo_state() -> dict:
    """SYNC, non-blocking. Returns the latest published per-joint servo state
    from the active backend — never re-reads the robot/sim directly. For
    continuous streaming use the GET /servo-stream SSE endpoint instead of
    polling this tool."""
    return controller.get_servo_state()


@mcp.tool()
def take_action(subgoal: str, k: int, libero_task_id: int | None = None) -> dict:
    """ASYNC — returns immediately (~10ms), does not wait for the robot to finish.

    Runs the robot for up to k control ticks (hard-clamped to [1, 10000]) toward
    `subgoal`; in real mode each tick is at the configured rollout fps. If the robot is already busy, returns {status: ROBOT_BUSY} and
    does not spawn a new task (check the current task's status first). After
    calling, poll check_status(task_id, wait_s) with an increasing wait_s
    (e.g. 3 -> 5 -> 8) until the status is no longer RUNNING — do not call
    take_action again before the previous one reaches
    DONE/FAILED/SAFETY_STOP/TIMEOUT/ABORTED.

    Args:
        subgoal: natural-language (English) description. Sim mode uses it to
            fuzzy-match one LIBERO task when libero_task_id is not given; real
            mode uses the task configured in robot_config.yaml.
        k: max control ticks for this run. Default to 500 on the first
            take_action call for a subgoal. If that run exhausts its k ticks
            without succeeding and you retry the same subgoal, increase k by
            200 over the previous value each retry (500 -> 700 -> 900 -> ...),
            up to the [1, 10000] hard clamp.
        libero_task_id: sim-only task selector; ignored in real mode.
    """
    return controller.take_action(subgoal, k, libero_task_id)


@mcp.tool()
async def check_status(task_id: str, wait_s: float) -> dict:
    """ASYNC, genuinely blocking for up to wait_s (clamped to [1, 15]s), or
    returns early once the task finishes. This is the only way to learn
    whether take_action has completed — allows at most 8 polls per task_id;
    the 9th automatically aborts and returns TIMEOUT.

    Returned statuses and how to handle them:
      - RUNNING: not finished, poll again with a larger wait_s.
      - DONE + success=true: sim success; call go_home() then report the result.
      - DONE + success=false: inspect the images in the response, then decide
        whether to retry (take_action again) or stop.
      - DONE + success_supported=false: real mode has no oracle; inspect images
        and servo state before deciding whether another action is needed.
      - FAILED_MAX_RETRY: the edge already opened the gripper, backed off, and
        went home (safe_at_home=true). Ask the user — do not auto-retry.
      - SAFETY_STOP: emergency stop mid-run. Report to the user immediately —
        retrying is forbidden.
      - TIMEOUT / ABORTED: stopped automatically due to poll exhaustion or abort.
    """
    return await controller.check_status(task_id, wait_s)


@mcp.tool()
def check_success() -> dict:
    """SYNC, briefly blocking (~ms). Re-checks the success predicate
    in sim mode (ground-truth BDDL) plus object/basket coordinates. In real
    mode returns {supported: false, reason, backend_mode}; judge via images."""
    return controller.check_success()


@mcp.tool()
def go_home() -> dict:
    """SYNC, blocking. Sim moves to home_eef_pos using relative actions. Real
    mode interpolates back to the initial joint position captured at connect."""
    return controller.go_home()


@mcp.tool()
def abort(task_id: str) -> dict:
    """SYNC, non-blocking. Sets the abort flag — the running background task
    exits within at most 1 step."""
    return controller.abort(task_id)


@mcp.tool()
def reset_episode() -> dict:
    """SYNC, blocking. Sim resets the current episode. Real mode only clears
    the policy action queue; it does not move hardware."""
    return controller.reset_episode()


@mcp.custom_route("/mjpeg", methods=["GET"])
async def mjpeg(request: Request) -> StreamingResponse:
    # ?camera=both|top|wrist|agentview — single camera avoids UI-side cropping.
    camera = request.query_params.get("camera")
    return StreamingResponse(
        viewer.mjpeg_generator(camera=camera),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@mcp.custom_route("/servo-stream", methods=["GET"])
async def servo_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(viewer.servo_sse_generator(), media_type="text/event-stream")


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/webrtc/offer", methods=["POST"])
async def webrtc_offer(request: Request) -> JSONResponse:
    """FE's /api/webrtc/offer route handler proxies straight here (see that
    route's docstring for the wire contract: `{sdp, type}` in, `{sdp, type}`
    out, errors as `{"error": str}`)."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Body không phải JSON."}, status_code=400)

    try:
        answer = await handle_offer(viewer, payload)
    except SignalingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse(answer)


def _headless() -> bool:
    flag = os.environ.get("EDGE_VLA_HEADLESS", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return not os.environ.get("DISPLAY")


def main() -> None:
    # SIGTERM's default action kills the process without unwinding the stack,
    # so a `kill` from our run scripts would skip controller.close() below and
    # leave real servos energized at their last commanded position. Routing it
    # through the same path as Ctrl+C (KeyboardInterrupt) guarantees teardown
    # (and, in real mode, torque release) always runs before the process exits.
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    threading.Thread(
        target=register_with_cloud_background,
        kwargs={"robot_mode": robot_mode},
        daemon=True,
    ).start()

    try:
        headless = _headless()
        if platform.system() == "Darwin" and controller.backend_mode.startswith("sim"):
            def _serve_once_sim_ready() -> None:
                controller.wait_until_ready()
                mcp.run(transport="streamable-http")

            threading.Thread(target=_serve_once_sim_ready, daemon=True).start()
            logger.info(
                f"Edge MCP server listening on :{config.MCP_PORT} "
                "(streamable-http) + /mjpeg + /servo-stream "
                "(no local cv2 debug window on macOS — main thread owns MuJoCo/CGL instead)"
            )
            controller.run_forever()  # must run on the main thread (CGL requirement)
            return

        if headless:
            if controller.backend_mode.startswith("sim"):
                controller.wait_until_ready()
            logger.info(
                f"Edge MCP server listening on :{config.MCP_PORT} "
                "(streamable-http, headless) + /health + /mjpeg + /servo-stream"
            )
            mcp.run(transport="streamable-http")
            return

        server_thread = threading.Thread(
            target=mcp.run, kwargs={"transport": "streamable-http"}, daemon=True
        )
        server_thread.start()
        logger.info(
            f"Edge MCP server listening on :{config.MCP_PORT} "
            "(streamable-http) + /mjpeg + /servo-stream"
        )
        viewer.run_forever()  # must run on the main thread (cv2.imshow requirement)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down — releasing hardware (torque off in real mode)...")
        controller.close()


if __name__ == "__main__":
    main()
