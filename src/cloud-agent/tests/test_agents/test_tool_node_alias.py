from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents.nodes.tool import _call_simple
from agents.tools.mcp_client import ToolResult


@pytest.mark.asyncio
@patch("agents.nodes.tool.asyncio.sleep", new_callable=AsyncMock)
async def test_call_simple_alias_mapping(mock_sleep):
    mock_ctx = MagicMock()
    mock_ctx.robot_id = "test_robot"
    mock_ctx.trace_id = "test_trace"

    # Mock call_tool to return a ToolResult with some arbitrary data
    mock_result = ToolResult(ok=True, tool_name="dummy", data={"k": "v"})
    mock_ctx.mcp.call_tool = AsyncMock(return_value=mock_result)

    # Test capture -> capture_tool
    capture_call = {"name": "capture", "args": {"detail_level": "high"}}
    res = await _call_simple(mock_ctx, capture_call)

    mock_ctx.mcp.call_tool.assert_called_with(
        robot_id="test_robot",
        tool_name="capture_tool",
        arguments={"detail_level": "high"},
        trace_id="test_trace",
        timeout_s=10.0,
        priority=5
    )
    # the returned result should have the original LLM-facing tool name
    assert res.tool_name == "capture"

    # Test check_status -> get_task_status
    check_status_call = {"name": "check_status", "args": {"task_id": "ep_1", "wait_s": 5}}
    res2 = await _call_simple(mock_ctx, check_status_call)

    mock_sleep.assert_called_once_with(5.0)

    mock_ctx.mcp.call_tool.assert_called_with(
        robot_id="test_robot",
        tool_name="get_task_status",
        arguments={"episode_id": "ep_1"},
        trace_id="test_trace",
        timeout_s=30.0,
        priority=5
    )
    # wait_s should be popped
    assert "wait_s" not in mock_ctx.mcp.call_tool.call_args.kwargs["arguments"]
    assert res2.tool_name == "check_status"
