import asyncio
from pathlib import Path

from src.edge_vla.mcp_server.registry import TOOL_REGISTRY
from tests.test_edge_vla.fakes import set_test_context


def test_capture_tool_returns_images_for_default_cameras():
    set_test_context()

    result = asyncio.run(TOOL_REGISTRY["capture_tool"]({}, "trc_test"))

    assert result["ok"] is True
    assert result["tool_name"] == "capture_tool"
    images = result["data"]["images"]
    assert {img["camera_id"] for img in images} == {"top", "wrist"}
    for img in images:
        assert img["format"] == "jpeg"
        assert img["width"] <= 512  # detail_level=low mặc định resize cạnh dài về 512
        assert img["thumbnail_b64"] is not None
        assert len(img["sha256"]) == 64
    assert result["data"]["capture_latency_ms"] >= 0


def test_capture_tool_detail_high_keeps_original_resolution():
    set_test_context()

    result = asyncio.run(
        TOOL_REGISTRY["capture_tool"]({"cameras": ["top"], "detail_level": "high"}, "trc_test")
    )

    assert result["ok"] is True
    # FakeEmbodiment.read_frames() luôn trả ảnh 320x240 (HxWx3 -> shape (240, 320, 3)).
    assert result["data"]["images"][0]["width"] == 320
    assert result["data"]["images"][0]["height"] == 240


def test_capture_tool_no_thumbnail_when_disabled():
    set_test_context()

    result = asyncio.run(
        TOOL_REGISTRY["capture_tool"]({"cameras": ["top"], "include_thumbnail": False}, "trc_test")
    )

    assert result["data"]["images"][0]["thumbnail_b64"] is None
    assert result["data"]["images"][0]["thumbnail_url"] is None


def test_capture_tool_missing_frame_returns_robot_030():
    _shared_state, embodiment = set_test_context()
    # capture_tool không còn khái niệm "frame cũ" — read_frames() trả ảnh mới nhất, không kèm
    # timestamp. Giả lập camera chưa có frame nào bằng cách cho read_frames() thiếu key đó.
    embodiment.read_frames = lambda: {}

    result = asyncio.run(TOOL_REGISTRY["capture_tool"]({"cameras": ["top"]}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "ROBOT_030"


def test_capture_tool_invalid_camera_id_returns_val_010():
    set_test_context()

    result = asyncio.run(TOOL_REGISTRY["capture_tool"]({"cameras": ["front"]}, "trc_test"))

    assert result["ok"] is False
    assert result["error"]["code"] == "VAL_010"


def test_capture_tool_writes_local_artifact_file():
    set_test_context()

    result = asyncio.run(TOOL_REGISTRY["capture_tool"]({"cameras": ["top"]}, "trc_test"))

    url = result["data"]["images"][0]["url"]
    assert url.startswith("file://")
    assert Path(url.removeprefix("file://")).exists()
