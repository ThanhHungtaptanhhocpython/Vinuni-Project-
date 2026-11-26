import asyncio
import dataclasses
import math

import numpy as np

from src.edge_vla.mcp_server.registry import TOOL_REGISTRY
from tests.test_edge_vla.fake_config import CALIBRATION_PROFILE
from tests.test_edge_vla.fakes import FakeEmbodiment, set_test_context


def _configured_embodiment() -> FakeEmbodiment:
    embodiment = FakeEmbodiment()
    # `read_state()` ghi đè `present_position` từ `positions` — set cả hai để khớp giá trị test.
    embodiment.positions = np.array([10.0 + i for i in range(embodiment.spec.action_dim)], dtype=np.float32)
    embodiment.joints = [
        dataclasses.replace(joint, present_temperature=36.0 + i, present_current=30.0, present_voltage=11.9, present_load=-1.0)
        for i, joint in enumerate(embodiment.joints)
    ]
    embodiment.read_frames = lambda: {}  # chưa có camera source
    return embodiment


def test_get_robot_state_returns_full_robot_state_envelope():
    embodiment = _configured_embodiment()
    set_test_context(embodiment=embodiment)

    result = asyncio.run(TOOL_REGISTRY["get_robot_state"]({"include": None, "unit": "degree"}, "trc_test"))

    assert result["ok"] is True
    assert result["tool_name"] == "get_robot_state"
    data = result["data"]
    assert data["status"] == "IDLE"
    assert data["model"] == "so101"
    assert data["backend_mode"] == "real"
    assert len(data["joints"]) == 6
    assert [j["name"] for j in data["joints"]] == list(embodiment.spec.joint_names)
    assert data["joints"][0]["present_position"] == 10.0
    assert data["bus"]["ok"] is True
    assert data["bus"]["port"] == "/dev/ttyACM0"
    assert data["safety"]["temperature_threshold_c"] == 65.0
    assert data["calibration"]["profile"] == CALIBRATION_PROFILE
    assert len(data["cameras"]) == 2
    assert all(cam["ok"] is False for cam in data["cameras"])  # chưa có camera source


def test_get_robot_state_radian_unit_converts_angles():
    embodiment = _configured_embodiment()
    set_test_context(embodiment=embodiment)

    result = asyncio.run(TOOL_REGISTRY["get_robot_state"]({"include": ["joints"], "unit": "radian"}, "trc_test"))

    data = result["data"]
    assert data["joints"][0]["present_position"] == math.radians(10.0)
    assert data["cameras"] == []


def test_get_robot_state_include_errors_only_still_returns_envelope():
    set_test_context()

    result = asyncio.run(TOOL_REGISTRY["get_robot_state"]({"include": ["errors"], "unit": "degree"}, "trc_test"))

    assert result["ok"] is True
    assert result["data"]["joints"] == []
    assert result["data"]["cameras"] == []
