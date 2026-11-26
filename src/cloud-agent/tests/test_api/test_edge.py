import pytest
from auth.edge_tokens import create_edge_registration_token
from config import get_settings
from stores import reset_console_stores
from stores.edge_registry import edge_registry_store

_SECRET = "test-only-edge-jwt-secret-do-not-use-32"


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_console_stores()
    yield
    reset_console_stores()


def _token(
    robot_id: str = "robot-edge-01",
    robot_type: str = "franka",
    secret: str = _SECRET,
    base_url: str = "http://edge.local:8931",
) -> str:
    return create_edge_registration_token(
        robot_id=robot_id,
        robot_type=robot_type,
        capabilities=[{"name": "take_action", "version": "1.0"}],
        secret=secret,
        base_url=base_url,
    )


@pytest.mark.asyncio
async def test_register_edge_creates_ground_truth(client):
    resp = await client.post(
        "/api/v1/edge/register",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["created"] is True
    assert body["robot_id"] == "robot-edge-01"
    assert body["robot_type"] == "franka"
    assert body["base_url"] == "http://edge.local:8931"
    assert body["capabilities"] == [{"name": "take_action", "version": "1.0"}]

    record = edge_registry_store.get("robot-edge-01")
    assert record is not None
    assert record["robot_type"] == "franka"


@pytest.mark.asyncio
async def test_register_edge_is_noop_when_already_registered(client):
    edge_registry_store.register_if_new(
        robot_id="robot-edge-02", robot_type="so101", base_url="http://old-edge", capabilities=[]
    )
    resp = await client.post(
        "/api/v1/edge/register",
        headers={"Authorization": f"Bearer {_token('robot-edge-02', 'franka', base_url='http://new-edge')}"},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["created"] is False
    # Type/capabilities from the FIRST registration are kept, not overwritten.
    assert body["robot_type"] == "so101"
    # Transport details are refreshed because tunnel/LAN URLs can change each boot.
    assert body["base_url"] == "http://new-edge"


@pytest.mark.asyncio
async def test_register_edge_rejects_wrong_secret(client):
    resp = await client.post(
        "/api/v1/edge/register",
        headers={"Authorization": f"Bearer {_token(secret='wrong-secret-wrong-secret-wrong-32')}"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "EDGE_001"


@pytest.mark.asyncio
async def test_register_edge_missing_header(client):
    resp = await client.post("/api/v1/edge/register")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "EDGE_001"


@pytest.mark.asyncio
async def test_get_edge_registry_ok(client):
    edge_registry_store.register_if_new(
        robot_id="robot-edge-03", robot_type="so101", base_url="http://edge-03", capabilities=[]
    )
    resp = await client.get("/api/v1/edge/registry/robot-edge-03")
    assert resp.status_code == 200
    assert resp.json()["data"]["robot_type"] == "so101"


@pytest.mark.asyncio
async def test_get_edge_registry_missing(client):
    resp = await client.get("/api/v1/edge/registry/robot-never-booted")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "EDGE_002"


@pytest.mark.asyncio
async def test_register_edge_missing_secret_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "edge_jwt_secret", "")
    resp = await client.post(
        "/api/v1/edge/register",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "EDGE_000"
