import pytest
from stores import reset_console_stores
from stores.robots import robot_store
from stores.sessions import AUTO_SESSION_TITLE, session_store


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
async def test_list_hides_automatic_sessions(client):
    chat = session_store.create("robot-001", user_id="user_dev", title="Sắp xếp khay linh kiện")
    session_store.append_message(chat["id"], role="assistant", content="Xong.")
    session_store.create("robot-001", user_id="user_dev", title=AUTO_SESSION_TITLE, source="SCHEDULE")

    hidden = await client.get("/api/v1/sessions")
    assert hidden.status_code == 200
    titles = {row["title"] for row in hidden.json()["data"]}
    assert "Sắp xếp khay linh kiện" in titles
    assert "Phiên tự động" not in titles

    shown = await client.get("/api/v1/sessions?include_hidden=true")
    assert "Phiên tự động" in {row["title"] for row in shown.json()["data"]}


@pytest.mark.asyncio
async def test_list_search_and_robot_filter(client):
    khay = session_store.create("robot-001", user_id="user_dev", title="Sắp xếp khay linh kiện")
    session_store.append_message(khay["id"], role="assistant", content="Đã sắp xếp xong.")
    beta = session_store.create("robot-001", user_id="user_dev", title="Kiểm tra lô B-42")

    search = await client.get("/api/v1/sessions", params={"q": "khay", "include_hidden": False})
    titles = [row["title"] for row in search.json()["data"]]
    assert titles == ["Sắp xếp khay linh kiện"]

    filtered = await client.get("/api/v1/sessions", params={"robot_id": "robot-001"})
    assert {row["id"] for row in filtered.json()["data"]} == {khay["id"], beta["id"]}


