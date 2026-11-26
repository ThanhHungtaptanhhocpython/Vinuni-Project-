"""Ephemeral scene memory for one agent run.

The memory is only a planning aid. It summarizes recent observations and any
structured detections returned by Edge, but it is never authoritative enough to
drive motion without a fresh capture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MAX_RECENT_OBSERVATIONS = 8
MAX_OBJECTS = 20


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def empty_scene_memory() -> dict[str, Any]:
    return {
        "objects": [],
        "recent_observations": [],
        "last_updated_at": None,
        "requires_fresh_capture_before_motion": True,
    }


def _object_key(obj: dict[str, Any]) -> str | None:
    label = obj.get("label") or obj.get("name") or obj.get("object") or obj.get("id")
    if label is None:
        return None
    return str(label).strip().lower() or None


def _normalize_detection(
    name: str,
    value: Any,
    *,
    observed_at: str,
    source_tool: str,
) -> dict[str, Any]:
    if isinstance(value, dict):
        normalized = dict(value)
        normalized.setdefault("label", name)
    else:
        normalized = {"label": name, "value": value}

    normalized["last_seen_at"] = observed_at
    normalized["source_tool"] = source_tool
    normalized["stale"] = True
    return normalized


def _extract_detections(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    detections = data.get("detections")
    if detections is None and isinstance(data.get("episode"), dict):
        detections = data["episode"].get("detections")
    if detections is None:
        return []

    observed_at = _now_iso()
    if isinstance(detections, list):
        return [
            _normalize_detection(
                str(item.get("label") or item.get("name") or idx),
                item,
                observed_at=observed_at,
                source_tool="detections",
            )
            for idx, item in enumerate(detections)
            if isinstance(item, dict)
        ]
    if isinstance(detections, dict):
        return [
            _normalize_detection(
                name,
                value,
                observed_at=observed_at,
                source_tool="detections",
            )
            for name, value in detections.items()
        ]
    return []


def update_scene_memory(
    memory: dict[str, Any] | None,
    *,
    tool_name: str,
    data: dict[str, Any] | None,
    images: list[Any],
) -> dict[str, Any]:
    next_memory = dict(memory or empty_scene_memory())
    observed_at = _now_iso()

    recent = list(next_memory.get("recent_observations") or [])
    if images or tool_name in {"capture", "capture_tool", "take_action", "check_success"}:
        recent.append(
            {
                "tool": tool_name,
                "observed_at": observed_at,
                "image_count": len(images),
                "has_structured_detections": bool(_extract_detections(data)),
            }
        )
    next_memory["recent_observations"] = recent[-MAX_RECENT_OBSERVATIONS:]

    objects_by_key: dict[str, dict[str, Any]] = {}
    for obj in next_memory.get("objects") or []:
        if isinstance(obj, dict):
            key = _object_key(obj)
            if key:
                objects_by_key[key] = dict(obj)

    for detection in _extract_detections(data):
        key = _object_key(detection)
        if key is None:
            continue
        existing = objects_by_key.get(key, {})
        existing.update(detection)
        objects_by_key[key] = existing

    next_memory["objects"] = list(objects_by_key.values())[-MAX_OBJECTS:]
    next_memory["last_updated_at"] = observed_at
    next_memory["requires_fresh_capture_before_motion"] = True
    return next_memory


def scene_memory_prompt(memory: dict[str, Any] | None) -> str:
    memory = memory or empty_scene_memory()
    objects = memory.get("objects") or []
    if not objects:
        objects_text = "Chưa có object memory có cấu trúc. Hãy dựa vào capture mới nhất."
    else:
        lines = []
        for obj in objects[:MAX_OBJECTS]:
            label = obj.get("label") or obj.get("name") or obj.get("object") or "unknown"
            confidence = obj.get("confidence")
            last_seen = obj.get("last_seen_at")
            suffix = []
            if confidence is not None:
                suffix.append(f"confidence={confidence}")
            if last_seen:
                suffix.append(f"last_seen_at={last_seen}")
            lines.append(f"- {label}" + (f" ({', '.join(suffix)})" if suffix else ""))
        objects_text = "\n".join(lines)

    return (
        "## Scene memory tạm thời\n"
        f"{objects_text}\n\n"
        "Quy tắc: scene memory chỉ là gợi ý lập kế hoạch. Trước mọi take_action, "
        "phải gọi capture/get_robot_state để xác nhận lại vị trí vật thể; không "
        "được điều khiển robot chỉ dựa trên memory cũ."
    )
