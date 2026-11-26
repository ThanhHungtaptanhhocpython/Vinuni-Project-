from __future__ import annotations

from typing import Annotated

from auth.deps import require_user_if_configured
from auth.store import User
from fastapi import APIRouter, Depends, Query, Response, status
from models.console import MessageFeedbackPatch, SessionCreate, SessionPatch
from services.memory import long_term_memory_service
from stores.robots import robot_store
from stores.sessions import session_store

from api.common import api_error, current_user_id, envelope

router = APIRouter(tags=["sessions"])


@router.get("/sessions")
async def list_sessions(
    user: Annotated[User | None, Depends(require_user_if_configured)],
    q: str | None = Query(default=None),
    robot_id: str | None = Query(default=None),
    include_hidden: bool = Query(default=False),
    include_empty: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict:
    owner = current_user_id(user)
    items, next_cursor = session_store.list(
        user_id=owner,
        q=q,
        robot_id=robot_id,
        include_hidden=include_hidden,
        include_empty=include_empty,
        limit=limit,
        cursor=cursor,
    )
    return envelope(items, next_cursor=next_cursor, has_more=bool(next_cursor))


@router.get("/sessions/latest")
async def latest_session(
    user: Annotated[User | None, Depends(require_user_if_configured)],
    robot_id: str | None = Query(default=None),
) -> dict:
    session = session_store.latest(user_id=current_user_id(user), robot_id=robot_id)
    if session is None:
        raise api_error("AGENT_012", "Không tìm thấy phiên.", 404)
    return envelope(session)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    owner = current_user_id(user)
    if not robot_store.exists(body.robot_id):
        raise api_error("SCHED_004", "Robot đã chọn không còn tồn tại.", 404)
    return envelope(session_store.create(body.robot_id, user_id=owner))


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    session = session_store.get(session_id, user_id=current_user_id(user))
    if session is None:
        raise api_error("AGENT_012", "Không tìm thấy phiên.", 404)
    return envelope(session)


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    body: SessionPatch,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    title = body.title.strip()
    if not title:
        raise api_error("VAL_006", "Tên phiên không được để trống.", 422)
    session = session_store.rename(session_id, title, user_id=current_user_id(user))
    if session is None:
        raise api_error("AGENT_012", "Không tìm thấy phiên.", 404)
    return envelope(session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> Response:
    result = session_store.delete(session_id, user_id=current_user_id(user))
    if result == "missing":
        raise api_error("AGENT_012", "Không tìm thấy phiên.", 404)
    if result == "busy":
        raise api_error("AGENT_020", "Không thể xoá khi phiên đang chạy nhiệm vụ.", 409)
    await long_term_memory_service.delete_memories_by_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    messages = session_store.list_messages(session_id, user_id=current_user_id(user))
    if messages is None:
        raise api_error("AGENT_012", "Không tìm thấy phiên.", 404)
    return envelope(messages)


@router.patch("/sessions/{session_id}/messages/{message_id}/feedback")
async def set_message_feedback(
    session_id: str,
    message_id: str,
    body: MessageFeedbackPatch,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    message = session_store.set_message_feedback(
        session_id, message_id, body.feedback, user_id=current_user_id(user)
    )
    if message is None:
        raise api_error("AGENT_012", "Không tìm thấy tin nhắn.", 404)
    return envelope(message)
