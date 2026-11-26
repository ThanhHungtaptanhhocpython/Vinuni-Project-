import pytest
from agents.tools.mcp_client import ToolResult
from api import robot_stream_routes
from stores import reset_console_stores
from stores.edge_registry import edge_registry_store
from stores.robots import robot_store
from stores.runs import run_store


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_console_stores()
    yield
    reset_console_stores()


def _seed_edge_registry(robot_id: str = "robot-001", *, robot_type: str = "so101") -> None:
    edge_registry_store.register_if_new(robot_id=robot_id, robot_type=robot_type, capabilities=[])


def _seed_robot(robot_id: str = "robot-001") -> None:
    _seed_edge_registry(robot_id)
    robot_store.create(
        robot_id=robot_id,
        name="Cánh tay test",
        model="so101",
    )


@pytest.mark.asyncio
async def test_list_robots(client):
    resp = await client.get("/api/v1/robots")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert isinstance(rows, list)
    assert rows == []


@pytest.mark.asyncio
async def test_add_robot_api_disabled(client):
    _seed_edge_registry("robot-new-01", robot_type="so101")
    resp = await client.post(
        "/api/v1/robots",
        json={"id": "robot-new-01", "name": "Cánh tay Delta"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "ROBOT_004"


@pytest.mark.asyncio
async def test_list_robots_idle_when_only_active_run(client):
    from stores.sessions import session_store

    _seed_robot()
    session = session_store.create("robot-001", user_id="user_dev")
    session_store.set_active_run(session["id"], "run_busy_overlay")

    resp = await client.get("/api/v1/robots")
    assert resp.status_code == 200
    robot = next(row for row in resp.json()["data"] if row["id"] == "robot-001")
    assert robot["robot_status"] == "IDLE"


@pytest.mark.asyncio
async def test_list_robots_preserves_registry_busy(client):
    from stores.robots import robot_store

    _seed_robot()
    robot_store.set_status("robot-001", "BUSY")
    resp = await client.get("/api/v1/robots")
    assert resp.status_code == 200
    robot = next(row for row in resp.json()["data"] if row["id"] == "robot-001")
    assert robot["robot_status"] == "BUSY"

    detail = await client.get("/api/v1/robots/robot-001")
    assert detail.status_code == 200
    assert detail.json()["data"]["robot_status"] == "BUSY"


@pytest.mark.asyncio
async def test_get_robot_ok(client):
    _seed_robot()
    resp = await client.get("/api/v1/robots/robot-001")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["id"] == "robot-001"
    assert body["name"]
    assert "capabilities" in body
    assert "joints" in body


@pytest.mark.asyncio
async def test_get_robot_missing(client):
    resp = await client.get("/api/v1/robots/robot-missing")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SCHED_004"


@pytest.mark.asyncio
async def test_robot_stats_404_when_no_runs_yet(client):
    _seed_robot()
    resp = await client.get("/api/v1/robots/robot-001/stats")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "AGENT_013"


@pytest.mark.asyncio
async def test_robot_stats_missing_robot(client):
    resp = await client.get("/api/v1/robots/robot-missing/stats")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SCHED_004"


@pytest.mark.asyncio
async def test_robot_stats_aggregates_runs(client):
    _seed_robot()
    run_store.start(
        run_id="run_stats_ok",
        user_id="user_dev",
        session_id="ses_stats",
        robot_id="robot-001",
        source="CHAT",
        input_text="hi",
        trace_id="trc_stats_ok",
    )
    run_store.finish("run_stats_ok", status="COMPLETED", duration_s=10)

    run_store.start(
        run_id="run_stats_fail",
        user_id="user_dev",
        session_id="ses_stats",
        robot_id="robot-001",
        source="CHAT",
        input_text="hi again",
        trace_id="trc_stats_fail",
    )
    run_store.finish(
        "run_stats_fail",
        status="FAILED",
        duration_s=4,
        error={"code": "MCP_001", "message": "lỗi"},
    )

    resp = await client.get("/api/v1/robots/robot-001/stats")
    assert resp.status_code == 200
    stats = resp.json()["data"]
    assert stats["total_runs"] == 2
    assert stats["finished_runs"] == 2
    assert stats["success_rate"] == pytest.approx(0.5)
    assert stats["avg_duration_s"] == pytest.approx(7.0)
    outcomes = {o["status"]: o["count"] for o in stats["outcomes"]}
    assert outcomes == {"COMPLETED": 1, "FAILED": 1}
    assert len(stats["daily_activity"]) == stats["window_days"]
    assert sum(d["count"] for d in stats["daily_activity"]) == 2


@pytest.mark.asyncio
async def test_robot_environment_reports_sim_reset_available(client, monkeypatch):
    edge_registry_store.register_if_new(
        robot_id="robot-sim",
        robot_type="franka",
        capabilities=[{"name": "reset_episode", "version": "1.0"}],
        base_url="http://edge.local:8931",
    )
    robot_store.create(
        robot_id="robot-sim",
        name="Sim",
        model="franka",
    )

    async def fake_call_edge_tool(**kwargs):
        assert kwargs["tool_name"] == "get_robot_state"
        return ToolResult(
            ok=True,
            tool_name="get_robot_state",
            data={"backend_mode": "sim"},
        )

    monkeypatch.setattr(robot_stream_routes, "_call_edge_tool", fake_call_edge_tool)

    resp = await client.get("/api/v1/robots/robot-sim/environment")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend_mode"] == "sim"
    assert body["virtual"] is True
    assert body["reset_available"] is True


@pytest.mark.asyncio
async def test_reset_robot_environment_rejects_real_backend(client, monkeypatch):
    edge_registry_store.register_if_new(
        robot_id="robot-real",
        robot_type="so101",
        capabilities=[{"name": "reset_episode", "version": "1.0"}],
        base_url="http://edge.local:8931",
    )
    robot_store.create(
        robot_id="robot-real",
        name="Real",
        model="so101",
    )

    async def fake_call_edge_tool(**kwargs):
        assert kwargs["tool_name"] == "get_robot_state"
        return ToolResult(
            ok=True,
            tool_name="get_robot_state",
            data={"backend_mode": "real"},
        )

    monkeypatch.setattr(robot_stream_routes, "_call_edge_tool", fake_call_edge_tool)

    resp = await client.post("/api/v1/robots/robot-real/environment/reset")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ROBOT_043"


@pytest.mark.asyncio
async def test_reset_robot_environment_accepts_libero_sim_backend(client, monkeypatch):
    edge_registry_store.register_if_new(
        robot_id="robot-libero",
        robot_type="franka",
        capabilities=[],
        base_url="http://edge.local:8931",
    )
    robot_store.create(
        robot_id="robot-libero",
        name="LIBERO",
        model="franka",
    )
    calls: list[str] = []

    async def fake_call_edge_tool(**kwargs):
        calls.append(kwargs["tool_name"])
        if kwargs["tool_name"] == "get_robot_state":
            return ToolResult(
                ok=True,
                tool_name="get_robot_state",
                data={"robot_state": {"backend_mode": "libero_sim_eval"}},
            )
        return ToolResult(
            ok=True,
            tool_name="reset_episode",
            data={"status": "DONE", "backend_mode": "libero_sim_eval"},
        )

    monkeypatch.setattr(robot_stream_routes, "_call_edge_tool", fake_call_edge_tool)

    resp = await client.post("/api/v1/robots/robot-libero/environment/reset")

    assert resp.status_code == 200
    assert resp.json()["data"]["backend_mode"] == "libero_sim_eval"
    assert calls == ["get_robot_state", "reset_episode"]
