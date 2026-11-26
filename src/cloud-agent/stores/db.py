"""Database helpers for console persistence (PostgreSQL only).

`DATABASE_URL` examples:
  postgresql+psycopg://user:pass@localhost:5433/p172
  postgresql://user:pass@localhost:5433/p172   (normalized to +psycopg)

Pytest uses a separate database (`p172_test` by default).

Store code uses `?` placeholders and mapping rows via a thin SQLAlchemy wrapper.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

DEFAULT_POSTGRES_URL = "postgresql+psycopg://postgres:postgres@localhost:5433/p172"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    google_sub TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    picture_url TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    locale TEXT NOT NULL DEFAULT 'vi',
    created_at TEXT NOT NULL,
    password_hash TEXT
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    family_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_family ON refresh_tokens (family_id);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    robot_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active_run TEXT,
    last_message_preview TEXT,
    source TEXT NOT NULL DEFAULT 'CHAT'
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_robot ON sessions (robot_id);

CREATE TABLE IF NOT EXISTS session_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    feedback TEXT,
    run_id TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_session
    ON session_messages (session_id, created_at);

CREATE TABLE IF NOT EXISTS session_task_memory (
    session_id TEXT PRIMARY KEY,
    memory_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS long_term_memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.5,
    qdrant_point_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ltm_user
    ON long_term_memories (user_id, deleted_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ltm_session
    ON long_term_memories (session_id, deleted_at);

CREATE TABLE IF NOT EXISTS robots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    model TEXT NOT NULL,
    robot_status TEXT NOT NULL DEFAULT 'IDLE',
    connection_status TEXT NOT NULL DEFAULT 'OFFLINE',
    stream_connected INTEGER NOT NULL DEFAULT 0,
    edge_version TEXT NOT NULL DEFAULT '',
    max_temperature INTEGER NOT NULL DEFAULT 0,
    error_flags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_robots_created ON robots (created_at DESC);

CREATE TABLE IF NOT EXISTS edge_registry (
    robot_id TEXT PRIMARY KEY,
    robot_type TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '[]',
    registered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    robot_id TEXT NOT NULL,
    task_prompt TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    cron_description TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    overlap_policy TEXT NOT NULL DEFAULT 'SKIP',
    max_duration_s INTEGER NOT NULL DEFAULT 180,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedules_created ON schedules (created_at DESC);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    started_at TEXT,
    status TEXT NOT NULL,
    duration_s INTEGER,
    run_id TEXT,
    reason_code TEXT,
    FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_sched
    ON schedule_runs (schedule_id, scheduled_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    session_id TEXT,
    schedule_id TEXT,
    robot_id TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    input_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    duration_s INTEGER,
    trace_id TEXT,
    outcome TEXT,
    error_code TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS trace_runs (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    robot_id TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    input_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    run_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_runs_created ON trace_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_runs_user ON trace_runs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_runs_trace ON trace_runs (trace_id);

CREATE TABLE IF NOT EXISTS trace_events (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    type TEXT NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES trace_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_trace_events_run_seq
    ON trace_events (run_id, sequence);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    google_sub TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    picture_url TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    locale TEXT NOT NULL DEFAULT 'vi',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    family_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_family ON refresh_tokens (family_id);
"""

DEFAULT_OWNER_ID = "user_dev"

_lock = threading.Lock()
_engines: dict[str, Engine] = {}
_connections: dict[str, DbConnection] = {}


class Row(Mapping[str, Any]):
    """Dict-like row with integer index access."""

    __slots__ = ("_data", "_values")

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = dict(data)
        self._values = list(self._data.values())

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def keys(self):  # type: ignore[override]
        return self._data.keys()


class CursorResult:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.rowcount = int(result.rowcount or 0)

    def fetchone(self) -> Row | None:
        mapping = self._result.mappings().fetchone()
        return Row(mapping) if mapping is not None else None

    def fetchall(self) -> list[Row]:
        return [Row(m) for m in self._result.mappings().fetchall()]

    def __iter__(self) -> Iterator[Row]:
        for mapping in self._result.mappings():
            yield Row(mapping)


class DbConnection:
    """Thin SQLAlchemy wrapper: `?` placeholders + mapping rows."""

    def __init__(self, conn: Connection, label: str) -> None:
        self._conn = conn
        self.label = label

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> CursorResult:
        statement, bind = _bind_qmark(sql, params)
        try:
            return CursorResult(self._conn.execute(text(statement), bind))
        except Exception:
            # Postgres aborts the whole transaction on the first error. This
            # process reuses one connection, so leave it usable for the next call.
            self._conn.rollback()
            raise

    def executescript(self, script: str) -> None:
        for statement in _split_sql(script):
            self._conn.execute(text(statement))

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def normalize_database_url(database_url: str | None = None) -> str:
    settings = get_settings()
    if database_url is None:
        database_url = settings.database_url
    url = (database_url or "").strip()
    if not url:
        return DEFAULT_POSTGRES_URL
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://") and "+psycopg" not in url and "+asyncpg" not in url:
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if not url.startswith("postgresql"):
        raise ValueError(
            "DATABASE_URL must be a PostgreSQL URL "
            f"(postgresql+psycopg://…), got {url!r}"
        )
    return url


def database_label(database_url: str | None = None) -> str:
    url = normalize_database_url(database_url)
    return re.sub(r":([^:@/]+)@", ":***@", url)


def get_connection(database_url: str | None = None) -> tuple[DbConnection, str]:
    url = normalize_database_url(database_url)
    label = database_label(url)
    with _lock:
        conn = _connections.get(url)
        if conn is None:
            conn = _open(url, label)
            _connections[url] = conn
        return conn, label


