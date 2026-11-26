"""Error codes và AppError cho Cloud Agent.

Bảng mã lỗi đầy đủ nằm ở DOC 0 §7.2 — đây là subset các mã mà Agent sinh ra
(DOC 1 §2.9, §5).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    http_status: int
    message_vi: str
    retryable: bool


ERROR_CATALOG: dict[str, ErrorSpec] = {
    # --- Agent / LLM ---
    "AGENT_001": ErrorSpec(502, "Không kết nối được tới nhà cung cấp LLM.", True),
    "AGENT_002": ErrorSpec(429, "LLM đang quá tải, vui lòng thử lại sau.", True),
    "AGENT_003": ErrorSpec(504, "LLM phản hồi quá chậm.", True),
    "AGENT_004": ErrorSpec(400, "Yêu cầu gửi tới LLM không hợp lệ.", False),
    "AGENT_010": ErrorSpec(413, "Ngữ cảnh hội thoại vượt quá ngân sách token.", False),
    "AGENT_011": ErrorSpec(422, "Agent vượt quá giới hạn vòng lặp / tool call cho phép.", False),
    "AGENT_020": ErrorSpec(504, "Run vượt quá thời gian tối đa cho phép.", False),
    "AGENT_030": ErrorSpec(422, "Nội dung bị từ chối bởi bộ lọc an toàn của LLM.", False),
    # --- MCP / Edge ---
    "MCP_001": ErrorSpec(503, "Robot đang offline hoặc không thể kết nối.", True),
    "MCP_002": ErrorSpec(503, "Kết nối tunnel tới Edge bị gián đoạn.", True),
    "MCP_003": ErrorSpec(504, "Tool trên Edge không phản hồi trong thời gian cho phép.", True),
    "MCP_004": ErrorSpec(502, "Edge trả về lỗi không xác định.", False),
    "MCP_005": ErrorSpec(403, "Tool không nằm trong danh sách được phép.", False),
    # --- System ---
    "SYS_001": ErrorSpec(500, "Lỗi xác thực với dịch vụ bên ngoài.", False),
}


class AppError(Exception):
    """Exception chuẩn của Cloud — cấu trúc theo DOC 1 §5.1."""

    def __init__(
        self,
        code: str,
        *,
        detail: dict | None = None,
        message: str | None = None,
        source: str = "CLOUD",
        retry_after_ms: int | None = None,
    ) -> None:
        spec = ERROR_CATALOG[code]
        self.code = code
        self.http_status = spec.http_status
        self.message = message or spec.message_vi
        self.retryable = spec.retryable
        self.detail = detail
        self.source = source
        self.retry_after_ms = retry_after_ms
        super().__init__(f"{code}: {self.message}")

    def to_object(self) -> dict:
        """ErrorObject lưu vào AgentState.error / trả cho client."""
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "source": self.source,
            "retryable": self.retryable,
            "retry_after_ms": self.retry_after_ms,
        }


class RunCancelledError(Exception):
    """Run bị huỷ bởi người dùng (DOC 1 §2.8). Mang theo message dở dang nếu có."""

    def __init__(self, partial_content: str | None = None) -> None:
        self.partial_content = partial_content
        super().__init__("run cancelled")
