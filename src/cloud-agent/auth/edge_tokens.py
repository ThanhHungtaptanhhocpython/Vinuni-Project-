"""JWT for Edge self-registration — signed with `EDGE_JWT_SECRET`, a secret
shared between cloud-agent and every Edge device (symmetric, HS256).

Deliberately separate from `auth.tokens` (user access tokens): the trust
boundary is different — an Edge device, not a logged-in browser — and the two
secrets must be rotatable independently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from config import Settings

from auth.tokens import TokenError

_REGISTER_TOKEN_TTL_S = 300


def create_edge_registration_token(
    *,
    robot_id: str,
    robot_type: str,
    capabilities: list[dict[str, Any]],
    secret: str,
    base_url: str = "",
) -> str:
    """Used by the Edge side (and tests) to build the token cloud-agent verifies."""
    now = datetime.now(UTC)
    payload = {
        "robot_id": robot_id,
        "robot_type": robot_type,
        "base_url": base_url,
        "capabilities": capabilities,
        "iat": now,
        "exp": now + timedelta(seconds=_REGISTER_TOKEN_TTL_S),
        "typ": "edge_register",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_edge_registration_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.edge_jwt_secret:
        raise TokenError("EDGE_000", "EDGE_JWT_SECRET chưa được cấu hình trên backend.")
    try:
        payload = jwt.decode(token, settings.edge_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("EDGE_001", "Token đăng ký Edge đã hết hạn.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("EDGE_001", "Token đăng ký Edge không hợp lệ.") from exc
    if payload.get("typ") != "edge_register":
        raise TokenError("EDGE_001", "Token đăng ký Edge không hợp lệ.")
    if not payload.get("robot_id") or not payload.get("robot_type"):
        raise TokenError("EDGE_001", "Token đăng ký Edge thiếu robot_id/robot_type.")
    return payload
