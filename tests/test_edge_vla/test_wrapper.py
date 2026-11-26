import asyncio

from src.edge_vla.mcp_server import context
from src.edge_vla.mcp_server.errors import AppError, SafetyAbortError
from src.edge_vla.mcp_server.shared_state import SharedState
from src.edge_vla.mcp_server.tools._wrapper import mcp_tool
from tests.test_edge_vla.fakes import FakeEmbodiment, set_test_context


def _set_test_context() -> SharedState:
    shared_state, _embodiment = set_test_context()
    return shared_state



def test_wrapper_ok_path_returns_tool_result_envelope():
    _set_test_context()

    @mcp_tool("dummy_tool", "1.0", 5.0)
    async def dummy_tool(value: int) -> dict:
        return {"doubled": value * 2}

    result = asyncio.run(dummy_tool({"value": 3}, "trc_test"))

    assert result["ok"] is True
    assert result["tool_name"] == "dummy_tool"
    assert result["trace_id"] == "trc_test"
    assert result["data"] == {"doubled": 6}
    assert result["error"] is None
    assert result["robot_state_summary"]["status"] == "IDLE"
    assert isinstance(result["duration_ms"], int)


def test_wrapper_app_error_sets_ok_false_and_error_object():
    _set_test_context()

    @mcp_tool("dummy_tool", "1.0", 5.0)
    async def dummy_tool() -> dict:
        raise AppError("ROBOT_010", detail={"foo": "bar"})

    result = asyncio.run(dummy_tool({}, "trc_test"))

    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == "ROBOT_010"
    assert result["error"]["detail"] == {"foo": "bar"}
    assert result["error"]["trace_id"] == "trc_test"


def test_wrapper_safety_abort_error_maps_to_robot_053():
    _set_test_context()

    @mcp_tool("dummy_tool", "1.0", 5.0)
    async def dummy_tool() -> dict:
        raise SafetyAbortError(message="quá nhiệt")

    result = asyncio.run(dummy_tool({}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_053"
    assert result["error"]["retryable"] is False


def test_wrapper_timeout_maps_to_robot_002():
    _set_test_context()

    @mcp_tool("dummy_tool", "1.0", 0.01)
    async def dummy_tool() -> dict:
        await asyncio.sleep(1.0)
        return {}

    result = asyncio.run(dummy_tool({}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_002"


def test_wrapper_unhandled_exception_maps_to_sys_001():
    _set_test_context()

    @mcp_tool("dummy_tool", "1.0", 5.0)
    async def dummy_tool() -> dict:
        raise ValueError("boom")

    result = asyncio.run(dummy_tool({}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "SYS_001"
    assert result["error"]["detail"]["exc"] == "boom"
