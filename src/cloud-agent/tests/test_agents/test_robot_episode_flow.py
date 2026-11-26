"""Full-flow episode test — mô phỏng một lượt điều khiển cánh tay robot.

Scenario (DOC 1 §2.3 quy trình bắt buộc + monitor §2.7):
  1. preflight: robot online, nạp tools
  2. capture_tool + get_robot_state (quan sát trước hành động vật lý)
  3. take_action → Edge ACCEPTED episode → tool_node poll:
       - get_task_status (tiến độ VLA)
       - get_robot_state định kỳ (error_flags / nhiệt độ) trong lúc arm đang chạy
  4. LLM nhận outcome + ảnh after, tóm tắt cho user

Không gọi OpenAI — scripted LLM + MockEdgeTransport.
"""

from __future__ import annotations

import json

import pytest
from agents.context import RunContext
from agents.events import RecordingEmitter
from agents.runner import run_agent
from agents.tools.mcp_client import McpClient
from agents.tools.mock_transport import MockEdgeTransport
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _make_ctx(
    *,
    episode_duration_s: float = 0.25,
    poll_s: float = 0.05,
    robot_state_poll_every: int = 1,
    transport: MockEdgeTransport | None = None,
    llm_invoke,
) -> tuple[RunContext, MockEdgeTransport]:
    transport = transport or MockEdgeTransport(episode_duration_s=episode_duration_s)
    ctx = RunContext(
        run_id="run_ep_full",
        session_id="ses_ep_full",
        user_id="user_ep",
        robot_id="robot_so101",
        trace_id="trc_ep_full",
        source="CHAT",
        mcp=McpClient(transport),
        emitter=RecordingEmitter(),
        episode_poll_interval_s=poll_s,
        robot_state_poll_every=robot_state_poll_every,
        llm_invoke=llm_invoke,
    )
    return ctx, transport


def _scripted_llm(responses: list[AIMessage]):
    queue = list(responses)

    async def _invoke(messages, tools):
        assert tools, "agent phải bind tools sau preflight"
        tool_names = {t.name for t in tools}
        # Tool registry: get_task_status không bind (chỉ monitor nội bộ)
        assert "get_task_status" not in tool_names
        assert "take_action" in tool_names
        assert "capture_tool" in tool_names

        message = queue.pop(0).model_copy(deep=True)
        message.id = None
        return message

    return _invoke


