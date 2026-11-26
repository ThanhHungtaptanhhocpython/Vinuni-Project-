import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from agents.tools.mcp_client import ToolResult
from agents.tools.registry import mcp_to_langchain_tool
from langchain_core.tools import StructuredTool


@pytest.mark.asyncio
async def test_mcp_to_langchain_tool_check_status_alias():
    # Setup mock RunContext
    mock_ctx = MagicMock()
    mock_ctx.robot_id = "test_robot"
    mock_ctx.trace_id = "test_trace"
    mock_ctx.mcp.call_tool = AsyncMock(return_value=ToolResult(ok=True, tool_name="get_task_status", data={}))

    # Create the check_status synthetic capability definition
    mcp_tool = {
        "name": "check_status",
        "_real_name": "get_task_status",
        "description": "Check task status blocking",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "wait_s": {"type": "integer", "default": 0}
            },
            "required": ["task_id"]
        }
    }

    # Convert to LangChain tool
    tool: StructuredTool = mcp_to_langchain_tool(mcp_tool, mock_ctx)

    # Mock asyncio.sleep to not actually wait during the test
    original_sleep = asyncio.sleep
    asyncio.sleep = AsyncMock()
    try:
        # Call the tool with task_id and wait_s
        await tool.coroutine(task_id="ep_123", wait_s=5)

        # Verify sleep was called
        asyncio.sleep.assert_called_once_with(5.0)

        # Verify the underlying MCP client was called with episode_id instead of task_id
        # and wait_s was not passed, and timeout included wait_s (5.0 + 5.0)
        mock_ctx.mcp.call_tool.assert_called_once_with(
            robot_id="test_robot",
            tool_name="get_task_status",
            arguments={"episode_id": "ep_123"},
            trace_id="test_trace",
            timeout_s=10.0
        )
    finally:
        asyncio.sleep = original_sleep

@pytest.mark.asyncio
async def test_mcp_to_langchain_tool_capture_alias():
    # Setup mock RunContext
    mock_ctx = MagicMock()
    mock_ctx.robot_id = "test_robot"
    mock_ctx.trace_id = "test_trace"
    mock_ctx.mcp.call_tool = AsyncMock(return_value=ToolResult(ok=True, tool_name="capture_tool", data={}))

    # Create the capture synthetic capability definition
    mcp_tool = {
        "name": "capture",
        "_real_name": "capture_tool",
        "description": "Capture images",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }

    # Convert to LangChain tool
    tool: StructuredTool = mcp_to_langchain_tool(mcp_tool, mock_ctx)

    # Call the tool
    await tool.coroutine()

    # Verify the underlying MCP client was called with real_name capture_tool
    mock_ctx.mcp.call_tool.assert_called_once_with(
        robot_id="test_robot",
        tool_name="capture_tool",
        arguments={},
        trace_id="test_trace",
        timeout_s=10.0
    )
