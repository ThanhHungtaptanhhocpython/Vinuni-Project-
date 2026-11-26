import asyncio

from src.edge_vla.mcp_server import context
from src.edge_vla.mcp_server.registry import TOOL_REGISTRY
from src.edge_vla.mcp_server.shared_state import SharedState
from src.edge_vla.models import TaskOutcome
from tests.test_edge_vla.fakes import FakeEmbodiment, set_test_context


def _set_test_context() -> SharedState:
    shared_state, _embodiment = set_test_context()
    return shared_state


def test_get_task_status_unknown_episode_returns_robot_060():
    _set_test_context()

    result = asyncio.run(TOOL_REGISTRY["get_task_status"]({"episode_id": "epi_does_not_exist"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_060"


def test_get_task_status_running_episode():
    _set_test_context()
    ctx = context.get_context()
    ctx.episodes.start("epi_running", "go_home")

    result = asyncio.run(TOOL_REGISTRY["get_task_status"]({"episode_id": "epi_running"}, "trc_test"))

    assert result["ok"] is True
    assert result["data"]["status"] == "RUNNING"
    assert result["data"]["outcome"] is None


def test_get_task_status_completed_episode_reports_outcome():
    _set_test_context()
    ctx = context.get_context()
    ctx.episodes.start("epi_done", "go_home")
    ctx.episodes.finish("epi_done", TaskOutcome.SUCCESS, final_state={"status": "IDLE", "joints": []})

    result = asyncio.run(TOOL_REGISTRY["get_task_status"]({"episode_id": "epi_done"}, "trc_test"))

    assert result["ok"] is True
    assert result["data"]["status"] == "COMPLETED"
    assert result["data"]["outcome"] == "SUCCESS"
    assert result["data"]["final_state"] == {"status": "IDLE", "joints": []}
    assert result["data"]["ended_at"] is not None
