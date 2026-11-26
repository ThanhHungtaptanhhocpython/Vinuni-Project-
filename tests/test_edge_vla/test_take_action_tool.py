import asyncio
import dataclasses
import time

import numpy as np

from src.edge_vla.embodiment.base import StepResult
from src.edge_vla.embodiment.spec import ActionSpace
from src.edge_vla.mcp_server.registry import TOOL_REGISTRY
from src.edge_vla.mcp_server.shared_state import SharedState
from src.edge_vla.models import RobotStatus, StopMode
from src.edge_vla.vla_runtime.policy import Policy, policy_registry
from tests.test_edge_vla.fake_config import fake_config
from tests.test_edge_vla.fakes import FakeEmbodiment, set_test_context


class _ZeroActionPolicy(Policy):
    """Fake `Policy` — không load checkpoint thật (nặng, cần GPU), chỉ trả action = 0 (khớp
    obs.state mặc định) để test episode loop/precondition mà không phụ thuộc model thật."""

    action_space = ActionSpace.JOINT_POSITION_DEG

    def reset(self) -> None:
        pass

    async def predict(self, obs) -> np.ndarray:
        return np.zeros(6, dtype=np.float32)


def _set_context(policy_name: str = "fake_policy") -> tuple[SharedState, FakeEmbodiment]:
    return set_test_context(config=fake_config(policy=policy_name))


def _register_fake_policy(policy_name: str) -> None:
    policy_registry._cache[policy_name] = _ZeroActionPolicy()