def _tool_payload(msg: ToolMessage) -> dict:
    """Parse khối <tool_result untrusted> trong ToolMessage."""
    parts = msg.content if isinstance(msg.content, list) else [{"type": "text", "text": msg.content}]
    text = next(p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text")
    assert '<tool_result untrusted="true">' in text
    body = text.split(">", 1)[1].rsplit("</tool_result>", 1)[0].strip()
    return json.loads(body)


@pytest.mark.asyncio
async def test_full_robot_arm_episode_flow():
    """End-to-end: quan sát → take_action (poll episode) → tóm tắt."""

    llm = _scripted_llm(
        [
            # Vòng 1 — quan sát bắt buộc trước hành động vật lý
            AIMessage(
                content="Đang kiểm tra hiện trạng robot trước khi nhặt vật.",
                tool_calls=[
                    {
                        "name": "capture_tool",
                        "args": {"cameras": ["top", "wrist"], "detail_level": "low"},
                        "id": "tc_cap_1",
                    },
                    {
                        "name": "get_robot_state",
                        "args": {},
                        "id": "tc_state_1",
                    },
                ],
            ),
            # Vòng 2 — sau khi thấy IDLE + ảnh, ra lệnh VLA
            AIMessage(
                content="Robot IDLE, đã thấy đối tượng. Bắt đầu nhặt khối đỏ bỏ vào hộp.",
                tool_calls=[
                    {
                        "name": "take_action",
                        "args": {
                            "instruction": "nhặt khối đỏ bỏ vào hộp",
                            "max_duration_s": 30,
                        },
                        "id": "tc_act_1",
                    },
                ],
            ),
            # Vòng 3 — episode SUCCESS → tóm tắt cho user
            AIMessage(
                content=(
                    "Đã hoàn thành: nhặt khối đỏ và thả vào hộp. "
                    "Robot trở về IDLE, không có error_flags."
                ),
            ),
        ]
    )
    ctx, transport = _make_ctx(
        episode_duration_s=0.3,
        poll_s=0.05,
        robot_state_poll_every=1,
        llm_invoke=llm,
    )

    result = await run_agent(
        "Nhặt khối đỏ trên bàn bỏ vào hộp.",
        ctx=ctx,
    )

    # --- Run outcome ---
    assert result["final_status"] == "COMPLETED"
    assert result["error"] is None
    assert result["robot_id"] == "robot_so101"
    assert result["iteration"] == 3
    assert result["tool_call_count"] == 3  # capture + state + take_action
    assert result["take_action_count"] == 1
    assert result["robot_state"]["status"] == "IDLE"
    assert result["robot_state"]["error_flags"] == []

    # --- Messages timeline ---
    msgs = result["messages"]
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 3

    # capture: text untrusted; images live on the following HumanMessage
    cap_msg = next(m for m in tool_msgs if m.tool_call_id == "tc_cap_1")
    cap = _tool_payload(cap_msg)
    assert cap["ok"] is True
    assert cap["tool"] == "capture_tool"
    assert set(cap["data"]["cameras"]) == {"top", "wrist"}
    cap_idx = msgs.index(cap_msg)
    assert isinstance(msgs[cap_idx + 1], HumanMessage)
    image_parts = [
        p
        for p in msgs[cap_idx + 1].content
        if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    assert len(image_parts) == 2

    # get_robot_state trước action: IDLE
    state_msg = next(m for m in tool_msgs if m.tool_call_id == "tc_state_1")
    state_before = _tool_payload(state_msg)
    assert state_before["ok"] is True
    assert state_before["robot_state"]["status"] == "IDLE"

    # take_action: episode SUCCESS + post-episode capture images (HumanMessage)
    act_msg = next(m for m in tool_msgs if m.tool_call_id == "tc_act_1")
    act = _tool_payload(act_msg)
    assert act["ok"] is True
    assert act["tool"] == "take_action"
    assert act["data"]["episode"]["status"] == "SUCCESS"
    assert act["data"]["episode_id"].startswith("ep_")
    # Ít nhất 1 lần get_robot_state trong vòng monitor (poll định kỳ)
    assert act["data"]["robot_state_checks"] >= 1
    assert act["robot_state"]["status"] == "IDLE"
    act_idx = msgs.index(act_msg)
    assert isinstance(msgs[act_idx + 1], HumanMessage)
    post_images = [
        p
        for p in msgs[act_idx + 1].content
        if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    assert len(post_images) >= 2  # capture sau episode

    # Final assistant reply (no tool_calls)
    assert isinstance(msgs[-1], AIMessage)
    assert not msgs[-1].tool_calls
    assert "khối đỏ" in msgs[-1].content.lower() or "hoàn thành" in msgs[-1].content.lower()

    # --- Tool call history ---
    history = result["tool_call_history"]
    by_name = {r["tool_name"]: r for r in history}
    assert by_name["capture_tool"]["status"] == "SUCCEEDED"
    assert by_name["get_robot_state"]["status"] == "SUCCEEDED"
    assert by_name["take_action"]["status"] == "SUCCEEDED"
    assert by_name["take_action"]["duration_ms"] is not None
    assert by_name["take_action"]["duration_ms"] >= 0

    # Artifacts: pre-action capture (2) + post-episode capture (2)
    assert len(result["artifacts"]) >= 4

    # --- SSE / event sequence (subset, order matter for core milestones) ---
    events = ctx.emitter.names()
    assert "run.started" in events
    assert "tool.started" in events
    assert "tool.completed" in events
    assert "episode.started" in events
    assert "episode.progress" in events  # ít nhất 1 lần poll
    assert "episode.completed" in events
    assert "message.completed" in events
    assert "robot.state.updated" in events

    # robot.state.updated từ episode_monitor (không chỉ observe cuối)
    monitor_state_events = [
        d
        for n, d in ctx.emitter.events
        if n == "robot.state.updated" and d.get("source") == "episode_monitor"
    ]
    assert len(monitor_state_events) >= 1
    assert "temperature_c" in monitor_state_events[0]

    # Mock: get_robot_state được gọi nhiều hơn 1 (preflight / before / during / after)
    assert transport.call_counts.get("get_robot_state", 0) >= 2
    assert transport.call_counts.get("get_task_status", 0) >= 1

    # episode.completed nằm sau episode.started
    assert events.index("episode.started") < events.index("episode.completed")

    # payload episode.completed
    ep_done = next(d for n, d in ctx.emitter.events if n == "episode.completed")
    assert ep_done["status"] == "SUCCESS"
    assert ep_done["episode_id"].startswith("ep_")
    assert ep_done["robot_state_checks"] >= 1

    # usage counters
    assert ctx.llm_usage["calls"] == 0  # scripted hook không ghi usage OpenAI
    assert ctx.degraded is False


@pytest.mark.asyncio
async def test_episode_safety_abort_on_error_flags_during_monitor():
    """Giữa episode, get_robot_state thấy error_flags → stop + SAFETY_ABORT."""

    transport = MockEdgeTransport(episode_duration_s=2.0)
    responses = [
        AIMessage(
            content="Bắt đầu nhặt.",
            tool_calls=[
                {
                    "name": "take_action",
                    "args": {"instruction": "nhặt vật"},
                    "id": "tc_unsafe",
                }
            ],
        ),
        AIMessage(content="Đã dừng vì robot báo lỗi an toàn."),
    ]
    queue = list(responses)

    async def llm(messages, tools):
        msg = queue.pop(0).model_copy(deep=True)
        msg.id = None
        return msg

    ctx, transport = _make_ctx(
        episode_duration_s=2.0,
        poll_s=0.05,
        robot_state_poll_every=1,
        transport=transport,
        llm_invoke=llm,
    )

    original_emit = ctx.emitter.emit
    state_checks = {"n": 0}

    def emit_and_fault(event: str, data: dict | None = None) -> None:
        original_emit(event, data or {})
        # Sau lần robot.state.updated thứ 1 từ monitor → inject error
        if (
            event == "robot.state.updated"
            and (data or {}).get("source") == "episode_monitor"
        ):
            state_checks["n"] += 1
            if state_checks["n"] == 1:
                transport.inject_error_flags(["OVERTEMP", "COLLISION"])

    ctx.emitter.emit = emit_and_fault  # type: ignore[method-assign]

    result = await run_agent("Nhặt vật", ctx=ctx)

    ep = next(d for n, d in ctx.emitter.events if n == "episode.completed")
    assert ep["status"] == "SAFETY_ABORT"
    assert ep["robot_state_checks"] >= 1

    act_msg = next(
        m
        for m in result["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id == "tc_unsafe"
    )
    act = _tool_payload(act_msg)
    assert act["ok"] is False
    assert act["data"]["episode"]["status"] == "SAFETY_ABORT"
    assert "error_flags" in act["data"]["episode"]["reason"]
    assert result["robot_state"]["status"] == "IDLE"


@pytest.mark.asyncio
async def test_episode_cancelled_mid_run_stops_robot():
    """Cancel trong lúc episode đang chạy → stop_robot, episode STOPPED."""

    # LLM lần 1: take_action; sau đó không được gọi thêm nếu cancel set trong monitor
    responses = [
        AIMessage(
            content="Bắt đầu thao tác.",
            tool_calls=[
                {
                    "name": "take_action",
                    "args": {"instruction": "nhặt vật"},
                    "id": "tc_long",
                }
            ],
        ),
        AIMessage(content="Thao tác đã bị huỷ."),
    ]
    queue = list(responses)
    call_count = {"n": 0}

    async def llm_with_cancel_after_start(messages, tools):
        call_count["n"] += 1
        message = queue.pop(0).model_copy(deep=True)
        message.id = None
        return message

    # Episode dài hơn poll → cancel trong monitor
    ctx, _transport = _make_ctx(
        episode_duration_s=2.0,
        poll_s=0.05,
        llm_invoke=llm_with_cancel_after_start,
    )

    # Set cancel ngay sau lần LLM đầu (tool_node monitor sẽ thấy)
    # Dùng side-effect: patch emitter để cancel khi episode.started
    original_emit = ctx.emitter.emit

    def emit_and_cancel(event: str, data: dict | None = None) -> None:
        original_emit(event, data)
        if event == "episode.started":
            ctx.cancel_event.set()

    ctx.emitter.emit = emit_and_cancel  # type: ignore[method-assign]

    result = await run_agent("Nhặt vật", ctx=ctx)

    # Tool result take_action FAIL/STOPPED; agent có thể COMPLETED sau reply
    assert "episode.started" in ctx.emitter.names()
    assert "episode.completed" in ctx.emitter.names()
    ep = next(d for n, d in ctx.emitter.events if n == "episode.completed")
    assert ep["status"] == "STOPPED"

    act = next(
        r for r in result["tool_call_history"] if r["tool_name"] == "take_action"
    )
    # Tool record: FAILED hoặc SUCCEEDED=false for STOPPED outcome
    assert act["status"] in {"FAILED", "SUCCEEDED", "CANCELLED"}
    # Robot phải IDLE sau stop
    assert result["robot_state"]["status"] == "IDLE"
