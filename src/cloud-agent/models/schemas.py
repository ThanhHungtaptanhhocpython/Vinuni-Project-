from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class RunSource(StrEnum):
    CHAT = "CHAT"
    SCHEDULE = "SCHEDULE"
    MANUAL_RUN = "MANUAL_RUN"


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000, description="Tin nhắn từ user")
    robot_id: str = Field(..., min_length=1, description="Robot nhận lệnh")
    session_id: str | None = Field(default=None, description="Session hội thoại (tuỳ chọn)")


class ChatResponse(BaseModel):
    response: str = Field(..., description="Phản hồi cuối cùng từ agent")
    run_id: str = Field(..., description="ID của run")
    trace_id: str = Field(..., description="Trace ID")
    status: RunStatus = Field(..., description="Trạng thái của run")
    duration_ms: int | None = Field(default=None, description="Thời lượng chạy")
    error: dict | None = Field(default=None, description="ErrorObject nếu run lỗi")


class ChatStartResponse(BaseModel):
    run_id: str
    trace_id: str
    session_id: str
    status: RunStatus = RunStatus.RUNNING


class RunSummary(BaseModel):
    id: str
    trace_id: str
    session_id: str
    robot_id: str
    user_id: str
    source: RunSource
    status: RunStatus
    input_text: str
    created_at: str
    completed_at: str | None = None
    duration_ms: int | None = None
    error: dict | None = None


class RunDetail(RunSummary):
    final_response: str | None = None
    outcome: str | None = None
    events_dropped: int = 0


class TraceEvent(BaseModel):
    id: str
    run_id: str
    trace_id: str
    sequence: int
    timestamp: str
    type: str
    status: str | None = None
    duration_ms: int | None = None
    model: str | None = None
    tool_name: str | None = None
    arguments: dict | None = None
    result_summary: Any = None
    error: dict | None = None
    metadata: dict = Field(default_factory=dict)


class PaginatedRuns(BaseModel):
    items: list[RunSummary]
    total: int
