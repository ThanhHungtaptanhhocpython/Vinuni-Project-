"""Test VLM agent graph — dùng fake LLM (ctx.llm_invoke) và MockEdgeTransport,
không gọi OpenAI thật."""

import pytest
from agents.context import RunContext
from agents.events import RecordingEmitter
from agents.runner import run_agent
from agents.tools.mcp_client import McpClient
from agents.tools.mock_transport import MockEdgeTransport
from agents.tools.policies import plan_tool_batches
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def make_ctx(transport=None, llm_invoke=None) -> RunContext:
    transport = transport or MockEdgeTransport(episode_duration_s=0.2)
    return RunContext(
        run_id="run_test",
        session_id="ses_test",
        user_id="user_test",
        robot_id="robot_test",
        trace_id="trc_test",
        mcp=McpClient(transport),
        emitter=RecordingEmitter(),
        episode_poll_interval_s=0.05,
        llm_invoke=llm_invoke,
    )


def scripted_llm(responses: list[AIMessage]):
    """Fake LLM trả lần lượt các message trong danh sách.

    Copy message và xoá id để mô phỏng LLM thật (mỗi lần gọi trả message mới);
    nếu trùng id, add_messages sẽ replace thay vì append.
    """
    queue = list(responses)

    async def _invoke(messages, tools):
        message = queue.pop(0).model_copy(deep=True)
        message.id = None
        return message

    return _invoke


@pytest.mark.asyncio
async def test_completes_without_tool_calls():
    ctx = make_ctx(llm_invoke=scripted_llm([AIMessage("Xin chào, tôi sẵn sàng.")]))
    result = await run_agent("Chào bạn", ctx=ctx)

    assert result["final_status"] == "COMPLETED"
    assert result["error"] is None
    assert result["messages"][-1].content == "Xin chào, tôi sẵn sàng."
    assert "run.started" in ctx.emitter.names()


@pytest.mark.asyncio
async def test_tool_loop_capture_then_answer():
    ctx = make_ctx(
        llm_invoke=scripted_llm(
            [
                AIMessage(
                    "Để tôi quan sát hiện trạng trước.",
                    tool_calls=[{"name": "capture_tool", "args": {}, "id": "tc_1"}],
                ),
                AIMessage("Trên bàn có một khối màu đỏ."),
            ]
        )
    )
    result = await run_agent("Trên bàn có gì?", ctx=ctx)

    assert result["final_status"] == "COMPLETED"
    assert result["tool_call_count"] == 1
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    # Kết quả tool phải nằm trong khối untrusted [DOC 0 §6.7]
    text_part = tool_messages[0].content
    assert '<tool_result untrusted="true">' in text_part
    # Ảnh capture được đưa vào context và lưu artifacts
    image_messages = [
        m
        for m in result["messages"]
        if isinstance(m, HumanMessage)
        and isinstance(m.content, list)
        and any(p.get("type") == "image_url" for p in m.content if isinstance(p, dict))
    ]
    assert image_messages
    assert len(result["artifacts"]) == 2  # camera top + wrist
    assert result["scene_memory"]["requires_fresh_capture_before_motion"] is True
    assert result["scene_memory"]["recent_observations"][-1]["tool"] == "capture_tool"
    assert result["scene_memory"]["recent_observations"][-1]["image_count"] == 2
    assert "tool.completed" in ctx.emitter.names()


@pytest.mark.asyncio
async def test_take_action_is_monitored_to_completion_by_tool_node():
    ctx = make_ctx(
        llm_invoke=scripted_llm(
            [
                AIMessage(
                    "Bắt đầu nhặt khối đỏ.",
                    tool_calls=[
                        {
                            "name": "take_action",
                            "args": {"instruction": "nhặt khối đỏ bỏ vào hộp"},
                            "id": "tc_1",
                        }
                    ],
                ),
                AIMessage("Đã nhặt xong khối đỏ."),
            ]
        )
    )
    result = await run_agent("Nhặt khối đỏ bỏ vào hộp", ctx=ctx)

    assert result["final_status"] == "COMPLETED"
    assert result["take_action_count"] == 1
    events = ctx.emitter.names()
    # `tool_node` theo dõi episode tới khi xong (ADR-001) rồi mới emit
    # `tool.completed` với kết quả CUỐI — không còn để LLM tự poll
    # `check_status` lặp lại.
    assert "tool.completed" in events
    assert "episode.started" in events
    assert "episode.completed" in events
    # Sau episode, robot_state được cập nhật lại
    assert result["current_episode_id"].startswith("ep_")


@pytest.mark.asyncio
async def test_preflight_fails_when_robot_offline():
    ctx = make_ctx(
        transport=MockEdgeTransport(connected=False),
        llm_invoke=scripted_llm([]),
    )
    result = await run_agent("Chào bạn", ctx=ctx)

    assert result["final_status"] == "FAILED"
    assert result["error"]["code"] == "MCP_001"
    assert "run.failed" in ctx.emitter.names()


@pytest.mark.asyncio
async def test_guard_rejects_when_take_action_limit_exceeded():
    take_action_msg = AIMessage(
        "Thử lại lần nữa.",
        tool_calls=[
            {"name": "take_action", "args": {"instruction": "thử lại"}, "id": "tc_x"}
        ],
    )
    # LLM cứ đòi take_action mãi → vượt AGENT_MAX_TAKE_ACTION → guard_reject
    ctx = make_ctx(llm_invoke=scripted_llm([take_action_msg] * 10))
    result = await run_agent("Làm đi làm lại", ctx=ctx)

    assert result["final_status"] == "FAILED"
    assert result["error"]["code"] == "AGENT_011"
    assert result["take_action_count"] == ctx.settings.agent_max_take_action
    # Message cuối giải thích cho user bằng tiếng Việt
    assert "giới hạn an toàn" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_cancel_stops_run():
    async def cancel_then_answer(messages, tools):
        raise AssertionError("không được gọi LLM khi đã cancel")

    ctx = make_ctx(llm_invoke=cancel_then_answer)
    ctx.cancel_event.set()
    result = await run_agent("Nhặt khối đỏ", ctx=ctx)

    assert result["final_status"] == "CANCELLED"
    assert result["cancel_requested"] is True


def test_plan_tool_batches_rules():
    calls = [
        {"name": "capture_tool", "args": {}, "id": "1"},
        {"name": "get_robot_state", "args": {}, "id": "2"},
        {"name": "take_action", "args": {}, "id": "3"},
        {"name": "reset_error", "args": {}, "id": "4"},
    ]
    batches = plan_tool_batches(calls)
    # reset_error trước, read-only song song, motion cuối cùng
    assert batches[0][0]["name"] == "reset_error"
    assert {c["name"] for c in batches[1]} == {"capture_tool", "get_robot_state"}
    assert batches[-1][0]["name"] == "take_action"

    # stop_robot huỷ mọi tool khác trong batch
    batches = plan_tool_batches(calls + [{"name": "stop_robot", "args": {}, "id": "5"}])
    assert batches == [[{"name": "stop_robot", "args": {}, "id": "5"}]]
