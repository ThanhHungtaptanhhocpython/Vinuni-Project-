import inspect
import json
import queue
from datetime import UTC, datetime

import pytest
from agents.trace_repo import InMemoryTraceRepository, trace_repository
from api import runs_routes
from auth.deps import require_user_if_configured
from auth.store import User
from fastapi.testclient import TestClient
from main import app
from models.schemas import RunDetail, RunSource, RunStatus, TraceEvent


def _user(user_id: str) -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        name=user_id,
        google_sub=f"sub-{user_id}",
    )


def _authenticate_as(user_id: str) -> None:
    app.dependency_overrides[require_user_if_configured] = lambda: _user(user_id)


def _run(run_id: str = "run_stream", *, user_id: str = "user_1") -> RunDetail:
    return RunDetail(
        id=run_id,
        trace_id=f"trc_{run_id}",
        session_id="session_1",
        robot_id="robot_1",
        user_id=user_id,
        source=RunSource.CHAT,
        status=RunStatus.RUNNING,
        input_text="SECRET_INPUT_PROMPT",
        created_at=datetime.now(UTC).isoformat(),
    )


def _event(run_id: str, event_type: str, **kwargs) -> TraceEvent:
    return TraceEvent(
        id=f"evt_{event_type}_{datetime.now(UTC).timestamp()}",
        run_id=run_id,
        trace_id=f"trc_{run_id}",
        sequence=0,
        timestamp=datetime.now(UTC).isoformat(),
        type=event_type,
        **kwargs,
    )


def _stream_events(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        parsed = {"id": None, "event": None, "data": None}
        for line in block.splitlines():
            if line.startswith("id: "):
                parsed["id"] = int(line.removeprefix("id: ").strip())
            elif line.startswith("event: "):
                parsed["event"] = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                parsed["data"] = json.loads(line.removeprefix("data: ").strip())
        events.append(parsed)
    return events


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def isolated_trace_state():
    trace_repository.reset_for_test()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    trace_repository.reset_for_test()


def test_stream_missing_and_cross_user_resources_return_404(client):
    _authenticate_as("user_1")
    assert client.get("/api/v1/runs/missing/stream").status_code == 404

    trace_repository.create_run(_run("run_private", user_id="user_1"))
    _authenticate_as("user_2")
    assert client.get("/api/v1/runs/run_private/stream").status_code == 404


def test_stream_backlog_order_and_terminal_close(client):
    _authenticate_as("user_1")
    trace_repository.create_run(_run())
    trace_repository.add_event(_event("run_stream", "llm.started", model="gpt-test"))
    trace_repository.add_event(
        _event("run_stream", "tool.failed", tool_name="take_action", error={"code": "MCP_001", "message": "Robot offline"})
    )
    trace_repository.update_run(
        "run_stream",
        {"status": RunStatus.FAILED, "duration_ms": 420, "error": {"code": "MCP_001", "message": "Robot offline"}},
    )

    response = client.get("/api/v1/runs/run_stream/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _stream_events(response.text)
    assert [event["id"] for event in events] == [1, 2, 3]
    assert [event["data"]["sequence"] for event in events] == [1, 2, 3]
    assert events[1]["data"]["stage"] == "tool_execution"
    assert events[1]["data"]["status"] == "failed"
    assert events[-1]["data"]["stage"] == "finished"
    assert events[-1]["data"]["status"] == "failed"


def test_stream_after_sequence_and_last_event_id_resume(client):
    _authenticate_as("user_1")
    trace_repository.create_run(_run())
    trace_repository.add_event(_event("run_stream", "llm.started"))
    trace_repository.add_event(_event("run_stream", "llm.completed"))
    trace_repository.update_run("run_stream", {"status": RunStatus.COMPLETED, "duration_ms": 100})

    query_response = client.get("/api/v1/runs/run_stream/stream?after_sequence=1")
    header_response = client.get(
        "/api/v1/runs/run_stream/stream?after_sequence=0",
        headers={"Last-Event-ID": "2"},
    )

    assert [event["id"] for event in _stream_events(query_response.text)] == [2, 3]
    assert [event["id"] for event in _stream_events(header_response.text)] == [3]


@pytest.mark.parametrize("headers", [{"Last-Event-ID": "abc"}, {"Last-Event-ID": "-1"}])
def test_stream_rejects_invalid_last_event_id(client, headers):
    _authenticate_as("user_1")
    trace_repository.create_run(_run())

    response = client.get("/api/v1/runs/run_stream/stream", headers=headers)

    assert response.status_code == 422


def test_stream_projection_does_not_leak_sensitive_payloads(client):
    _authenticate_as("user_1")
    trace_repository.create_run(_run())
    trace_repository.add_event(
        _event(
            "run_stream",
            "unknown.raw",
            tool_name="safe_tool",
            arguments={"Authorization": "SECRET_ARGUMENT_TOKEN"},
            result_summary={"content": "SECRET_RESULT_CONTENT"},
            error={"message": "safe error", "token": "SECRET_ERROR_TOKEN"},
            metadata={
                "Prompt": "SECRET_PROMPT",
                "messages": [{"content": "SECRET_MESSAGE_CONTENT"}],
                "reasoning": "SECRET_REASONING",
                "chain_of_thought": "SECRET_COT",
            },
        )
    )
    trace_repository.update_run("run_stream", {"status": RunStatus.COMPLETED})

    response = client.get("/api/v1/runs/run_stream/stream")

    serialized = response.text
    for forbidden in [
        "SECRET_INPUT_PROMPT",
        "SECRET_ARGUMENT_TOKEN",
        "SECRET_RESULT_CONTENT",
        "SECRET_ERROR_TOKEN",
        "SECRET_PROMPT",
        "SECRET_MESSAGE_CONTENT",
        "SECRET_REASONING",
        "SECRET_COT",
        "authorization",
        "chain_of_thought",
    ]:
        assert forbidden.lower() not in serialized.lower()
    events = _stream_events(serialized)
    assert events[0]["data"]["stage"] == "unknown"
    assert "metadata" not in events[0]["data"]
    assert "arguments" not in events[0]["data"]
    assert "result_summary" not in events[0]["data"]


def test_trace_repository_subscribe_receives_live_events_without_loss():
    repo = InMemoryTraceRepository()
    repo.create_run(_run())
    repo.add_event(_event("run_stream", "llm.started"))

    backlog, subscription = repo.subscribe("run_stream")
    repo.add_event(_event("run_stream", "llm.completed"))

    live_event = subscription.queue.get(timeout=1)
    repo.unsubscribe(subscription)

    assert [event.sequence for event in backlog] == [1]
    assert live_event.sequence == 2
    assert repo.subscriber_count("run_stream") == 0


def test_trace_repository_bounded_queue_does_not_block_add_event():
    repo = InMemoryTraceRepository()
    repo.create_run(_run())
    _, subscription = repo.subscribe("run_stream", max_queue_size=1)

    repo.add_event(_event("run_stream", "llm.started"))
    repo.add_event(_event("run_stream", "llm.completed"))

    first = subscription.queue.get(timeout=1)
    assert first.sequence == 1
    assert subscription.closed is True
    with pytest.raises(queue.Empty):
        subscription.queue.get_nowait()
    assert repo.subscriber_count("run_stream") == 0


def test_stream_generator_does_not_use_threadpool_queue_wait():
    source = inspect.getsource(runs_routes.stream_run_thinking)

    assert "asyncio.to_thread" not in source
    assert "get_nowait" in source
