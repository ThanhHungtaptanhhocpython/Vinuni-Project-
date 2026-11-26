"""Schedules + per-schedule run rows persisted in the shared SQL store."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from services.cron import next_run_iso

from stores.db import DEFAULT_OWNER_ID, DbConnection, Row, get_connection
from stores.ids import new_id, utc_now_iso

AUTO_DISABLE_THRESHOLD = 5
OverlapPolicy = Literal["SKIP", "QUEUE", "FORCE_STOP"]
RunStatus = Literal[
    "COMPLETED",
    "FAILED",
    "RUNNING",
    "SKIPPED_BUSY",
    "SKIPPED_OFFLINE",
    "MISFIRED",
]


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _from_schedule(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"] or DEFAULT_OWNER_ID,
        "name": row["name"],
        "description": row["description"] or "",
        "robot_id": row["robot_id"],
        "task_prompt": row["task_prompt"],
        "cron_expression": row["cron_expression"],
        "cron_description": row["cron_description"] or "",
        "timezone": row["timezone"],
        "enabled": bool(row["enabled"]),
        "overlap_policy": row["overlap_policy"] or "SKIP",
        "max_duration_s": int(row["max_duration_s"] or 180),
        "consecutive_failures": int(row["consecutive_failures"] or 0),
        "next_run_at": row["next_run_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _from_run(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "scheduled_at": row["scheduled_at"],
        "started_at": row["started_at"],
        "status": row["status"],
        "duration_s": row["duration_s"],
        "run_id": row["run_id"],
        "reason_code": row["reason_code"],
    }


class ScheduleStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url

    def _db(self) -> DbConnection:
        conn, _path = get_connection(self._database_url)
        return conn

    def reset(self) -> None:
        conn = self._db()
        conn.execute("DELETE FROM schedule_runs")
        conn.execute("DELETE FROM schedules")
        conn.commit()

    def list(
        self,
        *,
        user_id: str,
        enabled: bool | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM schedules WHERE user_id = ?"
        params: list[Any] = [user_id]
        if enabled is not None:
            sql += " AND enabled = ?"
            params.append(1 if enabled else 0)
        sql += " ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_from_schedule(r) for r in self._db().execute(sql, params)]

    def enabled_count(self, *, user_id: str | None = None) -> int:
        if user_id is None:
            value = self._db().execute(
                "SELECT COUNT(*) FROM schedules WHERE enabled = 1"
            ).fetchone()[0]
        else:
            value = self._db().execute(
                "SELECT COUNT(*) FROM schedules WHERE enabled = 1 AND user_id = ?",
                (user_id,),
            ).fetchone()[0]
        return int(value)

    def get(self, schedule_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id is None:
            row = self._db().execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
        else:
            row = self._db().execute(
                "SELECT * FROM schedules WHERE id = ? AND user_id = ?",
                (schedule_id, user_id),
            ).fetchone()
        return _from_schedule(row) if row else None

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        schedule_id = new_id("sched")
        user_id = payload.get("user_id") or DEFAULT_OWNER_ID
        conn = self._db()
        conn.execute(
            """
            INSERT INTO schedules (
                id, user_id, name, description, robot_id, task_prompt,
                cron_expression, cron_description, timezone, enabled,
                overlap_policy, max_duration_s, consecutive_failures,
                next_run_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?)
            """,
            (
                schedule_id,
                user_id,
                payload["name"],
                payload.get("description") or "",
                payload["robot_id"],
                payload["task_prompt"],
                payload["cron_expression"],
                payload["cron_description"],
                payload["timezone"],
                payload.get("overlap_policy") or "SKIP",
                payload.get("max_duration_s") or 180,
                payload.get("next_run_at"),
                now,
                now,
            ),
        )
        conn.commit()
        return self.get(schedule_id, user_id=user_id)  # type: ignore[return-value]

    def patch(
        self, schedule_id: str, updates: dict[str, Any], *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        conn = self._db()
        existing = self.get(schedule_id, user_id=user_id)
        if existing is None:
            return None
        row = conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        if row is None:
            return None
        current = _from_schedule(row)
        if "enabled" in updates and updates["enabled"] is True and not current["enabled"]:
            current["consecutive_failures"] = 0
            current["next_run_at"] = next_run_iso(current["cron_expression"], current["timezone"])
        if "enabled" in updates and updates["enabled"] is False:
            current["next_run_at"] = None
        for key in (
            "name",
            "description",
            "robot_id",
            "task_prompt",
            "cron_expression",
            "cron_description",
            "timezone",
            "enabled",
            "overlap_policy",
            "max_duration_s",
            "next_run_at",
            "consecutive_failures",
        ):
            if key not in updates:
                continue
            if updates[key] is not None or key in {"next_run_at", "consecutive_failures"}:
                current[key] = updates[key]
        current["updated_at"] = utc_now_iso()
        conn.execute(
            """
            UPDATE schedules SET
                name = ?, description = ?, robot_id = ?, task_prompt = ?,
                cron_expression = ?, cron_description = ?, timezone = ?,
                enabled = ?, overlap_policy = ?, max_duration_s = ?,
                consecutive_failures = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                current["name"],
                current["description"],
                current["robot_id"],
                current["task_prompt"],
                current["cron_expression"],
                current["cron_description"],
                current["timezone"],
                1 if current["enabled"] else 0,
                current["overlap_policy"],
                current["max_duration_s"],
                current["consecutive_failures"],
                current["next_run_at"],
                current["updated_at"],
                schedule_id,
            ),
        )
        conn.commit()
        return self.get(schedule_id, user_id=user_id)

    def delete(self, schedule_id: str, *, user_id: str | None = None) -> bool:
        if self.get(schedule_id, user_id=user_id) is None:
            return False
        conn = self._db()
        conn.execute("DELETE FROM schedule_runs WHERE schedule_id = ?", (schedule_id,))
        cur = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        conn.commit()
        return cur.rowcount > 0

    def list_runs(self, schedule_id: str, *, user_id: str | None = None) -> list[dict[str, Any]] | None:
        if self.get(schedule_id, user_id=user_id) is None:
            return None
        rows = self._db().execute(
            """
            SELECT * FROM schedule_runs
            WHERE schedule_id = ?
            ORDER BY scheduled_at DESC, id DESC
            """,
            (schedule_id,),
        ).fetchall()
        return [_from_run(r) for r in rows]

    def add_run(self, schedule_id: str, run: dict[str, Any]) -> dict[str, Any]:
        conn = self._db()
        conn.execute(
            """
            INSERT INTO schedule_runs (
                id, schedule_id, scheduled_at, started_at, status,
                duration_s, run_id, reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["id"],
                schedule_id,
                run["scheduled_at"],
                run.get("started_at"),
                run["status"],
                run.get("duration_s"),
                run.get("run_id"),
                run.get("reason_code"),
            ),
        )
        conn.commit()
        return dict(run)

    def update_run(self, schedule_id: str, row_id: str, **fields: Any) -> dict[str, Any] | None:
        conn = self._db()
        row = conn.execute(
            "SELECT * FROM schedule_runs WHERE id = ? AND schedule_id = ?",
            (row_id, schedule_id),
        ).fetchone()
        if row is None:
            return None
        current = _from_run(row)
        current.update(fields)
        conn.execute(
            """
            UPDATE schedule_runs
            SET started_at = ?, status = ?, duration_s = ?, run_id = ?, reason_code = ?
            WHERE id = ?
            """,
            (
                current.get("started_at"),
                current["status"],
                current.get("duration_s"),
                current.get("run_id"),
                current.get("reason_code"),
                row_id,
            ),
        )
        conn.commit()
        return current

    def due(self, now_iso: str) -> list[dict[str, Any]]:
        now = _parse_iso(now_iso)
        rows = []
        for row in self._db().execute(
            "SELECT * FROM schedules WHERE enabled = 1 AND next_run_at IS NOT NULL"
        ):
            item = _from_schedule(row)
            if item["next_run_at"] and _parse_iso(item["next_run_at"]) <= now:
                rows.append(item)
        return rows

    def record_outcome(
        self,
        schedule_id: str,
        *,
        status: RunStatus,
        advance_cron: bool = True,
    ) -> dict[str, Any] | None:
        """Update consecutive_failures / auto-disable. Returns the schedule."""
        conn = self._db()
        row = conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        if row is None:
            return None
        current = _from_schedule(row)
        if status == "FAILED":
            current["consecutive_failures"] = int(current["consecutive_failures"]) + 1
            if current["consecutive_failures"] >= AUTO_DISABLE_THRESHOLD:
                current["enabled"] = False
                current["next_run_at"] = None
                advance_cron = False
        elif status == "COMPLETED":
            current["consecutive_failures"] = 0
        if advance_cron and current["enabled"]:
            current["next_run_at"] = next_run_iso(
                current["cron_expression"], current["timezone"]
            )
        return self.patch(
            schedule_id,
            {
                "enabled": current["enabled"],
                "consecutive_failures": current["consecutive_failures"],
                "next_run_at": current["next_run_at"],
            },
        )

    def has_running(self, schedule_id: str) -> bool:
        row = self._db().execute(
            """
            SELECT 1 FROM schedule_runs
            WHERE schedule_id = ? AND status = 'RUNNING'
            LIMIT 1
            """,
            (schedule_id,),
        ).fetchone()
        return row is not None


schedule_store = ScheduleStore()
