import pytest
from agents.context import RunContext
from agents.events import RecordingEmitter
from agents.runner import run_agent
from agents.tools.mcp_client import McpClient
from agents.tools.mock_transport import MockEdgeTransport
from models.schemas import RunStatus


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

@pytest.mark.asyncio
async def test_political_deflection_stops_pre_llm():
    async def fail_if_called(*args, **kwargs):
        pytest.fail("LLM should not be called for political topics.")

    ctx = make_ctx(llm_invoke=fail_if_called)
    result = await run_agent("quan điểm của bạn về bầu cử tổng thống là gì?", ctx=ctx)

    assert result["final_status"] == RunStatus.COMPLETED or result["final_status"] == "COMPLETED"
    assert "Tôi chỉ hỗ trợ vận hành và điều khiển robot" in result["messages"][-1].content
    assert result.get("tool_call_count", 0) == 0

@pytest.mark.asyncio
async def test_language_consistency_rewrite():
    call_count = 0

    async def mock_llm_invoke(messages, tools=None):
        nonlocal call_count
        call_count += 1
        from langchain_core.messages import AIMessage

        # If it's the rewrite prompt
        if len(messages) > 0 and isinstance(messages[-1].content, str) and "Dịch hoặc viết lại văn bản" in messages[-1].content:
            return AIMessage(content="Tôi sẽ bắt đầu gắp vật. Quá trình này sẽ mất một chút thời gian.")

        # Normal agent response
        return AIMessage(content="Tôi will gắp vật now. Please đợi một chút, the process will take some time.")

    ctx = make_ctx(llm_invoke=mock_llm_invoke)
    result = await run_agent("Gắp vật cho tôi", ctx=ctx)

    final_text = result["messages"][-1].content
    assert "Tôi sẽ bắt đầu gắp vật" in final_text
    assert "will" not in final_text
    assert call_count >= 2
