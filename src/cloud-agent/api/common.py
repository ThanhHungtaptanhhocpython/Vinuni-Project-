"""Shared API envelope + error helper (DOC 1 §5)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException


def meta() -> dict[str, str]:
    return {
        "trace_id": f"trc_{secrets.token_hex(8)}",
        "server_time": datetime.now(UTC).isoformat(timespec="milliseconds"),
    }


def envelope(data: Any, **extra_meta: Any) -> dict:
    payload = {"data": data, "meta": {**meta(), **extra_meta}}
    return payload


def api_error(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message},
    )


def current_user_id(user: Any | None) -> str:
    """Owner id for console rows — authenticated user or local fallback."""
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    return "user_dev"
