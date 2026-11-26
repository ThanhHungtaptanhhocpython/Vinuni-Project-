import os
from datetime import UTC, datetime

import pytest
from agents.db_trace_repo import DbTraceRepository
from agents.trace_repo import InMemoryTraceRepository
from models.schemas import RunDetail, RunSource, RunStatus, TraceEvent
from sqlalchemy import create_engine, text
from stores.db import reset_connection_cache

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5433/p172_test"


def _admin_url(database_url: str) -> str:
    base, sep, query = database_url.partition("?")
    head, _name = base.rsplit("/", 1)
    return f"{head}/postgres{sep}{query}" if sep else f"{head}/postgres"


def _with_connect_timeout(database_url: str) -> str:
    if "connect_timeout=" in database_url:
        return database_url
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}connect_timeout=2"


def _ensure_test_database(database_url: str) -> None:
    db_name = database_url.partition("?")[0].rstrip("/").rsplit("/", 1)[-1]
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


@pytest.fixture
def trace_database_url():
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
        "DATABASE_URL",
        DEFAULT_TEST_DATABASE_URL,
    )
    if not url.startswith("postgresql"):
        pytest.skip(f"DbTraceRepository tests require PostgreSQL, got {url!r}")
    url = _with_connect_timeout(url)
    try:
        _ensure_test_database(url)
    except Exception as exc:
        pytest.skip(f"Cannot reach PostgreSQL for DbTraceRepository test: {exc}")
    reset_connection_cache()
    yield url
    reset_connection_cache()


@pytest.fixture
def repo():
    r = InMemoryTraceRepository(max_items=3)
    r.reset_for_test()
    return r

def test_create_and_get_run(repo):
    run = RunDetail(
        id="run_1", trace_id="trc_1", session_id="ses_1", robot_id="rob_1", user_id="user_1",
        source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="hello", created_at=datetime.now(UTC).isoformat()
    )
    repo.create_run(run)

    fetched = repo.get_run("run_1")
    assert fetched.id == "run_1"
    assert fetched.status == RunStatus.RUNNING

def test_update_run_terminal_state(repo):
    run = RunDetail(
        id="run_1", trace_id="trc_1", session_id="ses_1", robot_id="rob_1", user_id="user_1",
        source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="hello", created_at=datetime.now(UTC).isoformat()
    )
    repo.create_run(run)
    repo.update_run("run_1", {"status": RunStatus.FAILED, "error": {"message": "failed"}})

    fetched = repo.get_run("run_1")
    assert fetched.status == RunStatus.FAILED
    assert fetched.error["message"] == "failed"

    # Try updating status again, it should not change since it's terminal
    repo.update_run("run_1", {"status": RunStatus.COMPLETED})
    fetched2 = repo.get_run("run_1")
    assert fetched2.status == RunStatus.FAILED

def test_add_event_and_timeline(repo):
    repo.add_event(TraceEvent(
        id="evt_1", run_id="run_1", trace_id="trc_1", sequence=0, timestamp=datetime.now(UTC).isoformat(), type="tool.started"
    ))
    timeline = repo.get_timeline("run_1")
    assert len(timeline) == 1
    assert timeline[0].id == "evt_1"
    assert timeline[0].sequence == 1


def test_getters_return_defensive_copies(repo):
    run = RunDetail(
        id="run_1", trace_id="trc_1", session_id="ses_1", robot_id="rob_1", user_id="user_1",
        source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="hello", created_at=datetime.now(UTC).isoformat()
    )
    repo.create_run(run)
    repo.add_event(TraceEvent(
        id="evt_1", run_id="run_1", trace_id="trc_1", sequence=0,
        timestamp=datetime.now(UTC).isoformat(), type="tool.started", metadata={"safe": "original"}
    ))

    fetched_run = repo.get_run("run_1")
    fetched_timeline = repo.get_timeline("run_1")
    fetched_list = repo.get_runs().items
    fetched_run.input_text = "mutated"
    fetched_timeline[0].metadata["safe"] = "mutated"
    fetched_list[0].input_text = "mutated"

    assert repo.get_run("run_1").input_text == "hello"
    assert repo.get_timeline("run_1")[0].metadata["safe"] == "original"
    assert repo.get_runs().items[0].input_text == "hello"

def test_eviction(repo):
    # Add 4 runs, max is 3
    for i in range(4):
        run = RunDetail(
            id=f"run_{i}", trace_id=f"trc_{i}", session_id="ses", robot_id="rob", user_id="user",
            source=RunSource.CHAT, status=RunStatus.COMPLETED, input_text="test", created_at=datetime.now(UTC).isoformat()
        )
        repo.create_run(run)

    runs = repo.get_runs(limit=10).items
    assert len(runs) == 3
    assert repo.get_run("run_0") is None
    assert repo.get_run("run_3") is not None

