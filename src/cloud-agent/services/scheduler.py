"""Fire due schedules, record outcomes, auto-disable after 5 consecutive failures."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from stores.ids import new_id, utc_now_iso
from stores.robots import robot_store
from stores.runs import run_store
from stores.schedules import AUTO_DISABLE_THRESHOLD, schedule_store
from stores.sessions import AUTO_SESSION_TITLE, session_store

from services.robot_status import release_if_idle

logger = logging.getLogger(__name__)

ExecuteFn = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]

_execute_override: ExecuteFn | None = None


def set_execute_override(fn: ExecuteFn | None) -> None:
    """Tests inject a fake agent so ticks don't call the LLM."""
    global _execute_override
    _execute_override = fn


async def _default_execute(
    schedule: dict[str, Any],
    session: dict[str, Any],
    *,
    run_id: str,
    trace_id: str,
    source: str = "SCHEDULE",
) -> dict[str, Any]:
    from agents.runner import build_default_context, run_agent

    # Reuse the console run_store id so schedule → /runs/{id} → timeline share one key.
    ctx = build_default_context(
        robot_id=schedule["robot_id"],
        session_id=session["id"],
        user_id=schedule.get("user_id") or "user_dev",
        source=source,
        run_id=run_id,
        trace_id=trace_id,
    )
    result = await run_agent(schedule["task_prompt"], ctx=ctx)
    status = result.get("final_status") or "FAILED"
    error = result.get("error")
    text = ""
    messages = result.get("messages") or []
    if messages:
        last = messages[-1]
        text = getattr(last, "content", None) or str(last)
        if not isinstance(text, str):
            text = str(text)
    return {
        "status": "COMPLETED" if status == "COMPLETED" else "FAILED",
        "run_id": run_id,
        "response": text,
        "error": error,
    }


async def _execute(
    schedule: dict[str, Any],
    session: dict[str, Any],
    *,
    run_id: str,
    trace_id: str,
    source: str = "SCHEDULE",
) -> dict[str, Any]:
    if _execute_override is not None:
        return await _execute_override(schedule, session)
    return await _default_execute(
        schedule, session, run_id=run_id, trace_id=trace_id, source=source
    )


