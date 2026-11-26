from __future__ import annotations

from typing import Annotated

from auth.deps import require_user_if_configured
from auth.store import User
from fastapi import APIRouter, Depends, Query, Response, status
from models.console import CronValidateRequest, ScheduleCreate, SchedulePatch
from services.cron import validate_cron
from services.scheduler import fire_schedule
from stores.robots import robot_store
from stores.schedules import schedule_store

from api.common import api_error, current_user_id, envelope

router = APIRouter(tags=["schedules"])


def _require_robot(robot_id: str) -> None:
    if not robot_store.exists(robot_id):
        raise api_error("SCHED_004", "Robot đã chọn không còn tồn tại.", 404)


def _validated_cron(expression: str, timezone: str) -> dict:
    result = validate_cron(expression, timezone)
    if not result.valid:
        raise api_error(result.code or "SCHED_002", result.message or "Biểu thức lịch chạy không hợp lệ.", 422)
    return {
        "cron_description": result.cron_description or "",
        "next_run_at": result.next_run_at,
        "next_5_runs_local": result.next_5_runs_local or [],
    }


@router.get("/schedules")
async def list_schedules(
    user: Annotated[User | None, Depends(require_user_if_configured)],
    enabled: bool | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
) -> dict:
    return envelope(
        schedule_store.list(user_id=current_user_id(user), enabled=enabled, limit=limit)
    )


@router.post("/schedules/validate-cron")
async def validate_cron_endpoint(
    body: CronValidateRequest,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    result = validate_cron(body.expression, body.timezone)
    return envelope(
        {
            "valid": result.valid,
            "message": result.message,
            "code": result.code,
            "cron_description": result.cron_description,
            "next_5_runs_local": result.next_5_runs_local,
        }
    )


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: ScheduleCreate,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    name = body.name.strip()
    if not name:
        raise api_error("SCHED_001", "Tên lịch chạy không được để trống.", 422)
    if not body.task_prompt.strip():
        raise api_error("VAL_006", "Thiếu trường bắt buộc.", 422)
    owner = current_user_id(user)
    _require_robot(body.robot_id)
    cron = _validated_cron(body.cron_expression, body.timezone)
    created = schedule_store.create(
        {
            "user_id": owner,
            "name": name,
            "description": body.description.strip(),
            "robot_id": body.robot_id,
            "task_prompt": body.task_prompt.strip(),
            "cron_expression": body.cron_expression.strip(),
            "cron_description": body.cron_description.strip() or cron["cron_description"],
            "timezone": body.timezone,
            "overlap_policy": body.overlap_policy,
            "max_duration_s": body.max_duration_s,
            "next_run_at": cron["next_run_at"],
        }
    )
    return envelope(created)


@router.get("/schedules/{schedule_id}")
async def get_schedule(
    schedule_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    schedule = schedule_store.get(schedule_id, user_id=current_user_id(user))
    if schedule is None:
        raise api_error("SCHED_010", "Không tìm thấy lịch chạy.", 404)
    return envelope(schedule)


@router.patch("/schedules/{schedule_id}")
async def patch_schedule(
    schedule_id: str,
    body: SchedulePatch,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    owner = current_user_id(user)
    existing = schedule_store.get(schedule_id, user_id=owner)
    if existing is None:
        raise api_error("SCHED_010", "Không tìm thấy lịch chạy.", 404)
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        name = (updates["name"] or "").strip()
        if not name:
            raise api_error("SCHED_001", "Tên lịch chạy không được để trống.", 422)
        updates["name"] = name
    if "robot_id" in updates and updates["robot_id"]:
        _require_robot(updates["robot_id"])
    expression = updates.get("cron_expression") or existing["cron_expression"]
    timezone = updates.get("timezone") or existing["timezone"]
    if "cron_expression" in updates or "timezone" in updates:
        cron = _validated_cron(expression, timezone)
        updates["cron_expression"] = expression.strip()
        updates.setdefault("cron_description", cron["cron_description"])
        if updates.get("enabled", existing["enabled"]):
            updates["next_run_at"] = cron["next_run_at"]
    patched = schedule_store.patch(schedule_id, updates, user_id=owner)
    if patched is None:
        raise api_error("SCHED_010", "Không tìm thấy lịch chạy.", 404)
    return envelope(patched)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> Response:
    if not schedule_store.delete(schedule_id, user_id=current_user_id(user)):
        raise api_error("SCHED_010", "Không tìm thấy lịch chạy.", 404)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/schedules/{schedule_id}/runs")
async def list_schedule_runs(
    schedule_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    runs = schedule_store.list_runs(schedule_id, user_id=current_user_id(user))
    if runs is None:
        raise api_error("SCHED_010", "Không tìm thấy lịch chạy.", 404)
    return envelope(runs)


@router.post("/schedules/{schedule_id}/run-now")
async def run_schedule_now(
    schedule_id: str,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    schedule = schedule_store.get(schedule_id, user_id=current_user_id(user))
    if schedule is None:
        raise api_error("SCHED_010", "Không tìm thấy lịch chạy.", 404)
    if not schedule["enabled"]:
        raise api_error("SCHED_020", "Lịch đang tắt.", 409)
    if schedule_store.has_running(schedule_id):
        raise api_error("SCHED_020", "Lịch đang chạy, không thể chạy thử lúc này.", 409)
    try:
        row = await fire_schedule(schedule, manual=True)
    except PermissionError:
        raise api_error("SCHED_020", "Lịch đang chạy, không thể chạy thử lúc này.", 409) from None
    except ConnectionError as exc:
        code = str(exc) or "MCP_001"
        message = (
            "Robot đang ngoại tuyến."
            if code in {"ROBOT_011", "MCP_001", "SCHED_004"}
            else "Không gọi được thiết bị Edge."
        )
        raise api_error(code if code != "SCHED_004" else "MCP_001", message, 503) from None
    return envelope({"run_id": row.get("run_id"), "status": row.get("status")})
