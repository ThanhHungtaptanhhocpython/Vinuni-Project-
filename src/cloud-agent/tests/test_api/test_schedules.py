import pytest
from services.scheduler import set_execute_override
from stores import reset_console_stores
from stores.robots import robot_store
from stores.schedules import AUTO_DISABLE_THRESHOLD, schedule_store

_SCHEDULE_BODY = {
    "name": "Ca đêm",
    "robot_id": "robot-001",
    "task_prompt": "Kiểm tra khay",
    "cron_expression": "0 22 * * 1-5",
    "timezone": "Asia/Ho_Chi_Minh",
}


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_console_stores()
    robot_store.create(
        robot_id="robot-001",
        name="Cánh tay test",
        model="so101",
        connection_status="ONLINE",
    )
    set_execute_override(None)
    yield
    set_execute_override(None)
    reset_console_stores()


async def _create_schedule(client, **overrides):
    body = {**_SCHEDULE_BODY, **overrides}
    resp = await client.post("/api/v1/schedules", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_list_starts_empty(client):
    resp = await client.get("/api/v1/schedules")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_validate_cron_ok_and_too_frequent(client):
    ok = await client.post(
        "/api/v1/schedules/validate-cron",
        json={"expression": "0 8 * * 1-5", "timezone": "Asia/Ho_Chi_Minh"},
    )
    assert ok.status_code == 200
    body = ok.json()["data"]
    assert body["valid"] is True
    assert body["cron_description"]
    assert len(body["next_5_runs_local"]) == 5

    bad = await client.post(
        "/api/v1/schedules/validate-cron",
        json={"expression": "*/1 * * * *", "timezone": "Asia/Ho_Chi_Minh"},
    )
    assert bad.status_code == 200
    assert bad.json()["data"]["valid"] is False
    assert bad.json()["data"]["code"] == "SCHED_003"


@pytest.mark.asyncio
async def test_create_rejects_missing_robot(client):
    resp = await client.post(
        "/api/v1/schedules",
        json={**_SCHEDULE_BODY, "robot_id": "robot-missing"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SCHED_004"


@pytest.mark.asyncio
async def test_create_and_toggle_resets_failures(client):
    created = await _create_schedule(client)
    schedule_id = created["id"]
    assert created["next_run_at"]

    off = await client.patch(f"/api/v1/schedules/{schedule_id}", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["data"]["enabled"] is False

    schedule_store.record_outcome(schedule_id, status="FAILED", advance_cron=False)
    schedule_store.record_outcome(schedule_id, status="FAILED", advance_cron=False)

    on = await client.patch(f"/api/v1/schedules/{schedule_id}", json={"enabled": True})
    assert on.json()["data"]["enabled"] is True
    assert on.json()["data"]["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_auto_disable_after_five_failures():
    created = schedule_store.create(
        {
            "name": "Failing job",
            "description": "",
            "robot_id": "robot-001",
            "task_prompt": "fail",
            "cron_expression": "0 8 * * *",
            "cron_description": "Mỗi ngày lúc 08:00",
            "timezone": "Asia/Ho_Chi_Minh",
            "overlap_policy": "SKIP",
            "max_duration_s": 120,
            "next_run_at": "2099-01-01T00:00:00Z",
        }
    )
    schedule_id = created["id"]
    for _ in range(AUTO_DISABLE_THRESHOLD):
        schedule_store.record_outcome(schedule_id, status="FAILED", advance_cron=True)
    row = schedule_store.get(schedule_id)
    assert row["enabled"] is False
    assert row["consecutive_failures"] == AUTO_DISABLE_THRESHOLD
    assert row["next_run_at"] is None


@pytest.mark.asyncio
async def test_run_now_refuses_disabled(client):
    created = await _create_schedule(client)
    await client.patch(f"/api/v1/schedules/{created['id']}", json={"enabled": False})
    resp = await client.post(f"/api/v1/schedules/{created['id']}/run-now")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SCHED_020"


@pytest.mark.asyncio
async def test_run_now_refuses_busy_robot(client):
    created = await _create_schedule(client)
    robot_store.set_status("robot-001", "BUSY")

    async def boom(*_args, **_kwargs):
        raise AssertionError("should not execute")

    set_execute_override(boom)
    resp = await client.post(f"/api/v1/schedules/{created['id']}/run-now")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SCHED_020"


@pytest.mark.asyncio
async def test_run_now_success_records_run(client):
    created = await _create_schedule(client)

    async def ok(schedule, session):
        return {"status": "COMPLETED", "response": "xong"}

    set_execute_override(ok)
    resp = await client.post(f"/api/v1/schedules/{created['id']}/run-now")
    assert resp.status_code == 200
    run_id = resp.json()["data"]["run_id"]
    assert run_id and run_id.startswith("run_")
    runs = await client.get(f"/api/v1/schedules/{created['id']}/runs")
    assert runs.status_code == 200
    assert runs.json()["data"][0]["status"] == "COMPLETED"
    assert runs.json()["data"][0]["run_id"] == run_id

    # Same id must resolve on Nhật ký detail + timeline (no dual-id 404).
    detail = await client.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == run_id
    timeline = await client.get(f"/api/v1/runs/{run_id}/timeline")
    assert timeline.status_code == 200
    assert timeline.json() == []


@pytest.mark.asyncio
async def test_get_schedule(client):
    created = await _create_schedule(client)
    resp = await client.get(f"/api/v1/schedules/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == created["id"]
    assert resp.json()["data"]["name"] == "Ca đêm"

    missing = await client.get("/api/v1/schedules/sch_missing")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "SCHED_010"


@pytest.mark.asyncio
async def test_list_schedules_enabled_filter(client):
    enabled = await _create_schedule(client, name="On")
    disabled = await _create_schedule(client, name="Off")
    await client.patch(f"/api/v1/schedules/{disabled['id']}", json={"enabled": False})

    only_on = await client.get("/api/v1/schedules", params={"enabled": True})
    assert only_on.status_code == 200
    ids = {row["id"] for row in only_on.json()["data"]}
    assert enabled["id"] in ids
    assert disabled["id"] not in ids


@pytest.mark.asyncio
async def test_create_rejects_blank_name(client):
    resp = await client.post(
        "/api/v1/schedules",
        json={**_SCHEDULE_BODY, "name": "  "},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "SCHED_001"


@pytest.mark.asyncio
async def test_patch_schedule_updates_cron(client):
    created = await _create_schedule(client)
    resp = await client.patch(
        f"/api/v1/schedules/{created['id']}",
        json={"cron_expression": "0 9 * * 1-5", "name": "Ca sáng"},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["name"] == "Ca sáng"
    assert body["cron_expression"] == "0 9 * * 1-5"
    assert body["next_run_at"]


@pytest.mark.asyncio
async def test_delete_schedule(client):
    created = await _create_schedule(client)
    resp = await client.delete(f"/api/v1/schedules/{created['id']}")
    assert resp.status_code == 204
    missing = await client.get(f"/api/v1/schedules/{created['id']}")
    assert missing.status_code == 404


def test_schedules_persist_across_store_instances(test_database_url):
    from stores.schedules import ScheduleStore

    first = ScheduleStore(database_url=test_database_url)
    created = first.create(
        {
            "name": "Ca đêm",
            "description": "",
            "robot_id": "robot-001",
            "task_prompt": "Kiểm tra khay",
            "cron_expression": "0 22 * * 1-5",
            "cron_description": "Tối 22:00 ngày làm việc",
            "timezone": "Asia/Ho_Chi_Minh",
            "overlap_policy": "SKIP",
            "max_duration_s": 180,
            "next_run_at": "2099-01-01T00:00:00Z",
        }
    )
    second = ScheduleStore(database_url=test_database_url)
    loaded = second.get(created["id"])
    assert loaded is not None
    assert loaded["name"] == "Ca đêm"
    assert loaded["enabled"] is True
