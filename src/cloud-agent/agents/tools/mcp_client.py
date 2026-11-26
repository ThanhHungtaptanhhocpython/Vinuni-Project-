"""MCP Client facade — theo DOC 1 §7.

Trong hệ thống đầy đủ, client gửi request qua Redis Stream tới edge-gateway
đang giữ tunnel của robot. Ở skeleton này, transport là interface cắm được:
MockEdgeTransport cho dev/test, RedisGatewayTransport sẽ thêm sau.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError
from .policies import BREAKER_EXEMPT_TOOLS, TOOL_RETRY, TOOL_TIMEOUT


class ImageRef(BaseModel):
    """Tham chiếu ảnh [DOC 0 §3.5.6] — field name khớp đúng những gì Edge `capture_tool`
    trả (`src/edge_vla/mcp_server/tools/capture.py`). `extra="allow"` để không vỡ khi Edge
    thêm field (vd `frame_id`)."""

    model_config = ConfigDict(extra="allow")

    url: str
    camera_id: str | None = None
    captured_at: str | None = None
    detail: Literal["low", "high"] = "low"
    url_expires_at: str | None = None
    # Trước Phase 8 (chưa có Cloud storage/presigned URL thật), Edge trả `url` dạng
    # `file://` local + base64 JPEG thumbnail riêng (DOC 3 §4.1). Cloud dùng field này
    # để dựng data: URI khi `url` không phải http(s)/data: — xem serialize.py.
    thumbnail_b64: str | None = None


class ToolError(BaseModel):
    code: str
    message: str | None = None
    detail: dict | None = None


class ToolResult(BaseModel):
    """Kết quả tool từ Edge [DOC 0 §3.5.5] — giữ extra để không vỡ khi Edge thêm field."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    tool_name: str
    data: dict[str, Any] = Field(default_factory=dict)
    images: list[ImageRef] = Field(default_factory=list)
    robot_state: dict | None = None
    error: ToolError | None = None
    duration_ms: int | None = None


class McpTransport(Protocol):
    """Đường truyền tới Edge MCP Server (qua gateway trong production)."""

    async def list_tools(self, robot_id: str) -> list[dict]: ...

    async def call_tool(
        self, robot_id: str, tool_name: str, arguments: dict, timeout_s: float
    ) -> dict: ...

    async def is_connected(self, robot_id: str) -> bool: ...


class CircuitBreaker:
    """Circuit breaker per robot_id — DOC 1 §7.3.

    Mở sau 5 lỗi liên tiếp, mở trong 30s, half-open cho 1 request thử.
    """

    def __init__(self, threshold: int = 5, open_seconds: float = 30.0) -> None:
        self.threshold = threshold
        self.open_seconds = open_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_probe = False

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.open_seconds:
            # Chuyển half-open: cho đúng 1 request thử
            if not self._half_open_probe:
                self._half_open_probe = True
                return False
            return True
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._half_open_probe = False

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold or self._half_open_probe:
            self._opened_at = time.monotonic()
            self._half_open_probe = False


class McpClient:
    """Facade gọi tool trên Edge với timeout, retry và circuit breaker."""

    def __init__(self, transport: McpTransport) -> None:
        self.transport = transport
        self.breaker: dict[str, CircuitBreaker] = {}

    def _breaker(self, robot_id: str) -> CircuitBreaker:
        return self.breaker.setdefault(robot_id, CircuitBreaker())

    async def is_robot_online(self, robot_id: str) -> bool:
        return await self.transport.is_connected(robot_id)

    async def list_tools(self, robot_id: str) -> list[dict]:
        """Nạp capability từ Edge (production: đọc cache Redis trước, miss thì tools/list)."""
        if not await self.transport.is_connected(robot_id):
            raise AppError("MCP_001", detail={"robot_id": robot_id})
        return await self.transport.list_tools(robot_id)

    async def call_tool(
        self,
        robot_id: str,
        tool_name: str,
        arguments: dict,
        trace_id: str,
        timeout_s: float | None = None,
        priority: int = 5,  # 1 = cao nhất (stop_robot)
    ) -> ToolResult:
        timeout_s = timeout_s or TOOL_TIMEOUT[tool_name]
        breaker = self._breaker(robot_id)

        # stop_robot bỏ qua breaker — E-Stop phải luôn được thử (DOC 1 §7.2)
        if breaker.is_open and tool_name not in BREAKER_EXEMPT_TOOLS:
            raise AppError("MCP_001", detail={"reason": "circuit_open", "robot_id": robot_id})

        max_retries, backoffs = TOOL_RETRY.get(tool_name, (0, []))
        attempt = 0
        while True:
            try:
                raw = await asyncio.wait_for(
                    self.transport.call_tool(robot_id, tool_name, arguments, timeout_s),
                    timeout=timeout_s + 1.0,
                )
                result = ToolResult.model_validate(raw)
            except TimeoutError:
                breaker.record_failure()
                if attempt < max_retries:
                    await asyncio.sleep(backoffs[attempt])
                    attempt += 1
                    continue
                raise AppError(
                    "MCP_003", detail={"tool": tool_name, "timeout_s": timeout_s}
                ) from None
            except AppError:
                breaker.record_failure()
                raise
            except Exception as exc:
                breaker.record_failure()
                if attempt < max_retries:
                    await asyncio.sleep(backoffs[attempt])
                    attempt += 1
                    continue
                raise AppError(
                    "MCP_004", detail={"tool": tool_name, "original_error": str(exc)}
                ) from exc

            if not result.ok and result.error is not None and attempt < max_retries:
                breaker.record_failure()
                await asyncio.sleep(backoffs[attempt])
                attempt += 1
                continue

            if result.ok:
                breaker.record_success()
            else:
                breaker.record_failure()
            return result
