"""Ground-truth registry of robots that a real Edge device has registered.

Separate from `stores.robots` on purpose: `robots` is what a *user* has
paired into their account (one row per user+robot_id); `edge_registry` is
what the Edge itself has proven it owns (one row per robot_id, written only
by a valid JWT registration call). Pairing (`POST /robots`) checks against
this table so a user can't claim an id no Edge has ever registered.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from stores.db import DbConnection, Row, get_connection
from stores.ids import utc_now_iso


def _from_row(row: Row) -> dict[str, Any]:
    return {
        "robot_id": row["robot_id"],
        "robot_type": row["robot_type"],
        "base_url": row["base_url"] or "",
        "capabilities": json.loads(row["capabilities"] or "[]"),
        "registered_at": row["registered_at"],
        "last_seen_at": row["last_seen_at"],
    }


class EdgeRegistryStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url
        self._lock = threading.Lock()

    def _db(self) -> DbConnection:
        conn, _label = get_connection(self._database_url)
        return conn

    def reset(self) -> None:
        with self._lock:
            conn = self._db()
            conn.execute("DELETE FROM edge_registry")
            conn.commit()

    def get(self, robot_id: str) -> dict[str, Any] | None:
        row = self._db().execute(
            "SELECT * FROM edge_registry WHERE robot_id = ?", (robot_id,)
        ).fetchone()
        return _from_row(row) if row is not None else None

    def register_if_new(
        self, *, robot_id: str, robot_type: str, capabilities: list[dict[str, Any]], base_url: str = ""
    ) -> tuple[dict[str, Any], bool]:
        """Insert on first registration; an id already present is left as-is
        (`base_url` + `last_seen_at` are refreshed) — the Edge re-announcing
        itself does not silently change what a user already paired against."""
        existing = self.get(robot_id)
        now = utc_now_iso()
        conn = self._db()
        if existing is not None:
            conn.execute(
                "UPDATE edge_registry SET base_url = ?, last_seen_at = ? WHERE robot_id = ?",
                (base_url, now, robot_id),
            )
            conn.commit()
            return self.get(robot_id), False  # type: ignore[return-value]

        conn.execute(
            """
            INSERT INTO edge_registry (
                robot_id, robot_type, base_url, capabilities, registered_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (robot_id, robot_type, base_url, json.dumps(capabilities), now, now),
        )
        conn.commit()
        return self.get(robot_id), True  # type: ignore[return-value]


edge_registry_store = EdgeRegistryStore()