@pytest.mark.asyncio
async def test_create_rename_delete_session(client):
    created = await client.post("/api/v1/sessions", json={"robot_id": "robot-001"})
    assert created.status_code == 201
    session_id = created.json()["data"]["id"]
    assert created.json()["data"]["title"] == "Phiên mới"
    assert created.json()["data"]["active_run"] is None

    detail = await client.get(f"/api/v1/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == session_id

    renamed = await client.patch(f"/api/v1/sessions/{session_id}", json={"title": "Ca sáng"})
    assert renamed.json()["data"]["title"] == "Ca sáng"

    deleted = await client.delete(f"/api/v1/sessions/{session_id}")
    assert deleted.status_code == 204
    missing = await client.get(f"/api/v1/sessions/{session_id}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_create_session_rejects_missing_robot(client):
    resp = await client.post("/api/v1/sessions", json={"robot_id": "robot-missing"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SCHED_004"


@pytest.mark.asyncio
async def test_patch_session_rejects_blank_title(client):
    created = await client.post("/api/v1/sessions", json={"robot_id": "robot-001"})
    session_id = created.json()["data"]["id"]
    resp = await client.patch(f"/api/v1/sessions/{session_id}", json={"title": "   "})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "VAL_006"


@pytest.mark.asyncio
async def test_list_messages_missing_session(client):
    resp = await client.get("/api/v1/sessions/ses_missing/messages")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "AGENT_012"


@pytest.mark.asyncio
async def test_delete_refused_while_running(client):
    session = session_store.create("robot-001", user_id="user_dev")
    session_store.set_active_run(session["id"], "run-active-001")
    resp = await client.delete(f"/api/v1/sessions/{session['id']}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "AGENT_020"


@pytest.mark.asyncio
async def test_list_messages(client):
    session = session_store.create("robot-001", user_id="user_dev")
    session_store.append_message(session["id"], role="user", content="Sắp xếp khay")
    session_store.append_message(session["id"], role="assistant", content="Đang làm.")
    resp = await client.get(f"/api/v1/sessions/{session['id']}/messages")
    assert resp.status_code == 200
    roles = [m["role"] for m in resp.json()["data"]]
    assert roles[0] == "user"
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_append_message_updates_preview():
    session = session_store.create("robot-001", user_id="user_dev")
    session_store.append_message(session["id"], role="user", content="Sắp xếp khay đỏ")
    row = session_store.get(session["id"], user_id="user_dev")
    assert row["title"] == "Sắp xếp khay đỏ"
    assert row["last_message_preview"] == "Sắp xếp khay đỏ"


@pytest.mark.asyncio
async def test_sessions_are_scoped_to_user(client):
    from datetime import UTC, datetime

    from auth.deps import require_user_if_configured
    from auth.store import User
    from main import app
    from stores.db import get_connection
    from stores.ids import utc_now_iso

    conn, _ = get_connection()
    conn.execute(
        """
        INSERT INTO users (id, email, google_sub, name, picture_url, timezone, locale, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            "user_other",
            "other@example.com",
            "other_sub",
            "Other",
            "",
            "Asia/Ho_Chi_Minh",
            "vi",
            utc_now_iso(),
        ),
    )
    conn.commit()

    other = session_store.create(
        "robot-001", user_id="user_other", title="Phiên của người khác"
    )
    session_store.append_message(other["id"], role="user", content="bí mật")
    mine = session_store.create("robot-001", user_id="user_dev", title="Của tôi")
    session_store.append_message(mine["id"], role="user", content="hello")

    listed = await client.get("/api/v1/sessions")
    ids = {row["id"] for row in listed.json()["data"]}
    assert mine["id"] in ids
    assert other["id"] not in ids

    assert (await client.get(f"/api/v1/sessions/{other['id']}")).status_code == 404
    assert (await client.get(f"/api/v1/sessions/{other['id']}/messages")).status_code == 404

    async def as_other():
        return User(
            id="user_other",
            email="other@example.com",
            google_sub="other_sub",
            name="Other",
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )

    app.dependency_overrides[require_user_if_configured] = as_other
    try:
        scoped = await client.get("/api/v1/sessions")
        scoped_ids = {row["id"] for row in scoped.json()["data"]}
        assert other["id"] in scoped_ids
        assert mine["id"] not in scoped_ids
    finally:
        app.dependency_overrides.pop(require_user_if_configured, None)


@pytest.mark.asyncio
async def test_list_starts_empty(client):
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_empty_drafts_are_hidden_and_reused(client):
    first = await client.post("/api/v1/sessions", json={"robot_id": "robot-001"})
    second = await client.post("/api/v1/sessions", json={"robot_id": "robot-001"})
    assert first.status_code == 201
    assert second.json()["data"]["id"] == first.json()["data"]["id"]

    listed = await client.get("/api/v1/sessions")
    assert listed.json()["data"] == []

    shown = await client.get("/api/v1/sessions?include_empty=true")
    assert len(shown.json()["data"]) == 1


@pytest.mark.asyncio
async def test_latest_session_returns_most_recent(client):
    missing = await client.get("/api/v1/sessions/latest")
    assert missing.status_code == 404

    older = session_store.create("robot-001", user_id="user_dev", title="Ca sáng")
    session_store.append_message(older["id"], role="user", content="hello")
    newer = session_store.create("robot-001", user_id="user_dev", title="Ca chiều")
    session_store.append_message(newer["id"], role="user", content="later")

    latest = await client.get("/api/v1/sessions/latest", params={"robot_id": "robot-001"})
    assert latest.status_code == 200
    assert latest.json()["data"]["id"] == newer["id"]


def test_sessions_persist_across_store_instances(test_database_url):
    from stores.sessions import SessionStore

    first = SessionStore(database_url=test_database_url)
    created = first.create("robot-001", user_id="user_dev", title="Ca sáng")
    first.append_message(created["id"], role="user", content="hello")

    second = SessionStore(database_url=test_database_url)
    loaded = second.get(created["id"], user_id="user_dev")
    assert loaded is not None
    assert loaded["title"] == "Ca sáng"
    messages = second.list_messages(created["id"], user_id="user_dev")
    assert messages is not None
    assert [m["content"] for m in messages] == ["hello"]
