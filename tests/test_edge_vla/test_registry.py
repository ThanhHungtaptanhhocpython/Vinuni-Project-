import asyncio
import json

from src.edge_vla.mcp_server import context
from src.edge_vla.mcp_server.registry import DEFAULT_HOST, DEFAULT_PORT, TOOL_REGISTRY, create_mcp_server
from src.edge_vla.mcp_server.shared_state import SharedState
from tests.test_edge_vla.fakes import FakeEmbodiment, set_test_context


def _set_test_context() -> None:
    set_test_context()


def test_tool_registry_has_all_registered_tools():
    assert set(TOOL_REGISTRY.keys()) == {
        "get_robot_state",
        "health_check",
        "reset_error",
        "go_home",
        "get_task_status",
        "capture_tool",
        "take_action",
        "stop_robot",
    }


def test_create_mcp_server_registers_all_tools_with_fastmcp():
    _set_test_context()

    server = create_mcp_server(host=DEFAULT_HOST, port=DEFAULT_PORT)

    tools = asyncio.run(server.list_tools())
    tool_names = {t.name for t in tools}
    assert tool_names == set(TOOL_REGISTRY.keys())


def test_call_tool_via_mcp_server_returns_tool_result_envelope():
    """Gọi tool qua đúng đường FastMCP local (không gọi thẳng TOOL_REGISTRY) — verify điều
    kiện hoàn thành Phase 4: 'Gọi tool qua MCP local, trả đúng schema ToolResult'."""
    _set_test_context()
    server = create_mcp_server(host=DEFAULT_HOST, port=DEFAULT_PORT)

    content_blocks = asyncio.run(server.call_tool("health_check", {}))

    # FastMCP trả list[ContentBlock]; @server.tool trong registry.py trả thẳng dict ToolResult
    # (§3.5.5) từ TOOL_REGISTRY, FastMCP serialize thành TextContent JSON — parse lại để verify.
    structured = json.loads(content_blocks[0].text)
    assert structured["ok"] is True
    assert structured["tool_name"] == "health_check"
    assert "data" in structured
    assert "robot_state_summary" in structured
