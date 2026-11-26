"""Long-term memory metadata persisted in the primary database."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from stores.db import DbConnection, get_connection
from stores.ids import new_id, utc_now_iso

MEMORY_TYPES = frozenset({"summary", "fact", "preference", "decision"})
DELETE_FILTER_COLUMNS = {
    "session_id": "session_id",
    "user_id": "user_id",
}


@dataclass(frozen=True)
class StoredMemory:
    id: str
    user_id: str
    session_id: str
    run_id: str
    memory_type: str
    content: str
    importance: float
    confidence: float
    qdrant_point_id: str
    created_at: str
    updated_at: str
    deleted_at: str | None = None

    def to_payload(self) -> dict:
        return {
            "memory_id": self.id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "memory_type": self.memory_type,
            "content": self.content,
            "created_at": self.created_at,
            "importance": self.importance,
            "confidence": self.confidence,
        }


def _row_to_memory(row: Mapping[str, Any]) -> StoredMemory:
    return StoredMemory(
        id=row["id"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        run_id=row["run_id"],
        memory_type=row["memory_type"],
        content=row["content"],
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        qdrant_point_id=row["qdrant_point_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )


class LongTermMemoryStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._lock = threading.Lock()
        self._database_url = database_url
        self._conn: DbConnection | None = None

    def _db(self) -> DbConnection:
        conn, _ = get_connection(self._database_url)
        self._conn = conn
        return conn

    def reset(self) -> None:
        with self._lock:
            conn = self._db()
            conn.execute("DELETE FROM long_term_memories")
            conn.commit()

    def create(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        memory_type: str,
        content: str,
        importance: float,
        confidence: float,
        qdrant_point_id: str,
    ) -> StoredMemory:
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"Unsupported memory_type: {memory_type}")
        now = utc_now_iso()
        memory_id = new_id("mem")
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                INSERT INTO long_term_memories (
                    id, user_id, session_id, run_id, memory_type, content,
                    importance, confidence, qdrant_point_id, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    memory_id,
                    user_id,
                    session_id,
                    run_id,
                    memory_type,
                    content,
                    float(importance),
                    float(confidence),
                    qdrant_point_id,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM long_term_memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return _row_to_memory(row)

    def list_active(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[StoredMemory]:
        clauses = ["deleted_at IS NULL"]
        params: list[str] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        sql = "SELECT * FROM long_term_memories WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC"
        with self._lock:
            rows = self._db().execute(sql, params).fetchall()
        return [_row_to_memory(row) for row in rows]

    def soft_delete_by_session(self, session_id: str) -> list[str]:
        return self._soft_delete("session_id", session_id)

    def soft_delete_by_user(self, user_id: str) -> list[str]:
        return self._soft_delete("user_id", user_id)

    def _soft_delete(self, field: str, value: str) -> list[str]:
        column = DELETE_FILTER_COLUMNS.get(field)
        if column is None:
            raise ValueError(f"Unsupported long-term memory delete field: {field}")
        now = utc_now_iso()
        with self._lock:
            conn = self._db()
            rows = conn.execute(
                f"SELECT qdrant_point_id FROM long_term_memories WHERE {column} = ? AND deleted_at IS NULL",
                (value,),
            ).fetchall()
            point_ids = [row["qdrant_point_id"] for row in rows]
            conn.execute(
                f"UPDATE long_term_memories SET deleted_at = ?, updated_at = ? WHERE {column} = ? AND deleted_at IS NULL",
                (now, now, value),
            )
            conn.commit()
        return point_ids


long_term_memory_store = LongTermMemoryStore()
