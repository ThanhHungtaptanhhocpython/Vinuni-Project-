from __future__ import annotations

import asyncio
import json
import queue
from typing import Annotated

from agents.run_control import get_active_run, request_cancel
from agents.thinking_stream import is_terminal_trace_event, project_trace_event
from agents.trace_repo import trace_repository
from auth.deps import require_user_if_configured
from auth.store import User
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from models.schemas import RunSource, RunStatus, TraceEvent
from stores.runs import run_store

from api.common import api_error, current_user_id, envelope

router = APIRouter(tags=["runs"])

_RUN_STATUSES = {status.value for status in RunStatus} | {"SCHEDULED"}
_RUN_SOURCES = {source.value for source in RunSource}


def _user_id(user: User | None) -> str:
    return current_user_id(user)


def _duration_ms(row: dict) -> int | None:
    duration_s = row.get("duration_s")
    if isinstance(duration_s, int | float):
        return int(duration_s * 1000)
    return row.get("duration_ms")


def _console_row_to_trace_shape(row: dict) -> dict:
    return {
        **row,
        "user_id": row.get("user_id", "user_dev"),
        "duration_ms": _duration_ms(row),
        "final_response": row.get("final_response"),
        "events_dropped": row.get("events_dropped", 0),
    }


def _sort_created_desc(row: dict) -> tuple[str, str]:
    return (str(row.get("created_at") or ""), str(row.get("id") or ""))


def _parse_stream_cursor(
    last_event_id: str | None,
    after_sequence: int | None,
) -> int | None:
    if last_event_id is None or not last_event_id.strip():
        return after_sequence
    try:
        cursor = int(last_event_id.strip())
    except ValueError:
        raise api_error("VAL_005", "Invalid Last-Event-ID cursor.", 422) from None
    if cursor < 0:
        raise api_error("VAL_005", "Invalid Last-Event-ID cursor.", 422)
    return cursor


