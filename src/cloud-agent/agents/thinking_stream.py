from __future__ import annotations

from typing import Any

from models.schemas import TraceEvent

from agents.sanitizer import sanitize_payload

_MAX_TEXT_LEN = 240
_EVENT_MAP: dict[str, tuple[str, str, str]] = {
    "run.started": ("initializing", "running", "Khởi tạo run"),
    "llm.started": ("model_call", "running", "Gọi model"),
    "llm.completed": ("model_call", "completed", "Model đã phản hồi"),
    "llm.failed": ("model_call", "failed", "Gọi model thất bại"),
    "tool.started": ("tool_execution", "running", "Agent gọi tool"),
    "tool.completed": ("tool_execution", "completed", "Tool hoàn tất"),
    "tool.failed": ("tool_execution", "failed", "Tool thất bại"),
    "episode.started": ("robot_episode", "running", "Robot bắt đầu thực thi"),
    "episode.completed": ("robot_episode", "completed", "Robot hoàn tất episode"),
    "run.completed": ("finished", "completed", "Run hoàn tất"),
    "run.failed": ("finished", "failed", "Run thất bại"),
    "run.cancelled": ("finished", "cancelled", "Run đã huỷ"),
    "run.timeout": ("finished", "timeout", "Run quá thời gian"),
    "system.warning": ("system", "running", "Cảnh báo hệ thống"),
}
_TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled", "run.timeout"}
_DENY_TEXT_MARKERS = {
    "prompt",
    "messages",
    "content",
    "reasoning",
    "chain_of_thought",
    "authorization",
    "token",
    "secret",
    "api_key",
    "apikey",
}


def is_terminal_trace_event(event_type: str) -> bool:
    return event_type in _TERMINAL_EVENTS


def _safe_text(value: Any, *, max_len: int = _MAX_TEXT_LEN) -> str | None:
    if not isinstance(value, str):
        return None
    text = sanitize_payload(value)
    if not isinstance(text, str):
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in _DENY_TEXT_MARKERS):
        return None
    text = text.strip()
    if not text:
        return None
    return text[:max_len]


def _safe_tool_name(value: Any) -> str | None:
    text = _safe_text(value, max_len=80)
    if text is None:
        return None
    return text.replace("\n", " ").replace("\r", " ")


def _safe_error(error: dict | None) -> dict[str, str] | None:
    if not isinstance(error, dict):
        return None
    sanitized = sanitize_payload(error)
    if not isinstance(sanitized, dict):
        return None
    code = _safe_text(sanitized.get("code"), max_len=80)
    message = _safe_text(sanitized.get("message"), max_len=_MAX_TEXT_LEN)
    if not code and not message:
        return None
    result: dict[str, str] = {}
    if code:
        result["code"] = code
    if message:
        result["message"] = message
    return result


def _fallback_message(event: TraceEvent) -> str | None:
    if event.type in {"tool.started", "tool.completed", "tool.failed"}:
        tool_name = _safe_tool_name(event.tool_name)
        if tool_name:
            return f"Tool: {tool_name}"
    if event.type == "system.warning":
        metadata = sanitize_payload(event.metadata)
        if isinstance(metadata, dict):
            return _safe_text(metadata.get("message"))
    return None


def project_trace_event(event: TraceEvent) -> dict[str, Any]:
    stage, status, label = _EVENT_MAP.get(
        event.type,
        ("unknown", "running", "Agent cập nhật trạng thái"),
    )
    payload: dict[str, Any] = {
        "type": "agent.thinking",
        "run_id": event.run_id,
        "trace_id": event.trace_id,
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "stage": stage,
        "status": status,
        "label": label,
        "message": _fallback_message(event),
        "duration_ms": event.duration_ms,
        "error": _safe_error(event.error),
    }
    tool_name = _safe_tool_name(event.tool_name)
    if tool_name:
        payload["tool_name"] = tool_name
    return payload