def reset_connection_cache() -> None:
    """Close cached engines/connections — used by tests that switch DBs."""
    with _lock:
        for conn in _connections.values():
            try:
                conn.close()
            except Exception:
                pass
        for engine in _engines.values():
            try:
                engine.dispose()
            except Exception:
                pass
        _connections.clear()
        _engines.clear()


def _open(url: str, label: str) -> DbConnection:
    engine = _engines.get(url)
    if engine is None:
        engine = create_engine(url, pool_pre_ping=True)
        _engines[url] = engine

    conn = engine.connect()
    db = DbConnection(conn, label)
    db.executescript(SCHEMA)
    _ensure_owner_columns(db)
    _rewrite_status_bubbles(db)
    db.commit()
    return db


def _ensure_owner_columns(conn: DbConnection) -> None:
    """Add user_id + FKs to existing tables (CREATE IF NOT EXISTS won't alter them)."""
    from datetime import UTC, datetime

    # Cùng lý do: `CREATE TABLE IF NOT EXISTS` bỏ qua database đã có từ trước
    # `password_hash` (email/mật khẩu) — thêm cột bằng tay, idempotent nhờ
    # `IF NOT EXISTS` (Postgres hỗ trợ trực tiếp, khác SQLite).
    conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT")
    conn.execute("ALTER TABLE session_messages ADD COLUMN IF NOT EXISTS feedback TEXT")
    conn.execute("ALTER TABLE session_messages ADD COLUMN IF NOT EXISTS run_id TEXT")
    conn.execute("ALTER TABLE robots ADD COLUMN IF NOT EXISTS stream_connected INTEGER NOT NULL DEFAULT 0")
    # Older cloud DBs created `robots` before health columns existed; CREATE IF NOT
    # EXISTS does not alter them, so cloud-agent crashed on startup (_from_row).
    conn.execute("ALTER TABLE robots ADD COLUMN IF NOT EXISTS edge_version TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "ALTER TABLE robots ADD COLUMN IF NOT EXISTS max_temperature INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "ALTER TABLE robots ADD COLUMN IF NOT EXISTS error_flags TEXT NOT NULL DEFAULT '[]'"
    )
    conn.execute("ALTER TABLE edge_registry ADD COLUMN IF NOT EXISTS base_url TEXT NOT NULL DEFAULT ''")

    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    conn.execute(
        """
        INSERT INTO users (id, email, google_sub, name, picture_url, timezone, locale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            DEFAULT_OWNER_ID,
            "dev@localhost",
            "dev_local",
            "Dev User",
            "",
            "Asia/Ho_Chi_Minh",
            "vi",
            now,
        ),
    )
    _migrate_robots_drop_pairing(conn)
    for table in ("sessions", "schedules", "runs"):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id TEXT")
        conn.execute(
            f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL OR user_id = ''",
            (DEFAULT_OWNER_ID,),
        )
        # Orphan owners → fallback so FK can be applied
        conn.execute(
            f"""
            UPDATE {table} AS t
            SET user_id = ?
            WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = t.user_id)
            """,
            (DEFAULT_OWNER_ID,),
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules (user_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_runs_user ON runs (user_id, created_at DESC)"
    )
    for table in ("sessions", "schedules", "runs"):
        constraint = f"{table}_user_id_fkey"
        exists = conn.execute(
            """
            SELECT 1
            FROM information_schema.table_constraints
            WHERE table_schema = 'public'
              AND table_name = ?
              AND constraint_name = ?
              AND constraint_type = 'FOREIGN KEY'
            """,
            (table, constraint),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT {constraint}
            FOREIGN KEY (user_id) REFERENCES users(id)
            """
        )


def _migrate_robots_drop_pairing(conn: DbConnection) -> None:
    """Single-VM deploy: robots are global, not owned by a user account."""
    conn.execute("ALTER TABLE robots DROP CONSTRAINT IF EXISTS robots_user_id_fkey")
    conn.execute("DROP INDEX IF EXISTS idx_robots_user")
    conn.execute("ALTER TABLE robots DROP COLUMN IF EXISTS user_id")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_robots_created ON robots (created_at DESC)"
    )


def _bind_qmark(
    sql: str, params: Sequence[Any] | None
) -> tuple[str, dict[str, Any]]:
    if not params:
        return sql, {}
    bind: dict[str, Any] = {}
    out: list[str] = []
    idx = 0
    for ch in sql:
        if ch == "?":
            key = f"p{idx}"
            out.append(f":{key}")
            bind[key] = params[idx]
            idx += 1
        else:
            out.append(ch)
    if idx != len(params):
        raise ValueError(
            f"SQL placeholder count ({idx}) != params ({len(params)}): {sql!r}"
        )
    return "".join(out), bind


def _split_sql(script: str) -> Iterable[str]:
    for raw in script.split(";"):
        statement = raw.strip()
        if statement:
            yield statement


def _rewrite_status_bubbles(conn: DbConnection) -> None:
    """Old runs persisted the enum (FAILED) instead of the user-facing error."""
    replacements = (
        ("FAILED", "Robot đang offline hoặc không thể kết nối."),
        ("TIMEOUT", "Agent chạy quá thời gian chờ."),
        ("CANCELLED", "Đã huỷ."),
    )
    for status, text_value in replacements:
        conn.execute(
            """
            UPDATE session_messages
            SET content = ?
            WHERE role = 'assistant' AND content = ?
            """,
            (text_value, status),
        )
        conn.execute(
            "UPDATE sessions SET last_message_preview = ? WHERE last_message_preview = ?",
            (text_value, status),
        )
