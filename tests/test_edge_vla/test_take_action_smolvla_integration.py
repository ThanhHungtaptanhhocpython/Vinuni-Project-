"""Test tích hợp thật cho `take_action` — DOC 3 điều kiện hoàn thành Phase 6: 'Chạy 1 episode
end-to-end trên sim (mode=sim đầy đủ)'. Dùng checkpoint LeRobot thật (không mock `Policy`) —
`Embodiment` là `FakeEmbodiment` in-memory (đủ vì mục tiêu test này là verify VLA runtime +
episode loop chạy thật với checkpoint + GPU, không phải verify phần cứng/sim thật; phần đó đã
verify riêng ở `LiberoEmbodiment`/`So101Embodiment`).

Chạy chậm (load model ~15-20s) và cần checkpoint + GPU CUDA thật nên tự skip nếu môi trường
không có — set `VLA_CHECKPOINT_PATH_SMOLVLA` (hoặc `VLA_CHECKPOINT_PATH`) trỏ tới checkpoint
LeRobot chuẩn (SmolVLA hoặc bất kỳ loại nào tự train trên SO-101, 6D joint-space) để chạy test
này cục bộ:

    VLA_CHECKPOINT_PATH_SMOLVLA=/path/to/pretrained_model \\
        pytest tests/test_edge_vla/test_take_action_smolvla_integration.py -v --noconftest
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from src.edge_vla.mcp_server.registry import TOOL_REGISTRY
from src.edge_vla.models import RobotStatus
from tests.test_edge_vla.fake_config import fake_config
from tests.test_edge_vla.fakes import set_test_context

_CHECKPOINT_DIR = os.environ.get("VLA_CHECKPOINT_PATH_SMOLVLA") or os.environ.get("VLA_CHECKPOINT_PATH")


def _cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _CHECKPOINT_DIR or not Path(_CHECKPOINT_DIR).is_dir() or not _cuda_available(),
    reason=(
        "Cần set VLA_CHECKPOINT_PATH_SMOLVLA (hoặc VLA_CHECKPOINT_PATH) trỏ tới checkpoint "
        "LeRobot thật + GPU CUDA khả dụng — DOC 3 §5.1/§10.1"
    ),
)


def test_take_action_runs_one_episode_end_to_end_on_sim_with_real_checkpoint():
    shared_state, _embodiment = set_test_context(config=fake_config(policy="smolvla", device="cuda"))

    async def _drive() -> dict:
        accept = await TOOL_REGISTRY["take_action"](
            {"instruction": "pick up the paper and put it in the bin", "max_steps": 2, "timeout_s": 120},
            "trc_test",
        )
        assert accept["ok"] is True, accept
        episode_id = accept["data"]["episode_id"]

        deadline = time.monotonic() + 120.0
        status = await TOOL_REGISTRY["get_task_status"]({"episode_id": episode_id}, "trc_test")
        while status["data"]["status"] != "COMPLETED" and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            status = await TOOL_REGISTRY["get_task_status"]({"episode_id": episode_id}, "trc_test")
        return status

    status = asyncio.run(_drive())

    assert status["ok"] is True
    assert status["data"]["status"] == "COMPLETED"
    assert status["data"]["outcome"] == "MAX_STEPS"
    assert status["data"]["step"] == 2
    assert shared_state.robot_status == RobotStatus.IDLE
    assert shared_state.current_episode_id is None
