import asyncio
import dataclasses
import time

import numpy as np

from src.edge_vla.embodiment.base import EmbodimentError
from src.edge_vla.mcp_server.registry import TOOL_REGISTRY
from src.edge_vla.mcp_server.shared_state import SharedState
from src.edge_vla.models import RobotStatus
from tests.test_edge_vla.fakes import FakeEmbodiment, set_test_context


async def _wait_completed(episode_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    status = await TOOL_REGISTRY["get_task_status"]({"episode_id": episode_id}, "trc_test")
    while status["data"]["status"] != "COMPLETED" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        status = await TOOL_REGISTRY["get_task_status"]({"episode_id": episode_id}, "trc_test")
    return status


def test_go_home_busy_returns_robot_010():
    shared_state = SharedState()
    set_test_context(shared_state=shared_state)
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_existing")

    result = asyncio.run(TOOL_REGISTRY["go_home"]({"speed": "slow"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_010"


def test_go_home_error_returns_robot_020():
    shared_state = SharedState()
    set_test_context(shared_state=shared_state)
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_x")
    shared_state.state_machine.transition(RobotStatus.STOPPING, episode_id="epi_x")
    shared_state.state_machine.transition(RobotStatus.ERROR)

    result = asyncio.run(TOOL_REGISTRY["go_home"]({"speed": "slow"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_020"


def test_go_home_current_position_out_of_limit_returns_robot_024():
    _shared_state, embodiment = set_test_context()
    embodiment.in_limit = False

    result = asyncio.run(TOOL_REGISTRY["go_home"]({"speed": "slow"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_024"


def test_go_home_unsupported_returns_robot_011():
    embodiment = FakeEmbodiment(spec=dataclasses.replace(FakeEmbodiment().spec, supports_go_home=False))
    set_test_context(embodiment=embodiment)

    result = asyncio.run(TOOL_REGISTRY["go_home"]({"speed": "slow"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_011"


def test_go_home_reaches_home_and_completes_success():
    shared_state, embodiment = set_test_context()
    embodiment.home_position = np.full(embodiment.spec.action_dim, 10.0, dtype=np.float32)

    async def _drive() -> dict:
        accept = await TOOL_REGISTRY["go_home"]({"speed": "normal"}, "trc_test")
        assert accept["ok"] is True
        assert accept["data"]["status"] == "ACCEPTED"
        return await _wait_completed(accept["data"]["episode_id"])

    status = asyncio.run(_drive())

    assert status["ok"] is True
    assert status["data"]["status"] == "COMPLETED"
    assert status["data"]["outcome"] == "SUCCESS"
    assert shared_state.robot_status == RobotStatus.IDLE
    assert shared_state.current_episode_id is None
    assert np.array_equal(embodiment.positions, embodiment.home_position)


def test_go_home_failure_ends_with_timeout_outcome():
    """`go_home()` của embodiment tự áp deadline riêng — lỗi ở đây được coi như quá hạn, và
    tool phải đưa robot về trạng thái dừng an toàn qua đúng đường E-Stop (§4.8) thay vì bỏ
    mặc goal đang treo."""
    shared_state, embodiment = set_test_context()

    async def _fail_go_home() -> None:
        raise EmbodimentError("hardware fault")

    embodiment.go_home = _fail_go_home

    async def _drive() -> dict:
        accept = await TOOL_REGISTRY["go_home"]({"speed": "slow"}, "trc_test")
        return await _wait_completed(accept["data"]["episode_id"])

    status = asyncio.run(_drive())

    assert status["ok"] is True
    assert status["data"]["outcome"] == "TIMEOUT"
    assert shared_state.robot_status == RobotStatus.IDLE
