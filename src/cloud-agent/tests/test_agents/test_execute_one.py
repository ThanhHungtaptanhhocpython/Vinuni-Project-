from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents.nodes.tool import _execute_one, tool_node
from agents.tools.mcp_client import ToolResult


@pytest.mark.asyncio
@patch("agents.nodes.tool.mark_busy")
@patch("agents.nodes.tool.release_if_idle")
@patch("agents.nodes.tool._call_simple", new_callable=AsyncMock)
@patch("agents.nodes.tool._monitor_episode", new_callable=AsyncMock)
async def test_execute_one_take_action_no_monitor(
    mock_monitor, mock_call_simple, _release, _busy
):
    mock_ctx = MagicMock()
    mock_ctx.run_id = "test_run"
    mock_ctx.robot_id = "test_robot"
    mock_ctx.verbose = MagicMock()
    mock_ctx.emitter = MagicMock()

    mock_result = ToolResult(ok=True, tool_name="take_action", data={"k": "v"})
    mock_call_simple.return_value = mock_result

    call = {"name": "take_action", "args": {"instruction": "nhặt vật"}}
    state = {}

    await _execute_one(mock_ctx, state, call)

    # _call_simple should be called once for take_action
    mock_call_simple.assert_called_once_with(mock_ctx, call)

    # _monitor_episode should NOT be called anymore
    mock_monitor.assert_not_called()

@pytest.mark.asyncio
@patch("agents.nodes.tool.mark_busy")
@patch("agents.nodes.tool.release_if_idle")
@patch("agents.nodes.tool._call_simple", new_callable=AsyncMock)
@patch("agents.nodes.tool._monitor_episode", new_callable=AsyncMock)
async def test_tool_node_multiple_take_action_fails(
    mock_monitor, mock_call_simple, _release, _busy
):
    mock_ctx = MagicMock()
    mock_ctx.run_id = "test_run"
    mock_ctx.robot_id = "test_robot"
    mock_ctx.cancel_event.is_set.return_value = False

    with patch("agents.nodes.tool.get_run_context", return_value=mock_ctx):
        state = {
            "messages": [
                MagicMock(tool_calls=[
                    {"name": "take_action", "args": {"instruction": "1"}, "id": "tc_1"},
                    {"name": "take_action", "args": {"instruction": "2"}, "id": "tc_2"},
                ])
            ],
            "tool_call_history": []
        }

        # Mock first call to succeed (edge's immediate "task accepted" reply)
        mock_call_simple.return_value = ToolResult(ok=True, tool_name="take_action", data={"ep": "1"})
        # `tool_node` monitors the episode to completion itself (ADR-001) —
        # the LLM gets the FINAL outcome, not the immediate "accepted" reply.
        mock_monitor.return_value = ToolResult(ok=True, tool_name="take_action", data={"status": "DONE", "success": True})

        # We need to mock RunnableConfig which is passed to tool_node
        config = MagicMock()

        result = await tool_node(state, config)

        assert len(result["pending_tool_results"]) == 2

        # First call succeeds — result comes from the monitored (final) outcome
        assert result["pending_tool_results"][0][1].ok is True
        assert result["pending_tool_results"][0][1].data["status"] == "DONE"
        mock_monitor.assert_called_once()

        # Second call fails with MCP_004 because has_started_episode is True
        assert result["pending_tool_results"][1][1].ok is False
        assert result["pending_tool_results"][1][1].error.code == "MCP_004"