def _sse(event: str, data: dict, *, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


@router.get("/runs")
async def list_runs(
    user: Annotated[User | None, Depends(require_user_if_configured)],
    robot_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    has_error: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    if status and any(part not in _RUN_STATUSES for part in status.split(",") if part):
        raise api_error("VAL_005", "Invalid run status.", 422)
    if source and any(part not in _RUN_SOURCES for part in source.split(",") if part):
        raise api_error("VAL_005", "Invalid run source.", 422)

    console_rows = [
        row
        for row in run_store.list(
            user_id=_user_id(user),
            robot_id=robot_id,
            status=status,
            source=source,
            has_error=has_error,
            limit=offset + limit,
        )
        # Nhật ký là từng lượt robot/agent chạy. Dòng `session:*` chỉ là
        # tổng hợp hội thoại cũ, không có timeline/telemetry để trace lại.
        if not str(row.get("id", "")).startswith("session:")
    ]
    if session_id:
        console_rows = [row for row in console_rows if row.get("session_id") == session_id]
    console_items = [_console_row_to_trace_shape(row) for row in console_rows]

    trace_filters = {"user_id": _user_id(user)}
    if robot_id:
        trace_filters["robot_id"] = robot_id
    if session_id:
        trace_filters["session_id"] = session_id
    if source:
        trace_filters["source"] = source
    if status and status != "SCHEDULED":
        trace_filters["status"] = status
    trace_page = trace_repository.get_runs(
        limit=offset + limit,
        offset=0,
        **trace_filters,
    )

    trace_ids = {item.id for item in trace_page.items}
    merged_by_id = {item["id"]: item for item in console_items}
    for item in trace_page.items:
        merged_by_id[item.id] = item.model_dump()
    merged = sorted(merged_by_id.values(), key=_sort_created_desc, reverse=True)

    console_only_total = len([item for item in console_items if item["id"] not in trace_ids])
    response = envelope(merged[offset:offset + limit], total=trace_page.total + console_only_total)
    return response


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    trace_run = trace_repository.get_run(run_id)
    if trace_run is not None:
        if trace_run.user_id != _user_id(user):
            raise api_error("AGENT_012", "Không tìm thấy lượt chạy.", 404)
        return envelope(trace_run.model_dump())

    run = run_store.get(run_id, user_id=_user_id(user))
    if run is None:
        raise api_error("AGENT_012", "Không tìm thấy lượt chạy.", 404)
    return envelope(_console_row_to_trace_shape(run))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    run = trace_repository.get_run(run_id)
    if not run or run.user_id != _user_id(user):
        raise api_error("AGENT_012", "Không tìm thấy lượt chạy.", 404)
    if run.status != RunStatus.RUNNING:
        return envelope(
            {
                "run_id": run_id,
                "cancel_requested": False,
                "status": run.status,
                "message": "Run không còn chạy.",
            }
        )

    ctx = await get_active_run(run_id)
    if ctx is None:
        raise api_error("AGENT_013", "Run không còn nằm trong worker hiện tại.", 409)

    result = await request_cancel(ctx)
    return envelope({"run_id": run_id, **result})


@router.get("/runs/{run_id}/timeline", response_model=list[TraceEvent])
async def get_timeline(
    run_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> list[TraceEvent]:
    run = trace_repository.get_run(run_id)
    if run is not None:
        if run.user_id != _user_id(user):
            raise api_error("AGENT_012", "Không tìm thấy lượt chạy.", 404)
        return trace_repository.get_timeline(run_id)
    # Console-only row (legacy schedule dual-id, or agent never created a trace):
    # detail page can still load; timeline is simply empty.
    console = run_store.get(run_id, user_id=_user_id(user))
    if console is None:
        raise api_error("AGENT_012", "Không tìm thấy lượt chạy.", 404)
    return []


@router.get("/runs/{run_id}/stream")
async def stream_run_thinking(
    run_id: str,
    request: Request,
    user: Annotated[User | None, Depends(require_user_if_configured)],
    after_sequence: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    run = trace_repository.get_run(run_id)
    if not run or run.user_id != _user_id(user):
        raise api_error("AGENT_012", "Không tìm thấy lượt chạy.", 404)

    cursor = _parse_stream_cursor(last_event_id, after_sequence)
    backlog, subscription = trace_repository.subscribe(
        run_id,
        after_sequence=cursor,
        max_queue_size=100,
    )

    async def event_generator():
        heartbeat_s = 15.0
        poll_s = 0.25
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time() + heartbeat_s
        try:
            for trace_event in backlog:
                if await request.is_disconnected():
                    return
                projected = project_trace_event(trace_event)
                yield _sse(
                    "agent.thinking",
                    projected,
                    event_id=trace_event.sequence,
                )
                if is_terminal_trace_event(trace_event.type):
                    return

            while not subscription.closed:
                if await request.is_disconnected():
                    return
                try:
                    trace_event = subscription.queue.get_nowait()
                except queue.Empty:
                    now = loop.time()
                    if now >= next_heartbeat:
                        yield _sse("ping", {"type": "ping", "run_id": run_id})
                        next_heartbeat = now + heartbeat_s
                    await asyncio.sleep(min(poll_s, max(next_heartbeat - now, 0.0)))
                    continue

                next_heartbeat = loop.time() + heartbeat_s
                projected = project_trace_event(trace_event)
                yield _sse(
                    "agent.thinking",
                    projected,
                    event_id=trace_event.sequence,
                )
                if is_terminal_trace_event(trace_event.type):
                    return

            yield _sse(
                "agent.thinking",
                {
                    "type": "agent.thinking",
                    "run_id": run_id,
                    "trace_id": run.trace_id,
                    "sequence": cursor or 0,
                    "timestamp": run.completed_at or run.created_at,
                    "stage": "system",
                    "status": "failed",
                    "label": "Stream bị đóng",
                    "message": "Client xử lý stream quá chậm.",
                    "duration_ms": None,
                    "error": {
                        "code": "STREAM_QUEUE_FULL",
                        "message": "Client xử lý stream quá chậm.",
                    },
                },
            )
        finally:
            trace_repository.unsubscribe(subscription)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    user: Annotated[User | None, Depends(require_user_if_configured)],
    after_sequence: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    run = trace_repository.get_run(run_id)
    if not run or run.user_id != _user_id(user):
        raise api_error("AGENT_012", "Không tìm thấy lượt chạy.", 404)

    cursor = _parse_stream_cursor(last_event_id, after_sequence)
    backlog, subscription = trace_repository.subscribe(
        run_id,
        after_sequence=cursor,
        max_queue_size=100,
    )

    async def event_generator():
        heartbeat_s = 15.0
        poll_s = 0.25
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time() + heartbeat_s
        try:
            for trace_event in backlog:
                if await request.is_disconnected():
                    return
                yield _sse(
                    "trace",
                    trace_event.model_dump(),
                    event_id=trace_event.sequence,
                )
                if is_terminal_trace_event(trace_event.type):
                    return

            while not subscription.closed:
                if await request.is_disconnected():
                    return
                try:
                    trace_event = subscription.queue.get_nowait()
                except queue.Empty:
                    now = loop.time()
                    if now >= next_heartbeat:
                        yield _sse("ping", {"type": "ping", "run_id": run_id})
                        next_heartbeat = now + heartbeat_s
                    await asyncio.sleep(min(poll_s, max(next_heartbeat - now, 0.0)))
                    continue

                next_heartbeat = loop.time() + heartbeat_s
                yield _sse(
                    "trace",
                    trace_event.model_dump(),
                    event_id=trace_event.sequence,
                )
                if is_terminal_trace_event(trace_event.type):
                    return

            yield _sse(
                "error",
                {
                    "type": "error",
                    "error": {
                        "code": "STREAM_QUEUE_FULL",
                        "message": "Client xử lý stream quá chậm.",
                    },
                },
            )
        finally:
            trace_repository.unsubscribe(subscription)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