async def _wait_completed(episode_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    status = await TOOL_REGISTRY["get_task_status"]({"episode_id": episode_id}, "trc_test")
    while status["data"]["status"] != "COMPLETED" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        status = await TOOL_REGISTRY["get_task_status"]({"episode_id": episode_id}, "trc_test")
    return status


# --- tiền điều kiện (DOC 3 §4.3) ---


def test_take_action_rejects_empty_instruction():
    _set_context()

    result = asyncio.run(TOOL_REGISTRY["take_action"]({"instruction": ""}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "VAL_001"


def test_take_action_busy_returns_robot_010():
    shared_state, _embodiment = _set_context()
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_existing")

    result = asyncio.run(TOOL_REGISTRY["take_action"]({"instruction": "pick the block"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_010"


def test_take_action_error_status_returns_robot_020():
    shared_state, _embodiment = _set_context()
    shared_state.state_machine.transition(RobotStatus.BUSY, episode_id="epi_x")
    shared_state.state_machine.transition(RobotStatus.STOPPING, episode_id="epi_x")
    shared_state.state_machine.transition(RobotStatus.ERROR)

    result = asyncio.run(TOOL_REGISTRY["take_action"]({"instruction": "pick the block"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_020"


def test_take_action_missing_camera_frame_returns_robot_030():
    _shared_state, embodiment = _set_context()
    frames = embodiment.read_frames()
    del frames["wrist"]
    embodiment.read_frames = lambda: frames

    result = asyncio.run(TOOL_REGISTRY["take_action"]({"instruction": "pick the block"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_030"


def test_take_action_overheating_joint_returns_robot_021():
    _shared_state, embodiment = _set_context()
    embodiment.joints = [
        dataclasses.replace(joint, present_temperature=70.0) if i == 0 else joint
        for i, joint in enumerate(embodiment.joints)
    ]

    result = asyncio.run(TOOL_REGISTRY["take_action"]({"instruction": "pick the block"}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_021"


def test_take_action_unconfigured_policy_returns_robot_040(monkeypatch):
    _set_context("smolvla")  # ctx.config.vla.policy = "smolvla" — không khớp policy dưới đây
    monkeypatch.delenv("VLA_CHECKPOINT_PATH", raising=False)
    monkeypatch.delenv("VLA_CHECKPOINT_PATH_SOME_OTHER_POLICY", raising=False)

    result = asyncio.run(
        TOOL_REGISTRY["take_action"]({"instruction": "pick the block", "policy": "some_other_policy"}, "trc_test")
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_040"


# --- ACCEPTED + episode nền ---


def test_take_action_accepted_returns_episode_id_and_transitions_busy():
    policy_name = "fake_accept"
    _register_fake_policy(policy_name)
    shared_state, _embodiment = _set_context(policy_name)

    result = asyncio.run(
        TOOL_REGISTRY["take_action"]({"instruction": "pick the block", "max_steps": 3, "timeout_s": 30}, "trc_test")
    )

    assert result["ok"] is True
    assert result["data"]["status"] == "ACCEPTED"
    assert result["data"]["policy"] == policy_name
    assert shared_state.robot_status == RobotStatus.BUSY
    assert shared_state.current_episode_id == result["data"]["episode_id"]


def test_take_action_reaches_max_steps_and_returns_to_idle():
    policy_name = "fake_maxsteps"
    _register_fake_policy(policy_name)
    shared_state, _embodiment = _set_context(policy_name)

    async def _drive():
        accept = await TOOL_REGISTRY["take_action"](
            {"instruction": "pick the block", "max_steps": 3, "timeout_s": 30}, "trc_test"
        )
        return await _wait_completed(accept["data"]["episode_id"])

    status = asyncio.run(_drive())

    assert status["ok"] is True
    assert status["data"]["outcome"] == "MAX_STEPS"
    assert status["data"]["step"] == 3
    assert status["data"]["max_steps"] == 3
    assert shared_state.robot_status == RobotStatus.IDLE
    assert shared_state.current_episode_id is None


def test_take_action_stop_robot_mid_episode_ends_stopped():
    policy_name = "fake_stop"
    _register_fake_policy(policy_name)
    shared_state, _embodiment = _set_context(policy_name)

    async def _drive():
        accept = await TOOL_REGISTRY["take_action"](
            {"instruction": "pick the block", "max_steps": 1000, "timeout_s": 30}, "trc_test"
        )
        episode_id = accept["data"]["episode_id"]
        await asyncio.sleep(0.1)
        shared_state.request_stop(StopMode.GRACEFUL, "user_request")
        return await _wait_completed(episode_id)

    status = asyncio.run(_drive())

    assert status["ok"] is True
    assert status["data"]["outcome"] == "STOPPED"
    assert status["data"]["stop_reason"] == "user_request"
    assert shared_state.robot_status == RobotStatus.IDLE


def test_take_action_dry_run_does_not_call_apply_action():
    policy_name = "fake_dry_run"
    _register_fake_policy(policy_name)
    shared_state, embodiment = _set_context(policy_name)

    async def _drive():
        accept = await TOOL_REGISTRY["take_action"](
            {"instruction": "pick the block", "max_steps": 2, "timeout_s": 30, "dry_run": True}, "trc_test"
        )
        return await _wait_completed(accept["data"]["episode_id"])

    status = asyncio.run(_drive())

    assert status["ok"] is True
    assert status["data"]["outcome"] == "MAX_STEPS"
    assert embodiment.actions == []


def test_take_action_embodiment_success_signal_ends_success():
    """Đường mới, chỉ có ở embodiment có oracle (§sim): `StepResult.success=True` kết thúc
    episode ngay với outcome SUCCESS, không cần chờ max_steps."""
    policy_name = "fake_success"
    _register_fake_policy(policy_name)
    shared_state, embodiment = _set_context(policy_name)
    embodiment.next_step_result = StepResult(success=True)

    async def _drive():
        accept = await TOOL_REGISTRY["take_action"](
            {"instruction": "pick the block", "max_steps": 100, "timeout_s": 30}, "trc_test"
        )
        return await _wait_completed(accept["data"]["episode_id"])

    status = asyncio.run(_drive())

    assert status["ok"] is True
    assert status["data"]["outcome"] == "SUCCESS"
    assert shared_state.robot_status == RobotStatus.IDLE