def _robot_gate(robot: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Return (skip_status, reason_code) if this tick should not run."""
    if robot is None:
        return "SKIPPED_OFFLINE", "SCHED_004"
    if robot.get("connection_status") == "OFFLINE":
        return "SKIPPED_OFFLINE", "ROBOT_011"
    if robot.get("robot_status") in {"BUSY", "STOPPING"}:
        return "SKIPPED_BUSY", "ROBOT_010"
    if robot.get("robot_status") == "ERROR":
        return "SKIPPED_BUSY", "ROBOT_020"
    if robot.get("robot_status") == "DISCONNECTED":
        return "SKIPPED_OFFLINE", "ROBOT_011"
    return None, None


def _new_run(*, scheduled_at: str, started: bool, status: str, run_id: str | None, reason_code: str | None) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "id": new_id("sr"),
        "scheduled_at": scheduled_at,
        "started_at": now if started else None,
        "status": status,
        "duration_s": None,
        "run_id": run_id,
        "reason_code": reason_code,
    }


def _advance_next_run(schedule: dict[str, Any]) -> None:
    from services.cron import next_run_iso

    schedule_store.patch(
        schedule["id"],
        {"next_run_at": next_run_iso(schedule["cron_expression"], schedule["timezone"])},
    )


async def fire_schedule(schedule: dict[str, Any], *, manual: bool = False) -> dict[str, Any]:
    """Run one schedule now. Returns the schedule-run row."""
    owner = schedule.get("user_id") or "user_dev"
    robot = robot_store.get(schedule["robot_id"])
    scheduled_at = utc_now_iso()
    skip_status, reason = _robot_gate(robot)
    policy = schedule.get("overlap_policy") or "SKIP"
    force = skip_status == "SKIPPED_BUSY" and policy == "FORCE_STOP"
    if skip_status and not force:
        if manual:
            if skip_status == "SKIPPED_BUSY":
                raise PermissionError("SCHED_020")
            raise ConnectionError(reason or "MCP_001")
        if skip_status == "SKIPPED_BUSY" and policy == "QUEUE":
            return {
                "id": None,
                "status": "DEFERRED",
                "scheduled_at": scheduled_at,
                "started_at": None,
                "duration_s": None,
                "run_id": None,
                "reason_code": "ROBOT_010",
            }
        row = _new_run(
            scheduled_at=scheduled_at,
            started=False,
            status=skip_status,
            run_id=None,
            reason_code=reason,
        )
        schedule_store.add_run(schedule["id"], row)
        _advance_next_run(schedule)
        return row

    session = session_store.create(
        schedule["robot_id"],
        user_id=owner,
        title=AUTO_SESSION_TITLE,
        source="SCHEDULE",
    )
    agent_run_id = new_id("run")
    trace_id = (
        agent_run_id.replace("run_", "trc_", 1) if agent_run_id.startswith("run_") else agent_run_id
    )
    row = _new_run(
        scheduled_at=scheduled_at,
        started=True,
        status="RUNNING",
        run_id=agent_run_id,
        reason_code=None,
    )
    schedule_store.add_run(schedule["id"], row)
    session_store.set_active_run(session["id"], agent_run_id)
    session_store.append_message(session["id"], role="user", content=schedule["task_prompt"])
    run_source = "MANUAL_RUN" if manual else "SCHEDULE"
    run_store.start(
        run_id=agent_run_id,
        user_id=owner,
        session_id=session["id"],
        schedule_id=schedule["id"],
        robot_id=schedule["robot_id"],
        source=run_source,
        input_text=schedule["task_prompt"],
        trace_id=trace_id,
    )

    started = datetime.now(UTC)
    try:
        result = await _execute(
            schedule,
            session,
            run_id=agent_run_id,
            trace_id=trace_id,
            source=run_source,
        )
        status = result.get("status") or "FAILED"
        # Keep the pre-minted id — never swap to a second id from the agent.
        run_id = agent_run_id
        response = (result.get("response") or "").strip() or (
            "Nhiệm vụ lịch chạy hoàn tất." if status == "COMPLETED" else "Nhiệm vụ lịch chạy thất bại."
        )
        error = result.get("error")
        session_store.append_message(session["id"], role="assistant", content=response)
        duration = max(0, int((datetime.now(UTC) - started).total_seconds()))
        schedule_store.update_run(
            schedule["id"],
            row["id"],
            status=status,
            run_id=run_id,
            duration_s=duration,
            reason_code=(error or {}).get("code") if isinstance(error, dict) else None,
        )
        run_store.finish(
            agent_run_id,
            status=status,
            duration_s=duration,
            error=error if isinstance(error, dict) else None,
        )
        schedule_store.record_outcome(schedule["id"], status=status, advance_cron=not manual)
        row = {**row, "status": status, "run_id": run_id, "duration_s": duration}
        return row
    except Exception:
        logger.exception("scheduled run failed schedule_id=%s", schedule["id"])
        duration = max(0, int((datetime.now(UTC) - started).total_seconds()))
        schedule_store.update_run(
            schedule["id"],
            row["id"],
            status="FAILED",
            duration_s=duration,
            reason_code="SYS_001",
        )
        run_store.finish(
            agent_run_id,
            status="FAILED",
            duration_s=duration,
            error={"code": "SYS_001", "message": "Nhiệm vụ lịch chạy thất bại."},
        )
        schedule_store.record_outcome(schedule["id"], status="FAILED", advance_cron=not manual)
        session_store.append_message(
            session["id"],
            role="assistant",
            content="Nhiệm vụ lịch chạy thất bại.",
        )
        raise
    finally:
        session_store.set_active_run(session["id"], None)
        release_if_idle(schedule["robot_id"])


async def tick_due_schedules() -> list[dict[str, Any]]:
    due = schedule_store.due(utc_now_iso())
    fired: list[dict[str, Any]] = []
    for schedule in due:
        if schedule_store.has_running(schedule["id"]):
            continue
        try:
            fired.append(await fire_schedule(schedule, manual=False))
        except Exception:
            logger.exception("tick failed for %s", schedule["id"])
    return fired


async def scheduler_loop(stop: asyncio.Event, interval_s: float = 15.0) -> None:
    while not stop.is_set():
        try:
            await tick_due_schedules()
        except Exception:
            logger.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue


def auto_disabled(schedule: dict[str, Any]) -> bool:
    return (not schedule.get("enabled")) and int(schedule.get("consecutive_failures") or 0) >= AUTO_DISABLE_THRESHOLD