def test_fifo_creation_order_eviction(repo):
    # Active run created first
    run_0 = RunDetail(id="run_0", trace_id="trc_0", session_id="ses", robot_id="rob", user_id="user", source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="test", created_at=datetime.now(UTC).isoformat())
    repo.create_run(run_0)

    for i in range(1, 4):
        run = RunDetail(id=f"run_{i}", trace_id=f"trc_{i}", session_id="ses", robot_id="rob", user_id="user", source=RunSource.CHAT, status=RunStatus.COMPLETED, input_text="test", created_at=datetime.now(UTC).isoformat())
        repo.create_run(run)

    # run_1 should be evicted since it's the oldest terminal run
    assert repo.get_run("run_1") is None
    assert repo.get_run("run_0") is not None
    assert repo.get_run("run_2") is not None
    assert repo.get_run("run_3") is not None

    # Terminalize active run
    repo.update_run("run_0", {"status": RunStatus.COMPLETED})
    # Add one more to trigger eviction
    run_4 = RunDetail(id="run_4", trace_id="trc_4", session_id="ses", robot_id="rob", user_id="user", source=RunSource.CHAT, status=RunStatus.COMPLETED, input_text="test", created_at=datetime.now(UTC).isoformat())
    repo.create_run(run_4)

    # run_0 is the oldest terminal run, so it must be evicted now
    assert repo.get_run("run_0") is None
    assert repo.get_run("run_2") is not None
    assert repo.get_run("run_3") is not None
    assert repo.get_run("run_4") is not None


def test_eviction_is_bounded_even_when_oldest_runs_are_non_terminal():
    repository = InMemoryTraceRepository(max_items=3)

    for i in range(5):
        repository.create_run(
            RunDetail(
                id=f"run_{i}",
                trace_id=f"trc_{i}",
                session_id="ses",
                robot_id="rob",
                user_id="user",
                source=RunSource.CHAT,
                status=RunStatus.RUNNING,
                input_text="test",
                created_at=datetime.now(UTC).isoformat(),
            )
        )

    runs = repository.get_runs(limit=10).items
    assert len(runs) == 3
    assert repository.get_run("run_0") is None
    assert repository.get_run("run_1") is None
    assert repository.get_run("run_2") is not None
    assert repository.get_run("run_4") is not None


