"""MockEdgeTransport — giả lập Edge MCP Server cho dev/test.

Cho phép chạy agent end-to-end khi chưa có robot thật: capture trả ảnh 1x1
(data URL hợp lệ với OpenAI vision), take_action trả episode chạy nền và
tiến tới SUCCESS sau vài giây.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

# PNG 1x1 xám — đủ để OpenAI vision chấp nhận data URL trong môi trường dev
_TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNsaGj4DwAF"
    "hAKAsLxYfgAAAABJRU5ErkJggg=="
)

_CAPABILITIES: list[dict] = [
    {
        "name": "capture_tool",
        "version": "1.0.0",
        "description": "Chụp ảnh từ camera của robot để quan sát hiện trạng.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cameras": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Danh sách camera: top, wrist",
                    "default": ["top", "wrist"],
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["low", "high"],
                    "default": "low",
                    "description": "Độ phân giải ảnh đưa vào LLM",
                },
            },
        },
    },
    {
        "name": "get_robot_state",
        "version": "1.0.0",
        "description": "Đọc trạng thái hiện tại của robot (status, joints, error_flags).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_servo_state",
        "version": "1.0.0",
        "description": "Đọc snapshot servo/joints hiện tại để replay và vẽ telemetry.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "take_action",
        "version": "1.0.0",
        "description": "Ra lệnh cho mô hình VLA thực thi một nhiệm vụ thao tác.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Mệnh lệnh ngôn ngữ tự nhiên cho VLA, vd: 'nhặt khối đỏ bỏ vào hộp'",
                },
                "max_duration_s": {"type": "integer", "default": 60},
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "stop_robot",
        "version": "1.0.0",
        "description": "Dừng robot ngay lập tức.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["GRACEFUL", "EMERGENCY"], "default": "GRACEFUL"}
            },
        },
    },
    {
        "name": "get_task_status",
        "version": "1.0.0",
        "description": "Kiểm tra tiến độ của một episode đang chạy.",
        "inputSchema": {
            "type": "object",
            "properties": {"episode_id": {"type": "string"}},
            "required": ["episode_id"],
        },
    },
    {
        "name": "health_check",
        "version": "1.0.0",
        "description": "Kiểm tra kết nối và sức khoẻ của Edge.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reset_error",
        "version": "1.0.0",
        "description": "Xoá cờ lỗi của robot sau khi đã xử lý nguyên nhân.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "go_home",
        "version": "1.0.0",
        "description": "Đưa cánh tay về vị trí home an toàn.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reset_episode",
        "version": "1.0.0",
        "description": "Reset môi trường sim / xoá hàng đợi policy (demo).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class MockEdgeTransport:
    def __init__(self, connected: bool = True, episode_duration_s: float = 1.0) -> None:
        self.connected = connected
        self.episode_duration_s = episode_duration_s
        self._episodes: dict[str, dict] = {}
        self._robot_status = "IDLE"
        self._error_flags: list[str] = []
        # Đếm lời gọi tool (hữu ích cho assert poll get_robot_state)
        self.call_counts: dict[str, int] = {}

    def inject_error_flags(self, flags: list[str]) -> None:
        """Test helper: giả lập cờ lỗi giữa chừng episode."""
        self._error_flags = list(flags)

    async def is_connected(self, robot_id: str) -> bool:
        return self.connected

    async def list_tools(self, robot_id: str) -> list[dict]:
        return _CAPABILITIES

    def _robot_state(self) -> dict:
        # Episode xong thì robot trở lại IDLE
        if self._robot_status == "RUNNING" and all(
            self._episode_status(e)["status"] != "RUNNING" for e in self._episodes.values()
        ):
            self._robot_status = "IDLE"
        return {
            "status": self._robot_status,
            "error_flags": self._error_flags,
            "joints_deg": [0.0, -35.0, 40.0, 0.0, 55.0, 0.0],
            "gripper": "OPEN",
            "temperature_c": 41.2,
            "payload_g": 0,
            "updated_at": _now_iso(),
        }

    def _servo_state(self) -> dict:
        joints_deg = self._robot_state()["joints_deg"]
        return {
            "step": None,
            "joints": [
                {
                    "id": idx,
                    "name": f"joint_{idx}",
                    "pos": round(value * 0.01745329252, 6),
                    "vel": 0.0,
                    "actuator_force": 0.0,
                    "range": [-3.141593, 3.141593],
                }
                for idx, value in enumerate(joints_deg)
            ],
        }

    def _episode_status(self, ep: dict) -> dict:
        elapsed = time.monotonic() - ep["started_monotonic"]
        if ep.get("stopped"):
            return {"status": "STOPPED", "progress": ep.get("progress", 0.0)}
        progress = min(elapsed / self.episode_duration_s, 1.0)
        ep["progress"] = progress
        status = "SUCCESS" if progress >= 1.0 else "RUNNING"
        max_steps = 400
        return {
            "status": status,
            "step": int(progress * max_steps),
            "max_steps": max_steps,
            "progress": round(progress, 2),
        }

    async def call_tool(
        self, robot_id: str, tool_name: str, arguments: dict, timeout_s: float
    ) -> dict:
        self.call_counts[tool_name] = self.call_counts.get(tool_name, 0) + 1
        base = {"ok": True, "tool_name": tool_name, "duration_ms": 5}

        if tool_name == "capture_tool":
            cameras = arguments.get("cameras") or ["top", "wrist"]
            detail = arguments.get("detail_level", "low")
            return {
                **base,
                "data": {"cameras": cameras, "captured_at": _now_iso()},
                "images": [
                    {
                        "url": _TINY_PNG_DATA_URL,
                        "camera_id": cam,
                        "captured_at": _now_iso(),
                        "detail": detail,
                    }
                    for cam in cameras
                ],
                "robot_state": self._robot_state(),
            }

        if tool_name == "get_robot_state":
            return {**base, "robot_state": self._robot_state()}

        if tool_name == "get_servo_state":
            return {**base, "data": self._servo_state()}

        if tool_name == "take_action":
            episode_id = f"ep_{uuid.uuid4().hex[:12]}"
            self._episodes[episode_id] = {
                "instruction": arguments.get("instruction", ""),
                "started_monotonic": time.monotonic(),
            }
            self._robot_status = "RUNNING"
            return {
                **base,
                "data": {
                    "episode_id": episode_id,
                    "status": "ACCEPTED",
                    "estimated_duration_s": int(self.episode_duration_s) or 1,
                },
            }

        if tool_name == "get_task_status":
            ep = self._episodes.get(arguments.get("episode_id", ""))
            if ep is None:
                return {
                    "ok": False,
                    "tool_name": tool_name,
                    "error": {"code": "EDGE_404", "message": "episode không tồn tại"},
                }
            return {**base, "data": self._episode_status(ep)}

        if tool_name == "stop_robot":
            for ep in self._episodes.values():
                if self._episode_status(ep)["status"] == "RUNNING":
                    ep["stopped"] = True
            self._robot_status = "IDLE"
            return {**base, "data": {"stopped": True}, "robot_state": self._robot_state()}

        if tool_name == "health_check":
            return {**base, "data": {"status": "healthy", "edge_version": "1.0.0"}}

        if tool_name == "reset_error":
            self._error_flags = []
            return {**base, "robot_state": self._robot_state()}

        if tool_name == "go_home":
            self._robot_status = "IDLE"
            return {**base, "data": {"at_home": True}, "robot_state": self._robot_state()}

        if tool_name == "reset_episode":
            for ep in self._episodes.values():
                if self._episode_status(ep)["status"] == "RUNNING":
                    ep["stopped"] = True
            self._episodes.clear()
            self._error_flags = []
            self._robot_status = "IDLE"
            return {
                **base,
                "data": {"status": "DONE", "backend_mode": "mock"},
                "robot_state": self._robot_state(),
            }

        return {
            "ok": False,
            "tool_name": tool_name,
            "error": {"code": "EDGE_400", "message": f"tool không hỗ trợ: {tool_name}"},
        }
