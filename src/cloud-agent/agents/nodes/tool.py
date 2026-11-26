"""tool_node — gọi MCP Client theo quy tắc song song hoá (DOC 1 §2.1, §7.5).

take_action được xử lý bất đồng bộ theo ADR-001 (DOC 1 §2.7): gọi khởi động
(timeout 15s), sau đó vòng lặp theo dõi get_task_status mỗi 2s ngay trong
tool_node, kiểm tra cancel/deadline, và chỉ trả kết quả cuối cho LLM.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
from langchain_core.runnables import RunnableConfig
from services.robot_status import mark_busy, release_if_idle, ui_status_from_edge
from stores.edge_registry import edge_registry_store

from ..context import RunContext, get_run_context
from ..errors import AppError
from ..state import ToolCallRecord
from ..tools.mcp_client import ToolError, ToolResult
from ..tools.policies import MOTION_TOOLS, TOOL_TIMEOUT, plan_tool_batches

# Cả hai bộ từ vựng trạng thái: cũ (SUCCESS/STOPPED/MAX_STEPS/SAFETY_ABORT/
# FAILED, đặt cứng trong `_monitor_episode` — STOPPED/SAFETY_ABORT/TIMEOUT là
# hàm này TỰ đặt khi cancel/deadline/an toàn, không phải edge trả về) và thật
# (`edge_vla.core.task_store`: DONE/ABORTED/TIMEOUT/SAFETY_STOP/
# FAILED_MAX_RETRY, edge trả trực tiếp qua `check_status`).
_EPISODE_TERMINAL = {
    "SUCCESS", "STOPPED", "TIMEOUT", "MAX_STEPS", "SAFETY_ABORT", "FAILED",
    "DONE", "ABORTED", "SAFETY_STOP", "FAILED_MAX_RETRY",
}

_SERVO_STREAM_BATCH_S = 4.0
_SERVO_STREAM_SAMPLE_HZ = 10.0


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _error_result(tool_name: str, exc: AppError) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        error=ToolError(code=exc.code, message=exc.message, detail=exc.detail),
    )


def _is_robot_abnormal(robot_state: dict, temp_limit_c: float) -> str | None:
    """Trả reason nếu robot bất thường, else None (system prompt §2.3 #4)."""
    flags = robot_state.get("error_flags") or []
    if flags:
        return f"error_flags={flags}"
    temp = robot_state.get("temperature_c")
    if isinstance(temp, int | float) and temp >= temp_limit_c:
        return f"temperature_c={temp}>={temp_limit_c}"
    payload = robot_state.get("payload_g")
    if isinstance(payload, int | float) and payload > 1500:
        return f"payload_g={payload}"
    return None


def _edge_servo_stream_url(robot_id: str) -> str | None:
    record = edge_registry_store.get(robot_id)
    if record is None:
        return None
    base_url = str(record.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return None
    return f"{base_url}/servo-stream"


def _sse_data_blocks(buffer: str) -> tuple[list[str], str]:
    blocks: list[str] = []
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        data = "\n".join(
            line.removeprefix("data:").lstrip()
            for line in block.splitlines()
            if line.startswith("data:")
        )
        if data:
            blocks.append(data)
    return blocks, buffer


async def _record_servo_stream(ctx: RunContext, episode_id: str, max_steps: int | None) -> None:
    """Record real Edge `/servo-stream` samples into the run trace.

    Edge publishes servo state from the actual sim/real backend. This recorder
    batches samples before storing them so a long run does not exhaust the run
    trace event cap. It does not synthesize robot poses.
    """
    url = _edge_servo_stream_url(ctx.robot_id)
    if url is None:
        ctx.emitter.emit(
            "servo.stream.status",
            {
                "run_id": ctx.run_id,
                "episode_id": episode_id,
                "status": "unavailable",
                "reason": "edge_base_url_missing",
            },
        )
        return

    timeout = httpx.Timeout(connect=3.0, read=None, write=3.0, pool=3.0)
    last_emit = _now().timestamp()
    last_sample = 0.0
    min_sample_period_s = 1.0 / _SERVO_STREAM_SAMPLE_HZ
    buffer = ""
    pending: list[dict] = []

    def emit_batch() -> None:
        nonlocal pending, last_emit
        if not pending:
            return
        ctx.emitter.emit(
            "servo.stream.batch",
            {
                "run_id": ctx.run_id,
                "episode_id": episode_id,
                "max_steps": max_steps,
                "source": "edge_servo_stream",
                "sample_count": len(pending),
                "samples": pending,
            },
        )
        pending = []
        last_emit = _now().timestamp()

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            async with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as response:
                response.raise_for_status()
                ctx.emitter.emit(
                    "servo.stream.status",
                    {
                        "run_id": ctx.run_id,
                        "episode_id": episode_id,
                        "status": "connected",
                        "source": "edge_servo_stream",
                    },
                )
                async for chunk in response.aiter_text():
                    if ctx.cancel_event.is_set():
                        break
                    buffer += chunk.replace("\r\n", "\n")
                    blocks, buffer = _sse_data_blocks(buffer)
                    for data in blocks:
                        try:
                            servo_state = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(servo_state, dict) or not isinstance(servo_state.get("joints"), list):
                            continue
                        now_ts = _now().timestamp()
                        if now_ts - last_sample < min_sample_period_s:
                            continue
                        last_sample = now_ts
                        pending.append(servo_state)
                        if now_ts - last_emit >= _SERVO_STREAM_BATCH_S:
                            emit_batch()
    except httpx.HTTPError as exc:
        ctx.emitter.emit(
            "servo.stream.status",
            {
                "run_id": ctx.run_id,
                "episode_id": episode_id,
                "status": "failed",
                "source": "edge_servo_stream",
                "reason": str(exc),
            },
        )
        return
    except asyncio.CancelledError:
        return
    finally:
        emit_batch()


def _build_tool_summary(result: ToolResult) -> dict:
    """Trace-visible `data` + resolved image URLs for a `tool.completed` event.
    Kept separate so trace SSE and LLM message serialization resolve images the
    same way without embedding base64 in the text payload."""
    summary: dict = {}
    if isinstance(result.data, dict) and result.data:
        summary = dict(result.data)
    elif result.robot_state:
        summary = {"status": result.robot_state.get("status")}
    if result.images:
        # `ImageRef.url` is a pre-Phase-8 placeholder (`file://...`, see
        # http_transport.py's `_normalize_tool_result`) — only
        # `thumbnail_b64` carries the actual JPEG. `serialize.py`'s
        # `build_image_message` does this same substitution for the
        # LLM-facing message; the trace summary needs its own copy since
        # it goes out over a different wire path (SSE, to the UI).
        resolved_images = []
        for img in result.images:
            url = img.url
            if not url.startswith(("http://", "https://", "data:")):
                if not img.thumbnail_b64:
                    continue
                url = f"data:image/jpeg;base64,{img.thumbnail_b64}"
            resolved_images.append(
                {"url": url, "camera_id": img.camera_id, "captured_at": img.captured_at}
            )
        if resolved_images:
            summary["images"] = resolved_images
    return summary


async def _call_simple(ctx: RunContext, call: dict) -> ToolResult:
    original_name = call["name"]
    tool_name = original_name
    arguments = dict(call.get("args") or {})
    wait_s = 0.0
    # Đọc trước khi bất kỳ nhánh nào bên dưới có thể pop "wait_s" khỏi
    # `arguments` (nhánh legacy của check_status) — cần giá trị gốc để cộng
    # vào timeout của lệnh gọi MCP; thiếu bước này, gọi check_status với
    # wait_s lớn (native path, edge tự chờ nội bộ) sẽ bị timeout ở chính
    # tầng MCP client trước khi edge kịp trả lời.
    requested_wait_s = float(arguments.get("wait_s") or 0)

    use_legacy = True
    bound_names: set[str] = set()
    if hasattr(ctx, "lc_tools"):
        bound_names = {t.name for t in ctx.lc_tools}
        for t in ctx.lc_tools:
            if t.name == original_name:
                use_legacy = False
                if hasattr(t, "metadata") and t.metadata and "real_name" in t.metadata:
                    tool_name = t.metadata["real_name"]
                    use_legacy = True
                break

    if use_legacy:
        if original_name == "capture" and tool_name == "capture":
            tool_name = "capture_tool"
        elif original_name == "check_status":
            if tool_name == "check_status":
                tool_name = "get_task_status"
            if "task_id" in arguments:
                arguments["episode_id"] = arguments.pop("task_id")
            wait_s = float(arguments.pop("wait_s", 0))
        elif original_name == "abort":
            # `abort` is never LLM-bound (monitor-only). Native edge_vla exposes
            # `abort`; mock / DOC naming still uses `stop_robot`. Prefer the
            # bound name when present, otherwise fall back to stop_robot.
            if "abort" not in bound_names:
                tool_name = "stop_robot"
                arguments = {"mode": "GRACEFUL"}

    if wait_s > 0:
        await asyncio.sleep(wait_s)

    # `check_status(wait_s=N)` blocks on edge for up to N seconds (clamped 1–15).
    # MCP client timeout must exceed that blocking window plus HTTP/MCP overhead.
    timeout_base = TOOL_TIMEOUT.get(original_name, TOOL_TIMEOUT.get(tool_name, 10.0))
    if original_name == "check_status":
        mcp_timeout_s = timeout_base + requested_wait_s + 20.0
    else:
        mcp_timeout_s = timeout_base + requested_wait_s

    result = await ctx.mcp.call_tool(
        robot_id=ctx.robot_id,
        tool_name=tool_name,
        arguments=arguments,
        trace_id=ctx.trace_id,
        timeout_s=mcp_timeout_s,
        priority=1 if tool_name in ("stop_robot", "abort") else 5,
    )

    # Preserve original LLM-facing name
    result.tool_name = original_name
    return result


async def _monitor_episode(
    ctx: RunContext,
    state: dict,
    start_result: ToolResult,
    max_steps: int | None = None,
    tool_call_id: str | None = None,
) -> ToolResult:
    """Vòng lặp theo dõi episode (DOC 1 §2.7) + kiểm tra robot state định kỳ.

    Tên tool gọi bên trong đây là tên THẬT của `edge_vla.server`
    (`check_status`/`capture`/`abort`), không phải đặc tả MCP cũ
    (`get_task_status`/`capture_tool`/`stop_robot`) — hàm này trước đây gọi
    cứng tên cũ và chưa từng được nối vào `tool_node`, nên chưa từng chạy được
    với edge_vla hiện tại; LLM phải tự gọi `check_status` lặp lại thay vì có
    thanh tiến độ tự động. Xem `tool_node` — nơi hàm này giờ được gọi.

    Mỗi episode_poll_interval_s:
      - cancel / deadline
      - check_status (tiến độ VLA)
      - mỗi robot_state_poll_every nhịp: get_robot_state (error_flags, nhiệt độ)
        → bất thường: abort + SAFETY_ABORT

    Production còn subscribe Redis pubsub episode:{id}.
    """
    # `task_id` là tên tham số thật (edge_vla.server.take_action/check_status/
    # abort) — biến vẫn tên `episode_id` để khớp field `episode_id` mà
    # `episode.*` events và frontend (`trace-translate.ts`) đã dùng.
    episode_id = start_result.data.get("task_id") or start_result.data.get("episode_id", "")
    from ..tools.policies import estimate_episode_duration_s

    estimated_s = float(start_result.data.get("estimated_duration_s", 120))
    if max_steps is not None:
        estimated_s = max(estimated_s, estimate_episode_duration_s(max_steps))
    episode_started_at = _now().timestamp()
    ctx.emitter.emit(
        "episode.started",
        {
            "run_id": ctx.run_id,
            "episode_id": episode_id,
            "estimated_duration_s": estimated_s,
            "max_steps": max_steps,
            "tool_call_id": tool_call_id,
        },
    )
    # Production: run.status = WAITING_TOOL ghi vào DB tại đây

    run_deadline = datetime.fromisoformat(state["deadline_at"])
    # Budget from episode start, not from the earlier capture/LLM portion of the run.
    episode_deadline = episode_started_at + estimated_s + 30.0
    if episode_deadline > run_deadline.timestamp():
        episode_deadline = run_deadline.timestamp()

    every = max(1, ctx.robot_state_poll_every)
    servo_recorder = asyncio.create_task(_record_servo_stream(ctx, episode_id, max_steps))
    try:
        return await _monitor_episode_loop(
            ctx,
            state,
            episode_id=episode_id,
            max_steps=max_steps,
            tool_call_id=tool_call_id,
            episode_started_at=episode_started_at,
            episode_deadline=episode_deadline,
            every=every,
        )
    finally:
        servo_recorder.cancel()
        try:
            await servo_recorder
        except asyncio.CancelledError:
            pass


async def _monitor_episode_loop(
    ctx: RunContext,
    state: dict,
    *,
    episode_id: str,
    max_steps: int | None,
    tool_call_id: str | None,
    episode_started_at: float,
    episode_deadline: float,
    every: int,
) -> ToolResult:
    poll_tick = 0
    robot_state_checks = 0
    final_status: dict = {}
    last_robot_state: dict | None = None

    while True:
        # Chờ 1 chu kỳ poll, thoát sớm nếu có cancel
        try:
            await asyncio.wait_for(
                ctx.cancel_event.wait(), timeout=ctx.episode_poll_interval_s
            )
        except TimeoutError:
            pass

        if ctx.cancel_event.is_set():
            await _call_simple(ctx, {"name": "abort", "args": {"task_id": episode_id}})
            final_status = {"status": "STOPPED", "reason": "cancel_requested"}
            break

        if _now().timestamp() >= episode_deadline:
            await _call_simple(ctx, {"name": "abort", "args": {"task_id": episode_id}})
            final_status = {"status": "TIMEOUT", "reason": "episode_deadline"}
            break

        # --- 1) Tiến độ VLA episode ---
        # Edge tự đếm số lần gọi check_status cho MỘT task_id và ép TIMEOUT
        # sau MAX_POLL lần (edge_vla/config.py) bất kể task_deadline/k còn dư
        # bao nhiêu — độc lập với episode_deadline ở trên. Gọi wait_s=0 (bị
        # clamp lên 1s, edge_vla.core.controller.WAIT_CLAMP) mỗi tick đốt hết
        # ngân sách MAX_POLL đó chỉ sau vài chục giây thực. Tăng dần wait_s
        # (tối đa clamp cho phép) để MỖI lần gọi bao phủ được nhiều thời gian
        # chờ thật hơn, cùng một ngân sách MAX_POLL đi được xa hơn nhiều —
        # đổi lại, độ trễ phản hồi khi người dùng bấm Dừng giữa episode có
        # thể tăng tới ngần ấy giây ở những tick sau (chấp nhận được so với
        # việc episode luôn bị cắt sớm bất kể cấu hình timeout phía trên).
        # Fast unit-test polls (episode_poll_interval_s << 1) must not jump to
        # a 5s edge wait — that finishes short mock episodes before safety checks.
        wait_step = 5.0 if ctx.episode_poll_interval_s >= 1.0 else max(ctx.episode_poll_interval_s, 0.05)
        poll_wait_s = min(15.0, wait_step * (poll_tick + 1))
        try:
            status_result = await _call_simple(
                ctx,
                {"name": "check_status", "args": {"task_id": episode_id, "wait_s": poll_wait_s}},
            )
        except AppError:
            continue  # lỗi poll tạm thời — thử lại chu kỳ sau

        status = status_result.data
        if max_steps is not None:
            status.setdefault("max_steps", max_steps)
        elif isinstance(status.get("total"), int):
            status.setdefault("max_steps", status["total"])
        poll_tick += 1
        elapsed_s = round(max(0.0, _now().timestamp() - episode_started_at), 1)

        # --- 2) Robot state định kỳ (an toàn vật lý) ---
        if poll_tick % every == 0:
            try:
                state_result = await _call_simple(
                    ctx, {"name": "get_robot_state", "args": {}}
                )
                if state_result.robot_state is not None:
                    last_robot_state = state_result.robot_state
                    robot_state_checks += 1
                    ui_status = ui_status_from_edge(last_robot_state)
                    ctx.emitter.emit(
                        "robot.state.updated",
                        {
                            "run_id": ctx.run_id,
                            "episode_id": episode_id,
                            "status": ui_status,
                            "busy": bool(last_robot_state.get("busy")),
                            "robot_status": ui_status,
                            "error_flags": last_robot_state.get("error_flags"),
                            "temperature_c": last_robot_state.get("temperature_c"),
                            "source": "episode_monitor",
                        },
                    )
                    reason = _is_robot_abnormal(
                        last_robot_state, ctx.robot_state_temp_limit_c
                    )
                    if reason:
                        await _call_simple(
                            ctx, {"name": "abort", "args": {"task_id": episode_id}}
                        )
                        final_status = {
                            "status": "SAFETY_ABORT",
                            "reason": reason,
                            "step": status.get("step"),
                        }
                        ctx.emitter.emit(
                            "episode.progress",
                            {
                                "run_id": ctx.run_id,
                                "episode_id": episode_id,
                                **status,
                                "elapsed_s": elapsed_s,
                                "robot_state": last_robot_state,
                                "safety_abort": reason,
                                "tool_call_id": tool_call_id,
                            },
                        )
                        break
            except AppError:
                pass  # state poll best-effort; vẫn theo dõi task_status

        ctx.emitter.emit(
            "episode.progress",
            {
                "run_id": ctx.run_id,
                "episode_id": episode_id,
                **status,
                "elapsed_s": elapsed_s,
                "robot_state": last_robot_state,
                "robot_state_checks": robot_state_checks,
                "tool_call_id": tool_call_id,
            },
        )
        if status.get("status") in _EPISODE_TERMINAL:
            final_status = status
            break

    ctx.emitter.emit(
        "episode.completed",
        {
            "run_id": ctx.run_id,
            "episode_id": episode_id,
            "status": final_status.get("status"),
            # `step`/`steps` — frontend (`trace-translate.ts`'s `episode.completed`
            # case) đọc cả hai tên, cần cho dòng "Hoàn thành sau N bước".
            "step": final_status.get("step"),
            "elapsed_s": round(max(0.0, _now().timestamp() - episode_started_at), 1),
            # status=DONE không tự nói thành công hay không (edge_vla dùng
            # DONE cho cả hai, phân biệt bằng `success` riêng) — frontend cần
            # trường này để suy ra đúng `EpisodeOutcome` (`toOutcome()`).
            "success": final_status.get("success"),
            "robot_state_checks": robot_state_checks,
            "tool_call_id": tool_call_id,
        },
    )

    release_if_idle(ctx.robot_id)

    # Trả ToolMessage cho LLM với outcome + final_state + ảnh sau khi xong (§2.7)
    images = []
    robot_state = last_robot_state
    try:
        state_result = await _call_simple(ctx, {"name": "get_robot_state", "args": {}})
        robot_state = state_result.robot_state or robot_state
        capture = await _call_simple(ctx, {"name": "capture", "args": {}})
        images = capture.images
    except AppError:
        pass  # ảnh/state sau episode là best-effort

    # `edge_vla.core.task_store`: DONE + success=True là thành công thật;
    # SUCCESS là tên trạng thái cũ (không phải thứ edge trả về, chỉ giữ để
    # tương thích một edge server khác nếu có). status=DONE mà success=False
    # là "chạy xong nhưng KHÔNG đạt" — không phải ok.
    ok = final_status.get("status") == "SUCCESS" or (
        final_status.get("status") == "DONE" and final_status.get("success") is True
    )
    return ToolResult(
        ok=ok,
        tool_name="take_action",
        data={
            "episode_id": episode_id,
            "episode": final_status,
            "robot_state_checks": robot_state_checks,
        },
        images=images,
        robot_state=robot_state,
        error=None
        if ok
        else ToolError(
            code="EDGE_EPISODE",
            message=f"episode kết thúc: {final_status.get('status')}",
            detail={"reason": final_status.get("reason")},
        ),
    )


async def _execute_one(ctx: RunContext, state: dict, call: dict) -> tuple[dict, ToolResult, ToolCallRecord]:
    started = _now()
    record: ToolCallRecord = {
        "tool_call_id": call.get("id") or "",
        "tool_name": call["name"],
        "arguments": call.get("args") or {},
        "status": "RUNNING",
        "started_at": _iso(started),
        "ended_at": None,
        "duration_ms": None,
        "error_code": None,
    }
    ctx.emitter.emit(
        "tool.started",
        {
            "run_id": ctx.run_id,
            "tool": call["name"],
            "id": call.get("id"),
            "args": call.get("args") or {},
        },
    )
    ctx.verbose.tool_start(call["name"], call.get("args") or {})
    is_motion_tool = call["name"] in {"take_action", "go_home"}
    if is_motion_tool:
        ctx.active_tool_name = call["name"]
        ctx.active_tool_args = dict(call.get("args") or {})

    if call["name"] == "go_home":
        mark_busy(ctx.robot_id)

    try:
        result = await _call_simple(ctx, call)
    except AppError as exc:
        result = _error_result(call["name"], exc)
    finally:
        if is_motion_tool and call["name"] != "take_action":
            ctx.active_tool_name = None
            ctx.active_tool_args = {}

    ended = _now()
    record["ended_at"] = _iso(ended)
    record["duration_ms"] = int((ended - started).total_seconds() * 1000)
    if result.ok:
        record["status"] = "SUCCEEDED"
        if call["name"] == "take_action":
            mark_busy(ctx.robot_id)
        if call["name"] == "go_home":
            release_if_idle(ctx.robot_id)
        # `take_action` KHÔNG emit `tool.completed` ở đây — `tool_node` gọi
        # `_monitor_episode` ngay sau `_execute_one` trả về, rồi tự emit event
        # này với kết quả CUỐI (đã theo dõi xong episode), không phải kết quả
        # "vừa khởi động task". Emit sớm ở đây sẽ đóng dòng trace trước khi
        # episode thật sự xong — hàng đóng ("HOÀN THÀNH") trong khi robot còn
        # đang chạy.
        if call["name"] != "take_action":
            ctx.emitter.emit(
                "tool.completed",
                {
                    "run_id": ctx.run_id,
                    "tool": call["name"],
                    "id": call.get("id"),
                    "duration_ms": record["duration_ms"],
                    "summary": _build_tool_summary(result),
                },
            )
    else:
        record["status"] = "TIMEOUT" if (result.error and result.error.code == "MCP_003") else "FAILED"
        record["error_code"] = result.error.code if result.error else None
        if call["name"] in MOTION_TOOLS:
            release_if_idle(ctx.robot_id)
        ctx.emitter.emit(
            "tool.failed",
            {
                "run_id": ctx.run_id,
                "tool": call["name"],
                "id": call.get("id"),
                "error": {
                    "code": result.error.code,
                    "message": result.error.message,
                }
                if result.error
                else None,
            },
        )

    # Call verbose logging for tool completion
    tool_output = result.data if result.ok else (result.error.to_dict() if hasattr(result.error, "to_dict") else str(result.error))
    ctx.verbose.tool_end(call["name"], tool_output, result.ok)
    if call["name"] == "take_action" and not result.ok:
        ctx.active_tool_name = None
        ctx.active_tool_args = {}

    return call, result, record


async def tool_node(state: dict, config: RunnableConfig) -> dict:
    ctx = get_run_context(config)
    last_message = state["messages"][-1]
    tool_calls: list[dict] = last_message.tool_calls

    batches = plan_tool_batches(tool_calls)
    planned_ids = {c.get("id") for batch in batches for c in batch}

    results: list[tuple[dict, ToolResult]] = []
    records: list[ToolCallRecord] = []

    # Tool bị loại khỏi plan (vd: batch có stop_robot huỷ mọi tool khác — §7.5)
    for call in tool_calls:
        if call.get("id") not in planned_ids:
            records.append(
                {
                    "tool_call_id": call.get("id") or "",
                    "tool_name": call["name"],
                    "arguments": call.get("args") or {},
                    "status": "CANCELLED",
                    "started_at": _iso(_now()),
                    "ended_at": _iso(_now()),
                    "duration_ms": 0,
                    "error_code": None,
                }
            )
            results.append(
                (
                    call,
                    ToolResult(
                        ok=False,
                        tool_name=call["name"],
                        error=ToolError(
                            code="MCP_004",
                            message="Tool bị huỷ vì cùng batch có stop_robot.",
                        ),
                    ),
                )
            )

    has_started_episode = False

    for batch in batches:
        if ctx.cancel_event.is_set():
            for call in batch:
                results.append(
                    (
                        call,
                        ToolResult(
                            ok=False,
                            tool_name=call["name"],
                            error=ToolError(code="MCP_004", message="Run đã bị huỷ."),
                        ),
                    )
                )
            continue

        executable_batch = []
        for call in batch:
            if call["name"] == "take_action" and has_started_episode:
                now_t = _iso(_now())
                records.append(
                    {
                        "tool_call_id": call.get("id") or "",
                        "tool_name": call["name"],
                        "arguments": call.get("args") or {},
                        "status": "FAILED",
                        "started_at": now_t,
                        "ended_at": now_t,
                        "duration_ms": 0,
                        "error_code": "MCP_004",
                    }
                )
                results.append(
                    (
                        call,
                        ToolResult(
                            ok=False,
                            tool_name=call["name"],
                            error=ToolError(
                                code="MCP_004",
                                message="Cannot start multiple take_action in the same turn.",
                            ),
                        ),
                    )
                )
                ctx.emitter.emit(
                    "tool.started",
                    {
                        "run_id": ctx.run_id,
                        "tool": call["name"],
                        "id": call.get("id"),
                        "args": call.get("args") or {},
                    },
                )
                ctx.emitter.emit(
                    "tool.failed",
                    {
                        "run_id": ctx.run_id,
                        "tool": call["name"],
                        "id": call.get("id"),
                        "error": {
                            "code": "MCP_004",
                            "message": "Cannot start multiple take_action in the same turn.",
                        },
                    },
                )
                ctx.verbose.tool_start(call["name"], call.get("args") or {})
                ctx.verbose.tool_end(call["name"], {"error": "Cannot start multiple take_action in the same turn."}, False)
            else:
                executable_batch.append(call)
                if call["name"] == "take_action":
                    has_started_episode = True

        outcomes = await asyncio.gather(
            *(_execute_one(ctx, state, call) for call in executable_batch)
        )
        for call, result, record in outcomes:
            if call["name"] == "take_action" and result.ok:
                # `_execute_one` chỉ khởi động episode (không blocking) và cố
                # tình KHÔNG emit `tool.completed` cho take_action (xem comment
                # ở đó) — theo dõi tới khi episode thật sự xong rồi mới trả
                # kết quả CUỐI cho LLM và emit event ở đây, thay vì để LLM tự
                # gọi `check_status` lặp lại (ADR-001, DOC 1 §2.7).
                max_steps = call.get("args", {}).get("k")
                monitor_started = _now()
                result = await _monitor_episode(
                    ctx, state, result, max_steps=max_steps, tool_call_id=call.get("id")
                )
                monitor_ended = _now()
                record["ended_at"] = _iso(monitor_ended)
                record["duration_ms"] = int(
                    (monitor_ended - monitor_started).total_seconds() * 1000
                ) + (record["duration_ms"] or 0)
                if result.ok:
                    record["status"] = "SUCCEEDED"
                    ctx.emitter.emit(
                        "tool.completed",
                        {
                            "run_id": ctx.run_id,
                            "tool": call["name"],
                            "id": call.get("id"),
                            "duration_ms": record["duration_ms"],
                            "summary": _build_tool_summary(result),
                        },
                    )
                else:
                    record["status"] = "FAILED"
                    record["error_code"] = result.error.code if result.error else None
                    ctx.emitter.emit(
                        "tool.failed",
                        {
                            "run_id": ctx.run_id,
                            "tool": call["name"],
                            "id": call.get("id"),
                            "error": {
                                "code": result.error.code,
                                "message": result.error.message,
                            }
                            if result.error
                            else None,
                        },
                    )
            results.append((call, result))
            records.append(record)

    return {
        "pending_tool_results": results,
        "tool_call_history": state.get("tool_call_history", []) + records,
        "cancel_requested": ctx.cancel_event.is_set() or state.get("cancel_requested", False),
    }
