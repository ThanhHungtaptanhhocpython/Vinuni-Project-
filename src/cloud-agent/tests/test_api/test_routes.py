from unittest.mock import AsyncMock, patch

import pytest
from agents.trace_repo import trace_repository
from langchain_core.messages import AIMessage
from models.schemas import RunStatus
from stores import reset_console_stores
from stores.robots import robot_store
from stores.runs import run_store
from stores.sessions import session_store
from stores.task_memory import task_memory_store


@pytest.fixture(autouse=True)
def _reset_state():
    reset_console_stores()
    robot_store.create(
        robot_id="robot-001",
        name="Cánh tay test",
        model="so101",
    )
    trace_repository.reset_for_test()
    yield
    reset_console_stores()
    trace_repository.reset_for_test()


def _completed_agent_result(text: str = "Đã xong."):
    return {
        "messages": [AIMessage(content=text)],
        "final_status": "COMPLETED",
    }


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["env"] == "test"


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["agent"]
    assert "llm_provider" in body
    assert "model" in body
    assert body["app_env"] == "test"
    assert body["auth_required"] is False


@pytest.mark.asyncio
async def test_chat_empty_message(client):
    response = await client.post("/api/v1/chat", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_missing_message(client):
    response = await client.post("/api/v1/chat", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_success(client):
    with patch(
        "agents.runner.agent.ainvoke",
        new=AsyncMock(return_value=_completed_agent_result("Xin chào")),
    ):
        response = await client.post(
            "/api/v1/chat",
            json={"message": "hello", "robot_id": "robot-001"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == RunStatus.COMPLETED
    assert payload["response"] == "Xin chào"
    assert payload["run_id"]
    assert payload["trace_id"]
    assert payload["duration_ms"] is not None and payload["duration_ms"] >= 0
    assert payload["error"] is None

    run = run_store.get(payload["run_id"], user_id="user_dev")
    assert run is not None
    assert run["status"] == "COMPLETED"

    snapshot = trace_repository.get_run(payload["run_id"])
    assert snapshot is not None
    assert snapshot.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_chat_reuses_session(client):
    session = session_store.create("robot-001", user_id="user_dev", title="Ca sáng")
    with patch(
        "agents.runner.agent.ainvoke",
        new=AsyncMock(return_value=_completed_agent_result()),
    ):
        response = await client.post(
            "/api/v1/chat",
            json={
                "message": "tiếp tục",
                "robot_id": "robot-001",
                "session_id": session["id"],
            },
        )

    assert response.status_code == 200
    messages = session_store.list_messages(session["id"], user_id="user_dev")
    assert messages is not None
    assert any(m["role"] == "user" and m["content"] == "tiếp tục" for m in messages)
    assert any(m["role"] == "assistant" for m in messages)


@pytest.mark.asyncio
async def test_chat_passes_session_history_to_agent(client):
    session = session_store.create("robot-001", user_id="user_dev", title="Morning")
    # Vietnamese replies avoid runner rewrite_mixed_language → real LLM.
    invoke = AsyncMock(
        side_effect=[
            _completed_agent_result("Đã ghi nhớ nhiệm vụ."),
            _completed_agent_result("Tiếp tục nhiệm vụ trước."),
        ]
    )

    with patch("agents.runner.agent.ainvoke", new=invoke):
        first = await client.post(
            "/api/v1/chat",
            json={
                "message": "Nhặt bát đen",
                "robot_id": "robot-001",
                "session_id": session["id"],
            },
        )
        second = await client.post(
            "/api/v1/chat",
            json={
                "message": "Tiếp tục nhiệm vụ đó",
                "robot_id": "robot-001",
                "session_id": session["id"],
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    second_state = invoke.await_args_list[1].args[0]
    contents = [message.content for message in second_state["messages"]]
    assert contents == [
        "Nhặt bát đen",
        "Đã ghi nhớ nhiệm vụ.",
        "Tiếp tục nhiệm vụ đó",
    ]
    memory = task_memory_store.get(session["id"])
    assert memory["completed_tasks"][-1]["task"] == "Tiếp tục nhiệm vụ đó"


@pytest.mark.asyncio
async def test_chat_retrieves_and_records_long_term_memory(client, monkeypatch):
    retrieved = [
        {
            "memory_type": "preference",
            "content": "User prefers concise answers.",
        }
    ]
    record_calls = []

    async def fake_retrieve(*, user_id, query):
        assert user_id == "user_dev"
        assert "Continue" in query
        return retrieved

    async def fake_record(**kwargs):
        record_calls.append(kwargs)

    monkeypatch.setattr(
        "api.routes.long_term_memory_service.retrieve_memories",
        fake_retrieve,
    )
    monkeypatch.setattr(
        "api.routes.long_term_memory_service.record_run_memory",
        fake_record,
    )
    invoke = AsyncMock(return_value=_completed_agent_result("Continuing."))

    with patch("agents.runner.agent.ainvoke", new=invoke):
        response = await client.post(
            "/api/v1/chat",
            json={"message": "Continue with my usual style", "robot_id": "robot-001"},
        )

    assert response.status_code == 200
    state = invoke.await_args.args[0]
    assert state["long_term_memories"] == retrieved
    assert record_calls
    assert record_calls[0]["assistant_reply"] == "Continuing."
    assert record_calls[0]["task_memory"]["completed_tasks"][-1]["task"] == "Continue with my usual style"


@pytest.mark.asyncio
async def test_chat_start_returns_immediately(client):
    with patch(
        "agents.runner.agent.ainvoke",
        new=AsyncMock(return_value=_completed_agent_result("background done")),
    ):
        response = await client.post(
            "/api/v1/chat/start",
            json={"message": "start async", "robot_id": "robot-001"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"]
    assert payload["trace_id"]
    assert payload["session_id"]
    assert payload["status"] == RunStatus.RUNNING

    # Immediate side-effects before / while background work
    assert run_store.get(payload["run_id"], user_id="user_dev") is not None
    assert session_store.get(payload["session_id"], user_id="user_dev") is not None
    snapshot = trace_repository.get_run(payload["run_id"])
    assert snapshot is not None
    assert snapshot.input_text == "start async"
    messages = session_store.list_messages(payload["session_id"], user_id="user_dev")
    assert messages is not None
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "start async"


@pytest.mark.asyncio
async def test_chat_start_empty_message(client):
    response = await client.post("/api/v1/chat/start", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_start_rejects_when_robot_busy(client):
    from stores.robots import robot_store

    robot_store.set_status("robot-001", "BUSY")

    response = await client.post(
        "/api/v1/chat/start",
        json={"message": "another command", "robot_id": "robot-001"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ROBOT_010"


@pytest.mark.asyncio
async def test_chat_start_with_existing_session(client):
    session = session_store.create("robot-001", user_id="user_dev")
    with patch(
        "agents.runner.agent.ainvoke",
        new=AsyncMock(return_value=_completed_agent_result()),
    ):
        response = await client.post(
            "/api/v1/chat/start",
            json={
                "message": "reuse session",
                "robot_id": "robot-001",
                "session_id": session["id"],
            },
        )

    assert response.status_code == 200
    assert response.json()["session_id"] == session["id"]
