from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from models.schemas import PaginatedRuns, RunDetail, RunSummary, TraceEvent
from stores.db import DbConnection, get_connection

from .trace_repo import InMemoryTraceRepository

logger = logging.getLogger("agent.db_trace_repo")

RUN_FILTER_COLUMNS = {
    "user_id": "user_id",
    "robot_id": "robot_id",
    "session_id": "session_id",
    "source": "source",
    "status": "status",
}


class DbTraceRepository(InMemoryTraceRepository):
    """Database-backed trace repository with in-memory queues for live SSE."""

    def __init__(
        self,
        database_url: str | None = None,
        max_items: int = 1000,
        legacy_data_dir: str = "./data/traces",
    ):
        super().__init__(max_items=max_items)
        self._database_url = database_url
        self._legacy_data_dir = Path(legacy_data_dir)
        self._migrate_legacy_file_traces()
        self._load_recent_runs()

    def _db(self) -> DbConnection:
        conn, _path = get_connection(self._database_url)
        return conn

    def reset_for_test(self):
        super().reset_for_test()
        conn = self._db()
        conn.execute("DELETE FROM trace_events")
        conn.execute("DELETE FROM trace_runs")
        conn.commit()

    def _load_recent_runs(self) -> None:
        try:
            rows = self._db().execute(
                """
                SELECT run_json
                FROM trace_runs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (self._max_items,),
            ).fetchall()
        except Exception as exc:
            logger.error("Failed to load trace runs from DB: %s", exc)
            return

        runs: list[RunDetail] = []
        for row in reversed(rows):
            try:
                runs.append(self._run_from_json(row["run_json"]))
            except Exception as exc:
                logger.error("Failed to deserialize trace run from DB: %s", exc)

        with self._lock:
            for run in runs:
                self._runs[run.id] = run
                self._run_keys_fifo.append(run.id)
                self._events[run.id] = []
                self._sequences[run.id] = self._max_sequence(run.id)

    def _max_sequence(self, run_id: str) -> int:
        row = self._db().execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM trace_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["max_sequence"] or 0) if row else 0

    def _trace_run_exists(self, run_id: str) -> bool:
        row = self._db().execute(
            "SELECT 1 FROM trace_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return row is not None

    def _migrate_legacy_file_traces(self) -> None:
        if not self._legacy_data_dir.exists():
            return
        conn = self._db()
        migrated_run_ids: list[str] = []
        conn.execute("BEGIN")
        for run_file in self._legacy_data_dir.glob("*/run.json"):
            try:
                run = self._run_from_json(run_file.read_text(encoding="utf-8"))
                if self._trace_run_exists(run.id):
                    continue
                self._persist_run_to_conn(conn, run)

                events_file = run_file.with_name("events.jsonl")
                if events_file.exists():
                    for line in events_file.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        event = self._event_from_json(line)
                        self._persist_event_to_conn(conn, event)

                migrated_run_ids.append(run.id)
            except Exception as exc:
                logger.error("Failed to migrate legacy trace file %s: %s", run_file, exc)
        try:
            conn.commit()
        except Exception:
            conn.rollback()
            for run_id in migrated_run_ids:
                self._runs.pop(run_id, None)
                self._events.pop(run_id, None)
                self._sequences.pop(run_id, None)
            raise

    @staticmethod
    def _run_json(run: RunDetail) -> str:
        return run.model_dump_json()

    @staticmethod
    def _run_from_json(payload: str) -> RunDetail:
        if hasattr(RunDetail, "model_validate_json"):
            return RunDetail.model_validate_json(payload)
        return RunDetail(**json.loads(payload))

    @staticmethod
    def _event_from_json(payload: str) -> TraceEvent:
        if hasattr(TraceEvent, "model_validate_json"):
            return TraceEvent.model_validate_json(payload)
        return TraceEvent(**json.loads(payload))

    def _persist_run_to_conn(self, conn: DbConnection, run: RunDetail) -> None:
        conn.execute(
            """
            INSERT INTO trace_runs (
                id, trace_id, session_id, robot_id, user_id, source, status,
                input_text, created_at, completed_at, run_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                trace_id = excluded.trace_id,
                session_id = excluded.session_id,
                robot_id = excluded.robot_id,
                user_id = excluded.user_id,
                source = excluded.source,
                status = excluded.status,
                input_text = excluded.input_text,
                created_at = excluded.created_at,
                completed_at = excluded.completed_at,
                run_json = excluded.run_json
            """,
            (
                run.id,
                run.trace_id,
                run.session_id,
                run.robot_id,
                run.user_id,
                str(run.source),
                str(run.status),
                run.input_text,
                run.created_at,
                run.completed_at,
                self._run_json(run),
            ),
        )

    def _persist_event_to_conn(self, conn: DbConnection, event: TraceEvent) -> None:
        conn.execute(
            """
            INSERT INTO trace_events (
                run_id, sequence, id, trace_id, timestamp, type, event_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, sequence) DO UPDATE SET
                id = excluded.id,
                trace_id = excluded.trace_id,
                timestamp = excluded.timestamp,
                type = excluded.type,
                event_json = excluded.event_json
            """,
            (
                event.run_id,
                event.sequence,
                event.id,
                event.trace_id,
                event.timestamp,
                event.type,
                event.model_dump_json(),
            ),
        )

    def _persist_run_locked(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        conn = self._db()
        try:
            self._persist_run_to_conn(conn, run)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("Failed to persist trace run %s: %s", run_id, exc)

    def _persist_event_locked(self, event: TraceEvent) -> None:
        conn = self._db()
        try:
            self._persist_event_to_conn(conn, event)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("Failed to persist trace event %s/%s: %s", event.run_id, event.id, exc)

    def create_run(self, run: RunDetail) -> None:
        super().create_run(run)
        with self._lock:
            self._persist_run_locked(run.id)

    def update_run(self, run_id: str, updates: dict[str, Any]) -> None:
        super().update_run(run_id, updates)
        with self._lock:
            self._persist_run_locked(run_id)

    def _append_event_locked(self, events_list: list[TraceEvent], event: TraceEvent) -> None:
        super()._append_event_locked(events_list, event)
        self._persist_event_locked(events_list[-1])

    def _load_timeline_locked(self, run_id: str) -> None:
        if run_id in self._events and self._events[run_id]:
            return
        rows = self._db().execute(
            """
            SELECT event_json
            FROM trace_events
            WHERE run_id = ?
            ORDER BY sequence ASC
            """,
            (run_id,),
        ).fetchall()
        events = []
        max_sequence = 0
        for row in rows:
            event = self._event_from_json(row["event_json"])
            events.append(event)
            max_sequence = max(max_sequence, event.sequence)
        self._events[run_id] = events
        self._sequences[run_id] = max(self._sequences.get(run_id, 0), max_sequence)

    def _load_run_locked(self, run_id: str) -> RunDetail | None:
        row = self._db().execute(
            "SELECT run_json FROM trace_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        run = self._run_from_json(row["run_json"])
        if run.id not in self._runs:
            self._run_keys_fifo.append(run.id)
            self._events[run.id] = []
            self._sequences[run.id] = 0
        self._runs[run.id] = run
        return run

    def get_run(self, run_id: str) -> RunDetail | None:
        run = super().get_run(run_id)
        if run is not None:
            return run
        with self._lock:
            loaded = self._load_run_locked(run_id)
            return copy.deepcopy(loaded) if loaded is not None else None

    def get_timeline(self, run_id: str) -> list[TraceEvent]:
        with self._lock:
            if run_id not in self._runs:
                if self._load_run_locked(run_id) is None:
                    return []
            self._load_timeline_locked(run_id)
            return copy.deepcopy(self._events[run_id])

    def subscribe(self, run_id: str, **kwargs):
        with self._lock:
            if run_id not in self._runs and self._load_run_locked(run_id) is None:
                raise KeyError(run_id)
            self._load_timeline_locked(run_id)
        return super().subscribe(run_id, **kwargs)

    def get_runs(self, limit: int = 50, offset: int = 0, **filters) -> PaginatedRuns:
        limit = min(limit, 100)
        where = []
        params: list[Any] = []
        for key, column in RUN_FILTER_COLUMNS.items():
            value = filters.get(key)
            if value is not None:
                where.append(f"{column} = ?")
                params.append(str(value))
        sql = "SELECT run_json FROM trace_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        count_sql = sql.replace("SELECT run_json", "SELECT COUNT(*) AS total", 1)
        total_row = self._db().execute(count_sql, params).fetchone()
        total = int(total_row["total"] or 0) if total_row else 0
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._db().execute(sql, params).fetchall()
        items = [
            RunSummary(**self._run_from_json(row["run_json"]).model_dump())
            for row in rows
        ]
        return PaginatedRuns(items=items, total=total)
