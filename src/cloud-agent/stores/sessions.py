"""Chat sessions + messages persisted in PostgreSQL (per-user)."""

from __future__ import annotations

import threading
from typing import Any, Literal

from stores.db import DEFAULT_OWNER_ID, DbConnection, Row, get_connection
from stores.ids import new_id, utc_now_iso

SessionSource = Literal["CHAT", "SCHEDULE"]
AUTO_SESSION_TITLE = "Phiên tự động"
NEW_SESSION_TITLE = "Phiên mới"


def _is_empty_draft(row: dict[str, Any]) -> bool:
    """Opened /control/new but never sent a message — not conversation history."""
    return (
        row.get("source", "CHAT") == "CHAT"
        and row.get("title") == NEW_SESSION_TITLE
        and not row.get("last_message_preview")
        and not row.get("active_run")
    )


def _from_row(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"] or DEFAULT_OWNER_ID,
        "title": row["title"],
        "robot_id": row["robot_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "active_run": row["active_run"],
        "last_message_preview": row["last_message_preview"],
        "source": row["source"] or "CHAT",
    }


class SessionStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._lock = threading.Lock()
        self._database_url = database_url
        self._conn: DbConnection | None = None
        self._path: str | None = None

    @property
    def path(self) -> str:
        self._db()
        return self._path or "postgresql"

    def _db(self) -> DbConnection:
        conn, path = get_connection(self._database_url)
        self._conn = conn
        self._path = path
        return conn

    def ensure_db(self) -> str:
        self._db()
        return self.path

    def reset(self) -> None:
        with self._lock:
            conn = self._db()
            conn.execute("DELETE FROM session_messages")
            conn.execute("DELETE FROM sessions")
            conn.commit()

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row.get("user_id", DEFAULT_OWNER_ID),
            "title": row["title"],
            "robot_id": row["robot_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "active_run": row["active_run"],
            "last_message_preview": row["last_message_preview"],
            "source": row.get("source", "CHAT"),
        }

    def list(
        self,
        *,
        user_id: str,
        q: str | None = None,
        robot_id: str | None = None,
        include_hidden: bool = False,
        include_empty: bool = False,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query = (q or "").strip().lower()
        with self._lock:
            rows = [
                _from_row(r)
                for r in self._db().execute(
                    """
                    SELECT * FROM sessions
                    WHERE user_id = ?
                    ORDER BY updated_at DESC, created_at DESC, id DESC
                    """,
                    (user_id,),
                )
            ]
        if robot_id:
            rows = [s for s in rows if s["robot_id"] == robot_id]
        if not include_hidden:
            rows = [
                s
                for s in rows
                if s.get("source") != "SCHEDULE" and s["title"] != AUTO_SESSION_TITLE
            ]
        if not include_empty:
            rows = [s for s in rows if not _is_empty_draft(s)]
        if query:
            rows = [
                s
                for s in rows
                if query in s["title"].lower()
                or query in (s.get("last_message_preview") or "").lower()
            ]
        start = 0
        if cursor:
            for i, row in enumerate(rows):
                if row["id"] == cursor:
                    start = i + 1
                    break
        page = rows[start : start + limit]
        next_cursor = page[-1]["id"] if start + limit < len(rows) and page else None
        return [self._public(s) for s in page], next_cursor

    def count(self, *, user_id: str | None = None) -> int:
        with self._lock:
            if user_id is None:
                value = self._db().execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            else:
                value = self._db().execute(
                    "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            return int(value)

    def get(self, session_id: str, *, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
            return self._public(_from_row(row)) if row else None

    def latest(self, *, user_id: str, robot_id: str | None = None) -> dict[str, Any] | None:
        """Most recently updated chat session (including empty drafts)."""
        sql = """
            SELECT * FROM sessions
            WHERE user_id = ? AND source != 'SCHEDULE' AND title != ?
        """
        params: list[Any] = [user_id, AUTO_SESSION_TITLE]
        if robot_id:
            sql += " AND robot_id = ?"
            params.append(robot_id)
        sql += " ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._db().execute(sql, params).fetchone()
            return self._public(_from_row(row)) if row else None

    def create(
        self,
        robot_id: str,
        *,
        user_id: str,
        title: str = NEW_SESSION_TITLE,
        source: SessionSource = "CHAT",
    ) -> dict[str, Any]:
        with self._lock:
            conn = self._db()
            if source == "CHAT" and title == NEW_SESSION_TITLE:
                existing = conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE user_id = ? AND robot_id = ? AND source = 'CHAT' AND title = ?
                      AND (last_message_preview IS NULL OR last_message_preview = '')
                      AND active_run IS NULL
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (user_id, robot_id, NEW_SESSION_TITLE),
                ).fetchone()
                if existing:
                    return self._public(_from_row(existing))
            now = utc_now_iso()
            session_id = new_id("ses")
            conn.execute(
                """
                INSERT INTO sessions (
                    id, user_id, title, robot_id, created_at, updated_at,
                    active_run, last_message_preview, source
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (session_id, user_id, title, robot_id, now, now, source),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return self._public(_from_row(row))

    def get_or_create(
        self, session_id: str | None, robot_id: str, *, user_id: str
    ) -> dict[str, Any]:
        if session_id:
            existing = self.get(session_id, user_id=user_id)
            if existing:
                return existing
        return self.create(robot_id, user_id=user_id)

    def active_session_id_for_robot(self, robot_id: str) -> str | None:
        """Return any session id that currently has an active run on this robot.

        Used for live busy overlays (dashboard status), not to lock chat to one session.
        """
        with self._lock:
            conn = self._db()
            row = conn.execute(
                """
                SELECT id
                FROM sessions
                WHERE robot_id = ? AND active_run IS NOT NULL
                ORDER BY updated_at DESC, created_at DESC, id DESC
                LIMIT 1
                """,
                (robot_id,),
            ).fetchone()
            return str(row["id"]) if row else None

    def rename(self, session_id: str, title: str, *, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._db()
            cur = conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (title, utc_now_iso(), session_id, user_id),
            )
            if cur.rowcount == 0:
                return None
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
            return self._public(_from_row(row)) if row else None

    def delete(self, session_id: str, *, user_id: str) -> str | None:
        """None = deleted, 'missing', 'busy'."""
        with self._lock:
            conn = self._db()
            row = conn.execute(
                "SELECT active_run FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
            if row is None:
                return "missing"
            if row["active_run"]:
                return "busy"
            conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
            conn.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
            conn.commit()
            return None

    def set_active_run(self, session_id: str, run_id: str | None) -> None:
        with self._lock:
            conn = self._db()
            conn.execute(
                "UPDATE sessions SET active_run = ?, updated_at = ? WHERE id = ?",
                (run_id, utc_now_iso(), session_id),
            )
            conn.commit()

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        message_id = new_id("msg")
        preview = content.strip().replace("\n", " ")[:120] or None
        with self._lock:
            conn = self._db()
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            session = _from_row(row)
            conn.execute(
                """
                INSERT INTO session_messages (id, session_id, role, content, created_at, run_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, now, run_id),
            )
            new_title = session["title"]
            if (
                role == "user"
                and session["source"] != "SCHEDULE"
                and (not new_title or new_title == NEW_SESSION_TITLE)
            ):
                new_title = content.strip()[:48] or NEW_SESSION_TITLE
            active_run = session["active_run"]
            # User turns set active_run for run lifecycle; robot BUSY comes from
            # take_action/go_home on Edge only (see services.robot_status).
            if run_id is not None and role == "user":
                active_run = run_id
            conn.execute(
                """
                UPDATE sessions
                SET last_message_preview = ?, updated_at = ?, title = ?, active_run = ?
                WHERE id = ?
                """,
                (preview, now, new_title, active_run, session_id),
            )
            conn.commit()
            return {
                "id": message_id,
                "session_id": session_id,
                "role": role,
                "content": content,
                "created_at": now,
                "feedback": None,
                "run_id": run_id,
            }

    def list_messages(self, session_id: str, *, user_id: str) -> list[dict[str, Any]] | None:
        with self._lock:
            conn = self._db()
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
            if exists is None:
                return None
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, created_at, feedback, run_id
                FROM session_messages
                WHERE session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "session_id": r["session_id"],
                    "role": r["role"],
                    "content": r["content"],
                    "created_at": r["created_at"],
                    "feedback": r["feedback"],
                    "run_id": r["run_id"],
                }
                for r in rows
            ]

    def set_message_feedback(
        self,
        session_id: str,
        message_id: str,
        feedback: str | None,
        *,
        user_id: str,
    ) -> dict[str, Any] | None:
        """Like/dislike on one assistant message. `None` clears an existing pick."""
        with self._lock:
            conn = self._db()
            owns_session = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
            if owns_session is None:
                return None
            cur = conn.execute(
                """
                UPDATE session_messages
                SET feedback = ?
                WHERE id = ? AND session_id = ? AND role = 'assistant'
                """,
                (feedback, message_id, session_id),
            )
            if cur.rowcount == 0:
                return None
            conn.commit()
            row = conn.execute(
                """
                SELECT id, session_id, role, content, created_at, feedback
                FROM session_messages WHERE id = ?
                """,
                (message_id,),
            ).fetchone()
            return {
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "feedback": row["feedback"],
            }


session_store = SessionStore()
