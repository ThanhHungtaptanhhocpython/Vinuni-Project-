"""Event emitter — điểm phát SSE của Agent (DOC 1 §2.1, bảng node).

Trong hệ thống đầy đủ, emitter publish lên Redis pubsub để agent-api đẩy SSE
cho client. Ở skeleton này cung cấp LoggingEmitter (dev) và RecordingEmitter
(test); interface giữ nguyên để thay backend sau.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Protocol

from models.schemas import TraceEvent

from .sanitizer import sanitize_payload
from .trace_repo import trace_repository

logger = logging.getLogger("agent.events")


class EventEmitter(Protocol):
    def emit(self, event: str, data: dict | None = None) -> None: ...


class LoggingEmitter:
    """Ghi event ra log + trace thinking đọc được trên console."""

    def __init__(self, run_id: str = "", trace_id: str = "") -> None:
        self.run_id = run_id
        self.trace_id = trace_id

    def emit(self, event: str, data: dict | None = None) -> None:
        raw_payload = data or {}

        # Fast path: `token.delta` fires once per streamed chunk (the hottest
        # event by far) and `trace_repo._add_event_locked` only broadcasts it
        # (never persists — see module docstring), so the full sanitize +
        # logger.debug + dict-comprehension pipeline below is pure overhead
        # for it. Build the minimal TraceEvent directly and skip straight to
        # the repo.
        if event == "token.delta":
            rid = self.run_id or raw_payload.get("run_id") or "?"
            tid = self.trace_id or raw_payload.get("trace_id") or "trc_unknown"
            trace_event = TraceEvent(
                id=f"evt_{uuid.uuid4().hex[:12]}",
                run_id=rid,
                trace_id=tid,
                sequence=0,
                timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
                type=event,
                metadata=raw_payload,
            )
            trace_repository.add_event(trace_event)
            return

        # `summary.images` (tool.completed — capture/take_action/check_status
        # results, see nodes/tool.py) carries `data:image/jpeg;base64,...`
        # thumbnails: legitimately long strings, not secrets or log spam.
        # sanitize_payload's max_string_len=2000 would truncate them into
        # corrupted, unrenderable image data — pull them out, sanitize
        # everything else normally, then reattach untouched.
        raw_summary = raw_payload.get("summary")
        pulled_images = None
        if isinstance(raw_summary, dict) and isinstance(raw_summary.get("images"), list):
            pulled_images = raw_summary["images"]
            raw_payload = {
                **raw_payload,
                "summary": {k: v for k, v in raw_summary.items() if k != "images"},
            }

        # Apply sanitizer before logging or storing
        payload = sanitize_payload(raw_payload)
        if pulled_images is not None and isinstance(payload.get("summary"), dict):
            payload["summary"]["images"] = pulled_images

        # Strip large/sensitive text fields from logger to avoid leaking raw text
        log_payload = {k: v for k, v in payload.items() if k not in ["input_text", "final_response", "error", "delta", "message", "response", "args", "summary", "content"]}
        logger.debug(
            "event=%s run_id=%s at=%s data=%s",
            event,
            self.run_id,
            datetime.now(UTC).isoformat(timespec="milliseconds"),
            log_payload,
        )
        self._persist_to_repo(event, payload)

    def _persist_to_repo(self, event: str, payload: dict) -> None:
        rid = self.run_id or payload.get("run_id") or "?"
        tid = self.trace_id or payload.get("trace_id") or "trc_unknown"

        trace_event = TraceEvent(
            id=f"evt_{uuid.uuid4().hex[:12]}",
            run_id=rid,
            trace_id=tid,
            sequence=0, # Will be set by repo
            timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
            type=event,
            status=payload.get("status"),
            duration_ms=payload.get("duration_ms"),
            model=payload.get("model"),
            tool_name=payload.get("tool"),
            arguments=payload.get("args"),
            result_summary=payload.get("summary") or payload.get("robot_state") or payload.get("usage"),
            error=payload.get("error") if isinstance(payload.get("error"), dict) else ({"message": str(payload.get("error"))} if payload.get("error") else None),
            metadata=payload
        )
        trace_repository.add_event(trace_event)
class RecordingEmitter:
    """Lưu event vào list — dùng cho test."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event: str, data: dict | None = None) -> None:
        payload = sanitize_payload(data or {})
        self.events.append((event, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]
