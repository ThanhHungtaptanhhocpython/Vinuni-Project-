import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

# `src/cloud-agent` is not a valid package name (hyphen); put it on sys.path.
_CLOUD_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_CLOUD_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLOUD_AGENT_ROOT))

# Repo root must also be importable for `src.config` / `src.main`.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Disable tracing before importing the application. Importing src.main may
# initialize LangChain/LangSmith integrations, which otherwise leave a
# background thread alive after pytest has finished.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGFUSE_ENABLED"] = "false"
os.environ["APP_PORT"] = "8000"
os.environ["APP_ENV"] = "test"
# Dedicated Postgres DB so tests never wipe the local `p172` app database.
# Override with TEST_DATABASE_URL if needed.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5433/p172_test",
    ),
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from config import get_settings  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from main import app  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:5433/p172_test"
)
_TABLES = (
    "refresh_tokens",
    "users",
    "robots",
    "edge_registry",
    "session_messages",
    "sessions",
    "schedule_runs",
    "schedules",
    "runs",
)


def _admin_url(database_url: str) -> str:
    """Swap the DB name for `postgres` so we can CREATE DATABASE."""
    if "/" not in database_url.rsplit("@", 1)[-1]:
        return database_url
    head, _name = database_url.rsplit("/", 1)
    return f"{head}/postgres"


def _ensure_database(database_url: str) -> None:
    db_name = database_url.rstrip("/").rsplit("/", 1)[-1]
    if not db_name or db_name.startswith("?"):
        raise RuntimeError(f"Invalid test DATABASE_URL: {database_url}")
    admin = create_engine(_admin_url(database_url), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
        "DATABASE_URL", DEFAULT_TEST_DATABASE_URL
    )
    if not url.startswith("postgresql"):
        raise pytest.UsageError(
            "Cloud-agent tests require PostgreSQL. "
            f"Got DATABASE_URL={url!r}. "
            "Start Postgres (`docker compose up -d postgres`) and use "
            f"{DEFAULT_TEST_DATABASE_URL}."
        )
    try:
        _ensure_database(url)
    except Exception as exc:  # noqa: BLE001
        raise pytest.UsageError(
            "Cannot reach PostgreSQL for tests. "
            "Run `docker compose up -d postgres` (port 5433), then retry.\n"
            f"Underlying error: {exc}"
        ) from exc
    return url


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch, test_database_url):
    """Keep API tests deterministic regardless of developer .env."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "database_url", test_database_url)
    monkeypatch.setattr(settings, "auth_required", False)
    # TestClient is http:// — Secure cookies would not be stored/sent by httpx.
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    monkeypatch.setattr(settings, "mcp_endpoint_url", "")
    monkeypatch.setattr(settings, "edge_jwt_secret", "test-only-edge-jwt-secret-do-not-use-32")
    return settings


def _reseed_default_user(conn) -> None:
    """Restore the `DEFAULT_OWNER_ID` row `_ensure_owner_columns` inserts.

    `users` is in `_TABLES` so cross-test auth state doesn't leak — but that
    TRUNCATE also deletes the seeded dev user, and `get_connection`'s schema
    init (which normally re-inserts it) only runs once per URL per cached
    engine, not on every call. Any test that creates a session/run/schedule
    for `DEFAULT_OWNER_ID` right after a truncate hits a bare FK violation
    without this — reseed it ourselves, same values as `_ensure_owner_columns`.
    """
    from datetime import UTC, datetime

    from stores.db import DEFAULT_OWNER_ID

    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    conn.execute(
        """
        INSERT INTO users (id, email, google_sub, name, picture_url, timezone, locale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO NOTHING
        """,
        (DEFAULT_OWNER_ID, "dev@localhost", "dev_local", "Dev User", "", "Asia/Ho_Chi_Minh", "vi", now),
    )
    conn.commit()


@pytest.fixture(autouse=True)
def _clean_postgres(test_database_url):
    """Schema + empty tables before/after each test."""
    from auth.store import auth_store
    from stores.db import get_connection, reset_connection_cache
    from stores.runs import run_store
    from stores.schedules import schedule_store
    from stores.sessions import session_store

    reset_connection_cache()
    conn, _ = get_connection(test_database_url)
    conn.execute(
        "TRUNCATE TABLE "
        + ", ".join(_TABLES)
        + " RESTART IDENTITY CASCADE"
    )
    conn.commit()
    # `auth_store.clear()` DELETEs from `users` too (it owns the auth side of
    # that table) — reseeding BEFORE it would just get wiped again. Reseed
    # last, after every reset that might touch `users`.
    auth_store.clear()
    session_store.reset()
    schedule_store.reset()
    run_store.reset()
    _reseed_default_user(conn)

    yield

    reset_connection_cache()
    conn, _ = get_connection(test_database_url)
    conn.execute(
        "TRUNCATE TABLE "
        + ", ".join(_TABLES)
        + " RESTART IDENTITY CASCADE"
    )
    conn.commit()
    auth_store.clear()
    session_store.reset()
    schedule_store.reset()
    run_store.reset()
    _reseed_default_user(conn)


@pytest.fixture
def workspace_tmp_path() -> Path:
    path = Path("test_tmp") / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing API endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_llm():
    """Mock LLM to avoid calling OpenAI during tests."""
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock
