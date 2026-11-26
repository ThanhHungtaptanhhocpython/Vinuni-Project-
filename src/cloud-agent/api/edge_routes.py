"""Edge <-> cloud-agent registration.

An Edge device calls `POST /edge/register` on every boot with a JWT signed
using the shared `EDGE_JWT_SECRET` — proof it holds the secret, not a user
session. The console (frontend) then calls `GET /edge/registry/{robot_id}`
when looking up what that Edge declared (robot_type, capabilities). Pairing
via `POST /robots` is currently disabled (`ROBOT_004`); this registry still
records Edge self-registration on boot.
"""

from __future__ import annotations

from typing import Annotated

from auth.deps import require_user_if_configured
from auth.edge_tokens import decode_edge_registration_token
from auth.store import User
from auth.tokens import TokenError
from config import Settings, get_settings
from fastapi import APIRouter, Depends, Header
from stores.edge_registry import edge_registry_store
from stores.robots import robot_store

from api.common import api_error, envelope

router = APIRouter(tags=["edge"])


@router.post("/edge/register")
async def register_edge(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise api_error("EDGE_001", "Thiếu Authorization: Bearer <token>.", 401)
    token = authorization[len("bearer "):].strip()

    try:
        payload = decode_edge_registration_token(token, settings)
    except TokenError as exc:
        status_code = 500 if exc.code == "EDGE_000" else 401
        raise api_error(exc.code, exc.message, status_code) from exc

    record, created = edge_registry_store.register_if_new(
        robot_id=str(payload["robot_id"]),
        robot_type=str(payload["robot_type"]),
        base_url=str(payload.get("base_url") or "").strip().rstrip("/"),
        capabilities=payload.get("capabilities") or [],
    )
    robot_store.upsert_from_edge_registration(
        robot_id=str(payload["robot_id"]),
        robot_type=str(payload["robot_type"]),
    )
    return envelope({"created": created, **record})


@router.get("/edge/registry/{robot_id}")
async def get_edge_registry(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    record = edge_registry_store.get(robot_id)
    if record is None:
        raise api_error(
            "EDGE_002",
            "Không tìm thấy thiết bị Edge với ID này — kiểm tra Edge đã bật và đăng ký chưa.",
            404,
        )
    return envelope(record)