def test_eviction_closes_subscribers_for_dropped_runs():
    repository = InMemoryTraceRepository(max_items=1)
    repository.create_run(
        RunDetail(
            id="run_0",
            trace_id="trc_0",
            session_id="ses",
            robot_id="rob",
            user_id="user",
            source=RunSource.CHAT,
            status=RunStatus.RUNNING,
            input_text="test",
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    _, subscription = repository.subscribe("run_0")

    repository.create_run(
        RunDetail(
            id="run_1",
            trace_id="trc_1",
            session_id="ses",
            robot_id="rob",
            user_id="user",
            source=RunSource.CHAT,
            status=RunStatus.RUNNING,
            input_text="test",
            created_at=datetime.now(UTC).isoformat(),
        )
    )

    assert subscription.closed is True
    assert repository.subscriber_count("run_0") == 0
    assert repository.get_run("run_0") is None
    assert repository.get_run("run_1") is not None

def test_missing_run_update(repo):
    # Should not crash
    repo.update_run("non_existent", {"status": RunStatus.COMPLETED})
    assert repo.get_run("non_existent") is None

def test_duplicate_terminal(repo):
    run = RunDetail(
        id="run_1", trace_id="trc_1", session_id="ses", robot_id="rob", user_id="user",
        source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="hello", created_at=datetime.now(UTC).isoformat()
    )
    repo.create_run(run)

    repo.update_run("run_1", {"status": RunStatus.COMPLETED})
    repo.update_run("run_1", {"status": RunStatus.FAILED}) # Should be ignored

    timeline = repo.get_timeline("run_1")
    terminal_events = [e for e in timeline if e.type in ["run.completed", "run.failed"]]
    assert len(terminal_events) == 1
    assert terminal_events[0].type == "run.completed"


@pytest.mark.parametrize(
    ("event_type", "expected_status"),
    [
        ("run.completed", RunStatus.COMPLETED),
        ("run.failed", RunStatus.FAILED),
        ("run.cancelled", RunStatus.CANCELLED),
        ("run.timeout", RunStatus.TIMEOUT),
    ],
)
def test_terminal_event_type_determines_snapshot_status(
    event_type, expected_status
):
    repository = InMemoryTraceRepository()
    repository.create_run(
        RunDetail(
            id="run_terminal",
            trace_id="trc_terminal",
            session_id="ses",
            robot_id="rob",
            user_id="user",
            source=RunSource.CHAT,
            status=RunStatus.RUNNING,
            input_text="test",
            created_at=datetime.now(UTC).isoformat(),
        )
    )

    repository.add_event(
        TraceEvent(
            id="evt_terminal",
            run_id="run_terminal",
            trace_id="trc_terminal",
            sequence=0,
            timestamp=datetime.now(UTC).isoformat(),
            type=event_type,
        )
    )

    run = repository.get_run("run_terminal")
    timeline = repository.get_timeline("run_terminal")
    assert run.status == expected_status
    assert len(timeline) == 1
    assert timeline[0].type == event_type
    assert timeline[0].status == expected_status

def test_terminal_snapshot_first_wins(repo):
    run = RunDetail(
        id="run_1", trace_id="trc_1", session_id="ses", robot_id="rob", user_id="user",
        source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="hello", created_at=datetime.now(UTC).isoformat()
    )
    repo.create_run(run)

    repo.update_run("run_1", {"status": RunStatus.COMPLETED, "duration_ms": 100})
    repo.update_run("run_1", {"duration_ms": 200, "status": RunStatus.FAILED}) # Ignored

    fetched = repo.get_run("run_1")
    assert fetched.status == RunStatus.COMPLETED
    assert fetched.duration_ms == 100

def test_terminal_truncation_rules(repo):
    run = RunDetail(
        id="run_1", trace_id="trc_1", session_id="ses", robot_id="rob", user_id="user",
        source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="hello", created_at=datetime.now(UTC).isoformat()
    )
    repo.create_run(run)

    for i in range(499):
        repo.add_event(TraceEvent(
            id=f"evt_{i}", run_id="run_1", trace_id="trc_1", sequence=0, timestamp=datetime.now(UTC).isoformat(), type="test.event"
        ))

    # 500th event -> truncation warning replaces it
    repo.add_event(TraceEvent(
        id="evt_500", run_id="run_1", trace_id="trc_1", sequence=0, timestamp=datetime.now(UTC).isoformat(), type="test.event"
    ))
    timeline_500 = repo.get_timeline("run_1")
    assert len(timeline_500) == 500
    assert timeline_500[-1].type == "system.warning"

    # Terminalize
    repo.update_run("run_1", {"status": RunStatus.FAILED, "duration_ms": 123, "error": {"msg": "err"}, "outcome": "bad"})

    timeline = repo.get_timeline("run_1")
    assert len(timeline) == 500
    terminal_events = [e for e in timeline if e.type.startswith("run.")]
    assert len(terminal_events) == 1
    t_evt = terminal_events[0]
    assert t_evt.type == "run.failed"
    assert t_evt.status == "FAILED"
    assert t_evt.duration_ms == 123
    assert t_evt.error["msg"] == "err"
    assert t_evt.metadata["outcome"] == "bad"

    # Sequence check
    sequences = [e.sequence for e in timeline]
    assert len(set(sequences)) == 500
    assert sequences == sorted(sequences)

def test_repo_redaction(repo):
    run = RunDetail(
        id="run_1", trace_id="trc_1", session_id="ses", robot_id="rob", user_id="user",
        source=RunSource.CHAT, status=RunStatus.RUNNING, input_text='{"access_token": "secret"}', created_at=datetime.now(UTC).isoformat()
    )
    repo.create_run(run)
    fetched = repo.get_run("run_1")

    repo.update_run("run_1", {"error": {"password": "pwd"}})
    fetched = repo.get_run("run_1")
    assert "pwd" not in str(fetched.error)


def test_db_trace_repository_persists_runs_and_events(trace_database_url):
    database_url = trace_database_url
    repository = DbTraceRepository(database_url=database_url)
    repository.reset_for_test()
    run = RunDetail(
        id="run_db",
        trace_id="trc_db",
        session_id="ses_db",
        robot_id="rob_db",
        user_id="user_db",
        source=RunSource.CHAT,
        status=RunStatus.RUNNING,
        input_text='{"access_token": "secret"}',
        created_at=datetime.now(UTC).isoformat(),
    )

    repository.create_run(run)
    repository.add_event(
        TraceEvent(
            id="evt_db",
            run_id="run_db",
            trace_id="trc_db",
            sequence=0,
            timestamp=datetime.now(UTC).isoformat(),
            type="tool.completed",
            result_summary={"password": "secret"},
        )
    )
    repository.update_run(
        "run_db",
        {"status": RunStatus.COMPLETED, "duration_ms": 12, "outcome": "done"},
    )

    restarted = DbTraceRepository(database_url=database_url)
    fetched = restarted.get_run("run_db")
    timeline = restarted.get_timeline("run_db")

    assert fetched is not None
    assert fetched.status == RunStatus.COMPLETED
    assert fetched.duration_ms == 12
    assert "secret" not in fetched.input_text
    assert [event.sequence for event in timeline] == [1, 2]
    assert timeline[0].type == "tool.completed"
    assert timeline[1].type == "run.completed"
    assert "secret" not in str(timeline[0].result_summary)

    backlog, subscription = restarted.subscribe("run_db", after_sequence=0)
    assert [event.type for event in backlog] == ["tool.completed", "run.completed"]
    restarted.unsubscribe(subscription)


