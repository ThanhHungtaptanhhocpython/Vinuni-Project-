from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OverlapPolicy = Literal["SKIP", "QUEUE", "FORCE_STOP"]


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    robot_id: str = Field(..., min_length=1)
    task_prompt: str = Field(..., min_length=1, max_length=5000)
    cron_expression: str = Field(..., min_length=1, max_length=120)
    cron_description: str = ""
    timezone: str = "Asia/Ho_Chi_Minh"
    overlap_policy: OverlapPolicy = "SKIP"
    max_duration_s: int = Field(default=180, ge=60, le=300)


class SchedulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    robot_id: str | None = None
    task_prompt: str | None = Field(default=None, min_length=1, max_length=5000)
    cron_expression: str | None = None
    cron_description: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    overlap_policy: OverlapPolicy | None = None
    max_duration_s: int | None = Field(default=None, ge=60, le=300)


class CronValidateRequest(BaseModel):
    expression: str = Field(..., min_length=1, max_length=120)
    timezone: str = "Asia/Ho_Chi_Minh"


class RobotCreate(BaseModel):
    id: str = Field(..., min_length=3, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    # KHÔNG có `model` — loại tay do Edge tự khai khi đăng ký
    # (`stores.edge_registry`), ghép nối chỉ tra cứu lại, không cho người dùng
    # tự chọn (tránh lệch với phần cứng thật).


class SessionCreate(BaseModel):
    robot_id: str = Field(..., min_length=1)


class SessionPatch(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class SessionMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class MessageFeedbackPatch(BaseModel):
    # None xoá đánh giá đã có (bấm lại nút đang bật để bỏ chọn).
    feedback: Literal["like", "dislike"] | None = None
