"""Agent runs for Nhật ký — chat, schedule, and manual fire."""

from __future__ import annotations

from typing import Any, Literal

from stores.db import DEFAULT_OWNER_ID, DbConnection, Row, get_connection
from stores.ids import utc_now_iso
from stores.sessions import AUTO_SESSION_TITLE, NEW_SESSION_TITLE

RunSource = Literal["CHAT", "SCHEDULE", "MANUAL_RUN"]


def _public(row: Row) -> dict[str, Any]:
    error = None
    if row["error_code"] or row["error_message"]:
        error = {
            "code": row["error_code"] or "AGENT_001",
            "message": row["error_message"] or "Run thất bại.",
        }
    return {
        "id": row["id"],
        "user_id": row["user_id"] or DEFAULT_OWNER_ID,
        "session_id": row["session_id"] or "",
        "schedule_id": row["schedule_id"],
        "robot_id": row["robot_id"],
        "status": row["status"],
        "source": row["source"],
        "input_text": row["input_text"] or "",
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "duration_s": row["duration_s"],
        "trace_id": row["trace_id"] or "",
        "outcome": row["outcome"],
        "error": error,
        "episodes": [],
    }


class RunStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url

    def _db(self) -> DbConnection:
        conn, _path = get_connection(self._database_url)
        return conn

    def reset(self) -> None:
        conn = self._db()
        conn.execute("DELETE FROM runs")
        conn.commit()

    def start(
        self,
        *,
        run_id: str,
        robot_id: str,
        source: RunSource,
        input_text: str,
        user_id: str = DEFAULT_OWNER_ID,
        session_id: str | None = None,
        schedule_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        conn = self._db()
        conn.execute(
            """
            INSERT INTO runs (
                id, user_id, session_id, schedule_id, robot_id, status, source,
                input_text, created_at, completed_at, duration_s, trace_id,
                outcome, error_code, error_message
            ) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, NULL, NULL, ?, NULL, NULL, NULL)
            """,
            (
                run_id,
                user_id,
                session_id,
                schedule_id,
                robot_id,
                source,
                input_text,
                now,
                trace_id,
            ),
        )
        conn.commit()
        return self.get(run_id, user_id=user_id)  # type: ignore[return-value]

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        duration_s: int | None = None,
        error: dict[str, Any] | None = None,
        outcome: str | None = None,
    ) -> dict[str, Any] | None:
        conn = self._db()
        existing = conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        if existing is None:
            return None
        err_code = None
        err_msg = None
        if isinstance(error, dict):
            err_code = error.get("code")
            err_msg = error.get("message")
        if outcome is None:
            if status == "COMPLETED":
                outcome = "SUCCEEDED"
            elif status in {"FAILED", "TIMEOUT", "CANCELLED"}:
                outcome = status
        conn.execute(
            """
            UPDATE runs
            SET status = ?, completed_at = ?, duration_s = ?,
                outcome = ?, error_code = ?, error_message = ?
            WHERE id = ?
            """,
            (status, utc_now_iso(), duration_s, outcome, err_code, err_msg, run_id),
        )
        conn.commit()
        return self.get(run_id)

    def get(self, run_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id is None:
            row = self._db().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        else:
            row = self._db().execute(
                "SELECT * FROM runs WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            ).fetchone()
        return _public(row) if row else None

    def list(
        self,
        *,
        user_id: str,
        robot_id: str | None = None,
        status: str | None = None,
        source: str | None = None,
        has_error: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs WHERE user_id = ?"
        params: list[Any] = [user_id]
        if robot_id:
            sql += " AND robot_id = ?"
            params.append(robot_id)
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if statuses:
                sql += f" AND status IN ({','.join('?' * len(statuses))})"
                params.extend(statuses)
        if source:
            sources = [s.strip() for s in source.split(",") if s.strip()]
            if sources:
                sql += f" AND source IN ({','.join('?' * len(sources))})"
                params.extend(sources)
        if has_error:
            sql += " AND error_code IS NOT NULL"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = [_public(r) for r in self._db().execute(sql, params)]
        conversations = self._conversations(
            user_id=user_id,
            robot_id=robot_id,
            status=status,
            source=source,
            has_error=has_error,
        )
        covered = {c["session_id"] for c in conversations if c.get("session_id")}
        rows = [
            r
            for r in rows
            if not (r.get("source") == "CHAT" and r.get("session_id") in covered)
        ]
        rows.extend(conversations)
        if not has_error:
            rows.extend(
                self._upcoming_schedules(
                    user_id=user_id, robot_id=robot_id, status=status, source=source
                )
            )
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    def _conversations(
        self,
        *,
        user_id: str,
        robot_id: str | None,
        status: str | None,
        source: str | None,
        has_error: bool,
    ) -> list[dict[str, Any]]:
        """One Nhật ký row per chat phiên (not each agent turn)."""
        if has_error:
            return []
        statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
        sources = [s.strip() for s in (source or "").split(",") if s.strip()]
        if sources and "CHAT" not in sources:
            return []
        sql = """
            SELECT * FROM sessions
            WHERE user_id = ?
              AND (source IS NULL OR source = 'CHAT')
              AND title != ?
              AND NOT (
                title = ? AND (last_message_preview IS NULL OR last_message_preview = '')
                AND active_run IS NULL
              )
        """
        params: list[Any] = [user_id, AUTO_SESSION_TITLE, NEW_SESSION_TITLE]
        if robot_id:
            sql += " AND robot_id = ?"
            params.append(robot_id)
        rows: list[dict[str, Any]] = []
        for row in self._db().execute(sql, params):
            running = bool(row["active_run"])
            run_status = "RUNNING" if running else "COMPLETED"
            if statuses and run_status not in statuses:
                continue
            title = (row["title"] or "").strip()
            preview = (row["last_message_preview"] or "").strip()
            rows.append(
                {
                    "id": f"session:{row['id']}",
                    "user_id": row["user_id"] or user_id,
                    "session_id": row["id"],
                    "schedule_id": None,
                    "robot_id": row["robot_id"],
                    "status": run_status,
                    "source": "CHAT",
                    "input_text": title or preview,
                    "created_at": row["updated_at"] or row["created_at"],
                    "completed_at": None if running else row["updated_at"],
                    "duration_s": None,
                    "trace_id": "",
                    "outcome": None if running else "SUCCEEDED",
                    "error": None,
                    "episodes": [],
                }
            )
        return rows

    def _upcoming_schedules(
        self,
        *,
        user_id: str,
        robot_id: str | None,
        status: str | None,
        source: str | None,
    ) -> list[dict[str, Any]]:
        """Enabled lịch that have not fired yet still belong on Nhật ký."""
        statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
        sources = [s.strip() for s in (source or "").split(",") if s.strip()]
        if statuses and "SCHEDULED" not in statuses:
            return []
        if sources and "SCHEDULE" not in sources:
            return []
        sql = "SELECT * FROM schedules WHERE enabled = 1 AND user_id = ?"
        params: list[Any] = [user_id]
        if robot_id:
            sql += " AND robot_id = ?"
            params.append(robot_id)
        upcoming: list[dict[str, Any]] = []
        for row in self._db().execute(sql, params):
            prompt = (row["task_prompt"] or "").strip()
            name = (row["name"] or "").strip()
            upcoming.append(
                {
                    "id": f"upcoming:{row['id']}",
                    "user_id": row["user_id"] or user_id,
                    "session_id": "",
                    "schedule_id": row["id"],
                    "robot_id": row["robot_id"],
                    "status": "SCHEDULED",
                    "source": "SCHEDULE",
                    "input_text": f"{name}: {prompt}" if name and prompt else (name or prompt),
                    "created_at": row["next_run_at"] or row["created_at"],
                    "completed_at": None,
                    "duration_s": None,
                    "trace_id": "",
                    "outcome": None,
                    "error": None,
                    "episodes": [],
                }
            )
        return upcoming

    def stats(self, *, user_id: str, robot_id: str, days: int) -> dict[str, Any]:
        """Aggregate `runs` for the Tổng quan dashboard's "Thống kê" panel.

        Chỉ tính lượt chạy CỦA robot này trong `days` ngày gần nhất — trang
        nói về con robot đang chọn, không phải toàn hệ thống.
        """
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        since = (now - timedelta(days=days)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        rows = self._db().execute(
            """
            SELECT status, outcome, duration_s, created_at FROM runs
            WHERE user_id = ? AND robot_id = ? AND created_at >= ?
            """,
            (user_id, robot_id, since),
        ).fetchall()

        outcome_counts: dict[str, int] = {}
        daily_counts: dict[str, int] = {}
        finished_durations: list[int] = []
        succeeded = 0
        finished = 0
        for row in rows:
            outcome_counts[row["status"]] = outcome_counts.get(row["status"], 0) + 1
            date = row["created_at"][:10]
            daily_counts[date] = daily_counts.get(date, 0) + 1
            if row["outcome"]:
                finished += 1
                if row["outcome"] == "SUCCEEDED":
                    succeeded += 1
                if row["duration_s"] is not None:
                    finished_durations.append(row["duration_s"])

        # Điền đủ mọi ngày trong cửa sổ, kể cả ngày không có lượt nào — thiếu
        # thì biểu đồ hoạt động hụt cột giữa chừng thay vì một trục ngày liền.
        daily_activity = [
            {"date": (now - timedelta(days=i)).date().isoformat(), "count": daily_counts.get(
                (now - timedelta(days=i)).date().isoformat(), 0
            )}
            for i in range(days - 1, -1, -1)
        ]

        return {
            "window_days": days,
            "total_runs": len(rows),
            "finished_runs": finished,
            "success_rate": (succeeded / finished) if finished > 0 else None,
            "avg_duration_s": (
                sum(finished_durations) / len(finished_durations) if finished_durations else None
            ),
            "outcomes": [
                {"status": status, "count": count}
                for status, count in sorted(outcome_counts.items(), key=lambda kv: -kv[1])
            ],
            "daily_activity": daily_activity,
        }


run_store = RunStore()
