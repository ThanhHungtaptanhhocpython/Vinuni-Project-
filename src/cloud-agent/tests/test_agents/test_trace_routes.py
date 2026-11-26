import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from agents.tools.mcp_client import ToolError, ToolResult
from agents.trace_repo import trace_repository
from auth.deps import require_user_if_configured
from auth.store import User
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from main import app
from models.schemas import RunDetail, RunSource, RunStatus

TERMINAL_EVENT_TYPES = {
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.timeout",
}


def _user(user_id: str) -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        name=user_id,
        google_sub=f"sub-{user_id}",
    )


def _run(
    run_id: str,
    *,
    user_id: str = "user_1",
    status: RunStatus = RunStatus.COMPLETED,
    source: RunSource = RunSource.CHAT,
    session_id: str = "session_1",
    robot_id: str = "robot_1",
) -> RunDetail:
    return RunDetail(
        id=run_id,
        trace_id=f"trace_{run_id}",
        session_id=session_id,
        robot_id=robot_id,
        user_id=user_id,
        source=source,
        status=status,
        input_text="hello",
        created_at=datetime.now(UTC).isoformat(),
    )


def _terminal_events(run_id: str):
    return [
        event
        for event in trace_repository.get_timeline(run_id)
        if event.type in TERMINAL_EVENT_TYPES
    ]


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def isolated_trace_state():
    from datetime import UTC, datetime

    from stores import reset_console_stores
    from stores.db import get_connection
    from stores.robots import robot_store

    reset_console_stores()
    robot_store.create(robot_id="robot_1", name="Trace robot", model="so101")
    # Chat creates sessions owned by the authenticated user — seed FK parents.
    conn, _ = get_connection()
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    for user_id in ("user_1", "user_2"):
        conn.execute(
            """
            INSERT INTO users (id, email, google_sub, name, picture_url, timezone, locale, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            (user_id, f"{user_id}@example.com", f"sub-{user_id}", user_id, "", "UTC", "en", now),
        )
    conn.commit()
    trace_repository.reset_for_test()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    trace_repository.reset_for_test()
    reset_console_stores()


def _chat_body(message: str = "hello", *, robot_id: str = "robot_1") -> dict:
    return {"message": message, "robot_id": robot_id}


def _authenticate_as(user_id: str) -> None:
    app.dependency_overrides[require_user_if_configured] = lambda: _user(user_id)


def test_list_runs_is_scoped_to_current_user(client):
    trace_repository.create_run(_run("run_user_1", user_id="user_1"))
    trace_repository.create_run(_run("run_user_2", user_id="user_2"))

    _authenticate_as("user_1")
    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 1
    assert [item["id"] for item in body["data"]] == ["run_user_1"]


def test_run_detail_and_timeline_hide_cross_user_resources(client):
    trace_repository.create_run(_run("run_private", user_id="user_1"))
    _authenticate_as("user_2")

    detail_response = client.get("/api/v1/runs/run_private")
    timeline_response = client.get("/api/v1/runs/run_private/timeline")

    assert detail_response.status_code == 404
    assert timeline_response.status_code == 404


@pytest.mark.parametrize(
    "query",
    [
        "limit=101",
        "offset=-1",
        "status=INVALID_STATUS",
        "source=INVALID_SOURCE",
    ],
)
def test_list_runs_rejects_invalid_query_values(client, query):
    _authenticate_as("user_1")
    assert client.get(f"/api/v1/runs?{query}").status_code == 422


def test_list_runs_filters_and_total_before_pagination(client):
    _authenticate_as("user_1")
    runs = [
        _run("match_1"),
        _run("match_2"),
        _run("failed", status=RunStatus.FAILED),
        _run("scheduled", source=RunSource.SCHEDULE),
        _run("other_session", session_id="session_2"),
        _run("other_robot", robot_id="robot_2"),
        _run("other_user", user_id="user_2"),
    ]
    for run in runs:
        trace_repository.create_run(run)

    cases = [
        ("status=FAILED", {"failed"}),
        ("source=SCHEDULE", {"scheduled"}),
        ("session_id=session_2", {"other_session"}),
        ("robot_id=robot_2", {"other_robot"}),
        (
            "status=COMPLETED&source=CHAT&session_id=session_1&robot_id=robot_1",
            {"match_1", "match_2"},
        ),
    ]
    for query, expected_ids in cases:
        response = client.get(f"/api/v1/runs?{query}")
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["total"] == len(expected_ids)
        assert {item["id"] for item in body["data"]} == expected_ids

    page = client.get(
        "/api/v1/runs?status=COMPLETED&source=CHAT&session_id=session_1"
        "&robot_id=robot_1&limit=1&offset=1"
    )
    assert page.status_code == 200
    assert page.json()["meta"]["total"] == 2
    assert len(page.json()["data"]) == 1


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_event"),
    [
        (
            {"messages": [AIMessage(content="done")], "final_status": "COMPLETED"},
            RunStatus.COMPLETED,
            "run.completed",
        ),
        (
            {
                "messages": [],
                "final_status": "FAILED",
                "error": {
                    "code": "AGENT_TEST",
                    "message": "safe failure",
                    "authorization": "SECRET_RESPONSE_TOKEN",
                },
            },
            RunStatus.FAILED,
            "run.failed",
        ),
        (
            {"messages": [], "final_status": "CANCELLED"},
            RunStatus.CANCELLED,
            "run.cancelled",
        ),
    ],
)
def test_chat_response_snapshot_and_terminal_event_are_consistent(
    client,
    result,
    expected_status,
    expected_event,
):
    _authenticate_as("user_1")
    with patch(
        "agents.runner.agent.ainvoke",
        new=AsyncMock(return_value=result),
    ):
        response = client.post("/api/v1/chat", json=_chat_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == expected_status
    assert payload["run_id"]
    assert payload["trace_id"]
    assert payload["duration_ms"] >= 0

    snapshot = trace_repository.get_run(payload["run_id"])
    assert snapshot is not None
    assert snapshot.status == expected_status
    assert snapshot.trace_id == payload["trace_id"]
    assert snapshot.duration_ms is not None and snapshot.duration_ms >= 0

    terminal_events = _terminal_events(payload["run_id"])
    assert len(terminal_events) == 1
    assert terminal_events[0].type == expected_event
    assert terminal_events[0].status == expected_status

    serialized = json.dumps(
        {"response": payload, "snapshot": snapshot.model_dump(), "events": [e.model_dump() for e in terminal_events]},
        default=str,
    )
    assert "SECRET_RESPONSE_TOKEN" not in serialized


def test_chat_exception_returns_failed_sanitized_trace(client):
    _authenticate_as("user_1")
    with patch(
        "agents.runner.agent.ainvoke",
        new=AsyncMock(
            side_effect=Exception(
                '{"authorization":"SECRET_EXCEPTION_TOKEN","message":"boom"}'
            )
        ),
    ):
        response = client.post("/api/v1/chat", json=_chat_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == RunStatus.FAILED
    snapshot = trace_repository.get_run(payload["run_id"])
    assert snapshot is not None and snapshot.status == RunStatus.FAILED
    terminal_events = _terminal_events(payload["run_id"])
    assert len(terminal_events) == 1
    assert terminal_events[0].type == "run.failed"

    serialized = json.dumps(
        {"response": payload, "snapshot": snapshot.model_dump(), "events": [e.model_dump() for e in terminal_events]},
        default=str,
    )
    assert "SECRET_EXCEPTION_TOKEN" not in serialized
    assert len(payload["error"]["message"]) <= 2015
    assert len(serialized) < 10_000


def test_chat_timeout_returns_timeout_trace(client):
    _authenticate_as("user_1")
    with patch(
        "agents.runner.agent.ainvoke",
        new=AsyncMock(side_effect=TimeoutError()),
    ):
        response = client.post("/api/v1/chat", json=_chat_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == RunStatus.TIMEOUT
    assert payload["error"]["code"] == "AGENT_TIMEOUT"
    snapshot = trace_repository.get_run(payload["run_id"])
    assert snapshot is not None and snapshot.status == RunStatus.TIMEOUT
    terminal_events = _terminal_events(payload["run_id"])
    assert len(terminal_events) == 1
    assert terminal_events[0].type == "run.timeout"
    assert terminal_events[0].status == RunStatus.TIMEOUT


@pytest.mark.asyncio
async def test_runner_cancellation_terminalizes_then_propagates():
    from agents.runner import build_default_context, run_agent

    context = build_default_context(user_id="user_1")
    with patch(
        "agents.runner.agent.ainvoke",
        new=AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_agent("hello", ctx=context)

    snapshot = trace_repository.get_run(context.run_id)
    assert snapshot is not None and snapshot.status == RunStatus.CANCELLED
    assert snapshot.completed_at is not None
    assert snapshot.duration_ms is not None and snapshot.duration_ms >= 0
    terminal_events = _terminal_events(context.run_id)
    assert len(terminal_events) == 1
    assert terminal_events[0].type == "run.cancelled"
    assert terminal_events[0].status == RunStatus.CANCELLED


@pytest.fixture
def direct_mcp_mode():
    from config import get_settings

    settings = get_settings()
    original_app_env = settings.app_env
    original_endpoint = settings.mcp_endpoint_url
    settings.app_env = "development"
    settings.mcp_endpoint_url = "http://localhost:8000"
    try:
        yield
    finally:
        settings.app_env = original_app_env
        settings.mcp_endpoint_url = original_endpoint


@pytest.mark.parametrize(
    ("tool_result", "expected_status", "tool_event", "run_event"),
    [
        (
            ToolResult(ok=True, tool_name="take_action", data={"episode_id": "ep_1"}),
            RunStatus.COMPLETED,
            "tool.completed",
            "run.completed",
        ),
        (
            ToolResult(
                ok=False,
                tool_name="take_action",
                error=ToolError(
                    code="MCP_TEST",
                    message="failed",
                    detail={"authorization": "SECRET_MCP_TOKEN"},
                ),
            ),
            RunStatus.FAILED,
            "tool.failed",
            "run.failed",
        ),
    ],
)
def test_direct_mcp_result_creates_complete_trace(
    client,
    direct_mcp_mode,
    tool_result,
    expected_status,
    tool_event,
    run_event,
):
    _authenticate_as("user_1")
    with patch("api.routes.McpClient") as client_class:
        client_class.return_value.call_tool = AsyncMock(return_value=tool_result)
        response = client.post(
            "/api/v1/chat",
            json=_chat_body("run task_id=123"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == expected_status
    snapshot = trace_repository.get_run(payload["run_id"])
    assert snapshot is not None and snapshot.status == expected_status
    timeline = trace_repository.get_timeline(payload["run_id"])
    assert any(event.type == "tool.started" for event in timeline)
    assert any(event.type == tool_event for event in timeline)
    terminal_events = _terminal_events(payload["run_id"])
    assert len(terminal_events) == 1
    assert terminal_events[0].type == run_event
    assert "SECRET_MCP_TOKEN" not in json.dumps(payload, default=str)
    assert "SECRET_MCP_TOKEN" not in json.dumps(
        [event.model_dump() for event in timeline],
        default=str,
    )


def test_direct_mcp_exception_creates_failed_trace(client, direct_mcp_mode):
    _authenticate_as("user_1")
    with patch("api.routes.McpClient") as client_class:
        client_class.return_value.call_tool = AsyncMock(
            side_effect=Exception("transport unavailable")
        )
        response = client.post(
            "/api/v1/chat",
            json=_chat_body("run task_id=123"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == RunStatus.FAILED
    snapshot = trace_repository.get_run(payload["run_id"])
    assert snapshot is not None and snapshot.status == RunStatus.FAILED
    timeline = trace_repository.get_timeline(payload["run_id"])
    assert any(event.type == "tool.failed" for event in timeline)
    terminal_events = _terminal_events(payload["run_id"])
    assert len(terminal_events) == 1
    assert terminal_events[0].type == "run.failed"
