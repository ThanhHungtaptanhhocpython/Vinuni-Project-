"""Self-registration with the cloud-agent backend on boot.

The Edge is the active side: it knows its own id and what it can do, so it
calls the backend rather than waiting to be discovered. The JWT proves
possession of `EDGE_JWT_SECRET` (shared with cloud-agent, symmetric HS256) —
the backend never has to trust an unauthenticated robot_id claim.

Registration is best-effort and non-blocking: an unreachable backend must not
stop the robot from running locally (Edge's safety layer and MCP tools do not
depend on the cloud — DOC 3 §1.1). Failures are logged and retried in the
background thread; the caller (`server.main`) never waits on this.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

import jwt

from . import config as edge_config

logger = logging.getLogger(__name__)

_ROBOT_ID_FILE = Path(__file__).with_name(".edge_robot_id")

# Static — mirrors the `@mcp.tool()` functions actually registered in
# `server.py`. Not introspected at runtime: FastMCP tool registration happens
# at import time in the same module that would import this one, so
# hardcoding avoids a circular import for a list that only changes when a
# tool is added/removed here anyway.
_TOOL_CAPABILITIES = [
    {"name": "get_robot_state", "version": "1.0"},
    {"name": "capture", "version": "1.0"},
    {"name": "get_servo_state", "version": "1.0"},
    {"name": "take_action", "version": "1.0"},
    {"name": "check_status", "version": "1.0"},
    {"name": "check_success", "version": "1.0"},
    {"name": "go_home", "version": "1.0"},
    {"name": "abort", "version": "1.0"},
    {"name": "reset_episode", "version": "1.0"},
]

# ROBOT_MODE -> robot_type declared to the backend (must match
# frontend-ui's `ROBOT_MODELS` ids in lib/robot/models.ts).
_ROBOT_TYPE_BY_MODE = {"sim": "franka", "real": "so101"}

_RETRY_DELAYS_S = (2, 5, 15)


def resolve_robot_id() -> str:
    """`EDGE_ROBOT_ID` env override, else a UUID persisted to a local file so
    restarts keep registering the same identity instead of a new robot every
    boot."""
    env_id = edge_config.EDGE_ROBOT_ID
    if env_id:
        return env_id

    try:
        existing = _ROBOT_ID_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    generated = f"edge-{uuid.uuid4().hex[:12]}"
    try:
        _ROBOT_ID_FILE.write_text(generated + "\n", encoding="utf-8")
    except OSError:
        logger.warning("Không ghi được %s — robot_id sẽ đổi mỗi lần khởi động.", _ROBOT_ID_FILE)
    return generated


def robot_type_for_mode(robot_mode: str) -> str:
    return _ROBOT_TYPE_BY_MODE.get(robot_mode, robot_mode)


def _build_token(*, robot_id: str, robot_type: str, secret: str, base_url: str = "") -> str:
    now = int(time.time())
    payload = {
        "robot_id": robot_id,
        "robot_type": robot_type,
        "base_url": base_url,
        "capabilities": _TOOL_CAPABILITIES,
        "iat": now,
        "exp": now + 300,
        "typ": "edge_register",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def register_with_cloud_blocking(*, robot_id: str, robot_type: str) -> bool:
    """Single attempt. Returns True on 2xx, False otherwise (never raises —
    caller loops/backs off on False)."""
    base_url = edge_config.CLOUD_AGENT_BASE_URL
    secret = edge_config.EDGE_JWT_SECRET
    if not base_url or not secret:
        logger.info(
            "Bỏ qua đăng ký Cloud: CLOUD_AGENT_BASE_URL hoặc EDGE_JWT_SECRET chưa cấu hình."
        )
        return False

    import httpx

    edge_public_url = edge_config.EDGE_PUBLIC_URL
    if not edge_public_url:
        logger.warning(
            "EDGE_PUBLIC_URL chưa cấu hình — Edge vẫn đăng ký được nhưng camera/servo stream qua Cloud sẽ không hoạt động."
        )

    token = _build_token(
        robot_id=robot_id,
        robot_type=robot_type,
        secret=secret,
        base_url=edge_public_url,
    )
    url = base_url.rstrip("/") + "/api/v1/edge/register"
    try:
        resp = httpx.post(url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.warning("Đăng ký Cloud thất bại (%s): %s", url, exc)
        return False

    if resp.status_code >= 400:
        logger.warning("Đăng ký Cloud bị từ chối (%s): %s", resp.status_code, resp.text)
        return False

    body = resp.json().get("data", {})
    verb = "đã đăng ký mới" if body.get("created") else "đã có sẵn (bỏ qua)"
    logger.info("Đăng ký Cloud OK: robot_id=%s robot_type=%s (%s)", robot_id, robot_type, verb)
    return True


def register_with_cloud_background(*, robot_mode: str) -> None:
    """Fire-and-forget: call once on startup from a background thread, with a
    few short retries. Never blocks `server.main`."""
    robot_id = resolve_robot_id()
    robot_type = robot_type_for_mode(robot_mode)
    logger.info("Edge robot_id=%s robot_type=%s (mode=%s)", robot_id, robot_type, robot_mode)

    for delay in (0, *_RETRY_DELAYS_S):
        if delay:
            time.sleep(delay)
        if register_with_cloud_blocking(robot_id=robot_id, robot_type=robot_type):
            return
    logger.warning(
        "Không đăng ký được với Cloud sau %d lần thử — Edge vẫn chạy bình thường ở chế độ cục bộ.",
        1 + len(_RETRY_DELAYS_S),
    )
