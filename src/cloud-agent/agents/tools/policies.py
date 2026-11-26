"""Chính sách tool: allowlist, timeout, retry, song song hoá.

Nguồn: DOC 0 §3.4, §6.7, §7.4 — trích qua DOC 1 §2.6, §7.4, §7.5.
"""

from __future__ import annotations

# edge_vla CONTROL_FREQ — used to budget episode wall time from k.
_EDGE_CONTROL_HZ = 20.0
# edge_vla TASK_TIMEOUT_S hard ceiling for take_action runs.
_EDGE_TASK_TIMEOUT_S = 1800.0


def estimate_episode_duration_s(k: int | None, *, fallback: float = 180.0) -> float:
    """Wall-clock budget for a take_action run with k control ticks.

    Headless osmesa sim on a CPU VM can run ~10× slower than real-time, so a
    flat 180s estimate makes `_monitor_episode` abort healthy episodes around
    step 450/500 while the edge worker is still running."""
    steps = max(int(k or 500), 1)
    # steps/hz = ideal sim seconds; ×12 slack + margin for settle/success check.
    budget = steps / _EDGE_CONTROL_HZ * 12.0 + 90.0
    return min(_EDGE_TASK_TIMEOUT_S - 60.0, max(fallback, budget))


# Chỉ 8 tool được phép gọi qua MCP (allowlist — DOC 0 §6.7)
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "capture_tool",
        "capture",
        "get_robot_state",
        "take_action",
        "stop_robot",
        "get_task_status",
        "check_status",
        "health_check",
        "reset_error",
        "reset_episode",
        "go_home",
        "list_tasks",
    }
)

# Timeout theo tool (giây) — DOC 1 §7.4
TOOL_TIMEOUT: dict[str, float] = {
    "capture_tool": 10.0,
    "capture": 10.0,
    "get_robot_state": 5.0,
    "get_servo_state": 5.0,
    "take_action": 15.0,  # chỉ khởi động; episode chạy nền (ADR-001)
    "stop_robot": 3.0,
    # `abort` — tên thật của `edge_vla.server` cho lệnh dừng khẩn (`stop_robot`
    # là tên đặc tả cũ). `_monitor_episode` (agents/nodes/tool.py) gọi tên này
    # trực tiếp, không qua LLM — cần có trong bảng dù chưa nằm trong
    # `ALLOWED_TOOLS` (không cấp thêm quyền cho LLM, chỉ để nội bộ gọi được).
    "abort": 3.0,
    "get_task_status": 5.0,
    "check_status": 5.0,
    "health_check": 5.0,
    "reset_error": 10.0,
    "reset_episode": 10.0,
    "check_success": 5.0,
    "go_home": 30.0,
    "list_tasks": 5.0,
}

# (số lần retry, danh sách backoff giây) — DOC 1 §7.4
TOOL_RETRY: dict[str, tuple[int, list[float]]] = {
    "capture_tool": (1, [1.0]),
    "capture": (1, [1.0]),
    "get_robot_state": (2, [0.3, 0.8]),
    "get_servo_state": (2, [0.3, 0.8]),
    "take_action": (0, []),  # KHÔNG retry
    "stop_robot": (3, [0.2, 0.4, 0.8]),
    "abort": (3, [0.2, 0.4, 0.8]),
    "get_task_status": (2, [0.5, 1.0]),
    "check_status": (2, [0.5, 1.0]),
    "health_check": (2, [0.5, 1.0]),
    "reset_error": (1, [1.0]),
    "reset_episode": (1, [1.0]),
    "check_success": (2, [0.3, 0.8]),
    "go_home": (0, []),  # KHÔNG retry
    "list_tasks": (1, [0.5]),
}

# Phân loại cho quy tắc song song hoá — DOC 1 §7.5
READ_ONLY_TOOLS = frozenset({
    "capture_tool", "capture", "get_robot_state", "get_servo_state", "health_check", "list_tasks",
    "check_success",
})
MOTION_TOOLS = frozenset({"take_action", "go_home"})

# Tool được miễn circuit breaker — DOC 1 §7.3
BREAKER_EXEMPT_TOOLS = frozenset({"stop_robot", "abort", "health_check"})


def plan_tool_batches(tool_calls: list[dict]) -> list[list[dict]]:
    """Chia tool_calls của một lượt LLM thành các batch thực thi (DOC 1 §7.5).

    - Có stop_robot: chạy đầu tiên, huỷ mọi tool khác trong cùng batch.
    - reset_error: tuần tự, trước các tool khác.
    - Tool chỉ đọc: gom một batch chạy song song.
    - Tool gây chuyển động: tuần tự, luôn chạy cuối cùng.
    """
    stops = [c for c in tool_calls if c["name"] == "stop_robot"]
    if stops:
        return [[stops[0]]]

    batches: list[list[dict]] = []
    batches.extend([c] for c in tool_calls if c["name"] == "reset_error")

    read_only = [c for c in tool_calls if c["name"] in READ_ONLY_TOOLS]
    if read_only:
        batches.append(read_only)

    batches.extend([c] for c in tool_calls if c["name"] in MOTION_TOOLS)

    # Tool ngoài các nhóm trên (vd: get_task_status) chạy tuần tự sau cùng
    known = READ_ONLY_TOOLS | MOTION_TOOLS | {"reset_error", "stop_robot"}
    batches.extend([c] for c in tool_calls if c["name"] not in known)
    return batches
