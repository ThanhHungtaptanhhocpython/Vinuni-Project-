"""Robot registry persisted in the shared SQL store.

Single-VM deploy: robots are global (no per-user pairing). Capabilities and
joint telemetry are NOT persisted — they are what the Edge device declares
when it connects, not something the console records.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from config import get_settings

from stores.db import DbConnection, Row, get_connection
from stores.ids import utc_now_iso

_CAPABILITIES = [
    {"name": "capture_tool", "version": "1.0"},
    {"name": "get_robot_state", "version": "1.0"},
    {"name": "take_action", "version": "2.1"},
    {"name": "stop_robot", "version": "1.0"},
    {"name": "go_home", "version": "1.0"},
    {"name": "reset_error", "version": "1.0"},
    {"name": "health_check", "version": "1.0"},
    {"name": "get_task_status", "version": "1.0"},
]

_JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow",
    "wrist_1",
    "wrist_2",
    "wrist_3",
]

_DEFAULT_ROBOT_ID = "robot-001"


def _joints(*, healthy: bool) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "present_position": -30 + i * 25,
            "present_temperature": 34 + i if healthy else 58 + i,
            "present_load": 10 + i,
            "torque_enabled": healthy,
            "moving": False,
            "error_flags": [] if healthy else (["OVERLOAD"] if i == 2 else []),
        }
        for i, name in enumerate(_JOINT_NAMES)
    ]


def _from_row(row: Row) -> dict[str, Any]:
    raw_flags = row["error_flags"] if "error_flags" in row else "[]"
    error_flags = json.loads(raw_flags or "[]")
    return {
        "id": row["id"],
        "name": row["name"],
        "model": row["model"],
        "robot_status": row["robot_status"],
        "connection_status": row["connection_status"],
        "edge_version": (row["edge_version"] if "edge_version" in row else None) or "",
        "health": {
            "has_error": bool(error_flags),
            "error_flags": error_flags,
            "max_temperature": int(
                (row["max_temperature"] if "max_temperature" in row else None) or 0
            ),
        },
        "capabilities": list(_CAPABILITIES),
        "joints": _joints(healthy=not error_flags),
    }


class RobotStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url
        self._lock = threading.Lock()

    def _db(self) -> DbConnection:
        conn, _label = get_connection(self._database_url)
        return conn

    def reset(self) -> None:
        with self._lock:
            conn = self._db()
            conn.execute("DELETE FROM robots")
            conn.commit()

    def list(self) -> list[dict[str, Any]]:
        rows = self._db().execute(
            "SELECT * FROM robots ORDER BY created_at ASC"
        ).fetchall()
        return [_from_row(r) for r in rows]

    def get(self, robot_id: str) -> dict[str, Any] | None:
        row = self._db().execute(
            "SELECT * FROM robots WHERE id = ?", (robot_id,)
        ).fetchone()
        return _from_row(row) if row is not None else None

    def exists(self, robot_id: str) -> bool:
        row = self._db().execute(
            "SELECT 1 FROM robots WHERE id = ?", (robot_id,)
        ).fetchone()
        return row is not None

    def set_status(self, robot_id: str, robot_status: str) -> None:
        conn = self._db()
        conn.execute(
            "UPDATE robots SET robot_status = ?, updated_at = ? WHERE id = ?",
            (robot_status, utc_now_iso(), robot_id),
        )
        conn.commit()

    def is_stream_connected(self, robot_id: str) -> bool:
        row = self._db().execute(
            "SELECT stream_connected FROM robots WHERE id = ?",
            (robot_id,),
        ).fetchone()
        return bool(row is not None and int(row["stream_connected"] or 0))

    def mark_connected(self, robot_id: str) -> dict[str, Any] | None:
        conn = self._db()
        conn.execute(
            """
            UPDATE robots
            SET connection_status = 'ONLINE', stream_connected = 1, updated_at = ?
            WHERE id = ?
            """,
            (utc_now_iso(), robot_id),
        )
        conn.commit()
        return self.get(robot_id)

    def mark_disconnected(self, robot_id: str) -> dict[str, Any] | None:
        conn = self._db()
        conn.execute(
            """
            UPDATE robots
            SET connection_status = 'OFFLINE', stream_connected = 0, updated_at = ?
            WHERE id = ?
            """,
            (utc_now_iso(), robot_id),
        )
        conn.commit()
        return self.get(robot_id)

    def create(
        self,
        *,
        robot_id: str,
        name: str,
        model: str,
        robot_status: str = "IDLE",
        connection_status: str = "OFFLINE",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        conn = self._db()
        conn.execute(
            """
            INSERT INTO robots (
                id, name, model, robot_status, connection_status,
                stream_connected, edge_version, max_temperature, error_flags,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, '', 0, '[]', ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                model = EXCLUDED.model,
                updated_at = EXCLUDED.updated_at
            """,
            (robot_id, name, model, robot_status, connection_status, now, now),
        )
        conn.commit()
        return self.get(robot_id)  # type: ignore[return-value]

    def delete(self, robot_id: str) -> bool:
        conn = self._db()
        cur = conn.execute("DELETE FROM robots WHERE id = ?", (robot_id,))
        conn.commit()
        return cur.rowcount > 0

    def ensure_default_seeded(self) -> dict[str, Any]:
        """Ensure exactly one console robot exists (single-VM deploy).

        Prefer an Edge that already self-registered so `robots.id` matches
        `edge_registry.robot_id` (streams / MCP look up by that id). Otherwise
        seed a fixed `robot-001` from `EDGE_ROBOT_MODE`.
        """
        with self._lock:
            existing = self._db().execute(
                "SELECT * FROM robots ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if existing is not None:
                return _from_row(existing)

            registry = self._db().execute(
                "SELECT robot_id, robot_type FROM edge_registry ORDER BY last_seen_at DESC LIMIT 1"
            ).fetchone()
            if registry is not None:
                robot_id = str(registry["robot_id"])
                model = str(registry["robot_type"] or "franka")
                name = f"Cánh tay Edge ({model})"
                connection_status = "ONLINE"
            else:
                settings = get_settings()
                mode = settings.edge_robot_mode
                robot_id = _DEFAULT_ROBOT_ID
                model = "franka" if mode == "sim" else "so101"
                name = "Cánh tay Alpha (Sim)" if mode == "sim" else "Cánh tay Alpha (Real)"
                connection_status = "ONLINE" if mode == "sim" else "OFFLINE"

            return self.create(
                robot_id=robot_id,
                name=name,
                model=model,
                connection_status=connection_status,
            )

    def upsert_from_edge_registration(
        self, *, robot_id: str, robot_type: str
    ) -> dict[str, Any]:
        """Keep console robot row in sync when Edge self-registers."""
        existing = self.get(robot_id)
        if existing is not None:
            return existing
        with self._lock:
            rows = self._db().execute("SELECT id FROM robots").fetchall()
            # Single-robot deploy: replace a previous seed id with the real Edge id.
            if len(rows) == 1 and str(rows[0]["id"]) != robot_id:
                self._db().execute("DELETE FROM robots WHERE id = ?", (rows[0]["id"],))
                self._db().commit()
        return self.create(
            robot_id=robot_id,
            name=f"Cánh tay Edge ({robot_type})",
            model=robot_type,
            connection_status="ONLINE",
        )


robot_store = RobotStore()
