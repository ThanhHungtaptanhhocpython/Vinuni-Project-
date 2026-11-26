"""JWT access tokens + short-lived OAuth state cookies (DOC 1 §4.2, §6.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from config import Settings


class TokenError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def create_access_token(
    *,
    user_id: str,
    email: str,
    settings: Settings,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_s),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("AUTH_004", "Access token đã hết hạn.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("AUTH_004", "Access token không hợp lệ.") from exc
    if payload.get("typ") != "access":
        raise TokenError("AUTH_004", "Access token không hợp lệ.")
    return payload


def sign_oauth_state(data: dict[str, Any], settings: Settings, ttl_s: int = 600) -> str:
    now = datetime.now(UTC)
    payload = {**data, "iat": now, "exp": now + timedelta(seconds=ttl_s), "typ": "oauth_state"}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_oauth_state(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise TokenError("AUTH_005", "OAuth state không hợp lệ hoặc đã hết hạn.") from exc
    if payload.get("typ") != "oauth_state":
        raise TokenError("AUTH_005", "OAuth state không hợp lệ.")
    return payload
