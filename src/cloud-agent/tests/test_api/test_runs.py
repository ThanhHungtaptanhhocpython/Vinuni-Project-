import pytest
from stores import reset_console_stores
from stores.robots import robot_store
from stores.runs import run_store


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_console_stores()
    robot_store.create(
        robot_id="robot-001",
        name="Cánh tay test",
        model="so101",
    )
    yield
    reset_console_stores()


@pytest.mark.asyncio
async def test_runs_list_starts_empty(client):
    resp = await client.get("/api/v1/runs")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_runs_rejects_invalid_status_and_source(client):
    bad_status = await client.get("/api/v1/runs", params={"status": "NOT_A_STATUS"})
    assert bad_status.status_code == 422
    assert bad_status.json()["detail"]["code"] == "VAL_005"

    bad_source = await client.get("/api/v1/runs", params={"source": "NOT_A_SOURCE"})
    assert bad_source.status_code == 422
    assert bad_source.json()["detail"]["code"] == "VAL_005"


@pytest.mark.asyncio
async def test_runs_list_and_get(client):
    started = run_store.start(
        run_id="run_test_chat",
        user_id="user_dev",
        session_id="ses_test",
        robot_id="robot-001",
        source="CHAT",
        input_text="hello robot",
        trace_id="trc_test",
    )
    assert started["status"] == "RUNNING"
    run_store.finish(
        "run_test_chat",
        status="FAILED",
        duration_s=2,
        error={"code": "MCP_001", "message": "Robot đang offline hoặc không thể kết nối."},
    )

    listed = await client.get("/api/v1/runs")
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == "run_test_chat"
    assert rows[0]["source"] == "CHAT"
    assert rows[0]["error"]["code"] == "MCP_001"

    detail = await client.get("/api/v1/runs/run_test_chat")
    assert detail.status_code == 200
    assert detail.json()["data"]["input_text"] == "hello robot"

    missing = await client.get("/api/v1/runs/run_missing")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_enabled_schedule_appears_on_runs_list(client):
    created = await client.post(
        "/api/v1/schedules",
        json={
            "name": "Test",
            "robot_id": "robot-001",
            "task_prompt": "Kiểm tra khay",
            "cron_expression": "0 8 * * 1-5",
            "timezone": "Asia/Ho_Chi_Minh",
        },
    )
    assert created.status_code == 201
    schedule_id = created.json()["data"]["id"]

    listed = await client.get("/api/v1/runs")
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert any(row["schedule_id"] == schedule_id and row["status"] == "SCHEDULED" for row in rows)


@pytest.mark.asyncio
async def test_chat_session_aggregates_are_hidden_from_runs_list(client):
    """Nhật ký lists agent turns, not `session:*` conversation summaries."""
    from stores.sessions import session_store

    session = session_store.create("robot-001", user_id="user_dev", title="Ca sáng")
    session_store.append_message(session["id"], role="user", content="hello robot")
    session_store.append_message(
        session["id"], role="assistant", content="Robot đang offline hoặc không thể kết nối."
    )
    run_store.start(
        run_id="run_chat_session",
        user_id="user_dev",
        session_id=session["id"],
        robot_id="robot-001",
        source="CHAT",
        input_text="hello robot",
        trace_id="trc_chat_session",
    )
    run_store.finish("run_chat_session", status="COMPLETED", duration_s=1)

    listed = await client.get("/api/v1/runs", params={"source": "CHAT"})
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert not any(str(row.get("id", "")).startswith("session:") for row in rows)
    # Covered chat turns are folded into the (API-hidden) session aggregate.
    assert not any(row.get("id") == "run_chat_session" for row in rows)
