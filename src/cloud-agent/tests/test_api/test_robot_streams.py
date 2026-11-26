import httpx
import pytest
from stores.edge_registry import edge_registry_store
from stores.robots import robot_store


def _pair_robot(*, base_url: str = "http://edge.test") -> None:
    edge_registry_store.register_if_new(
        robot_id="robot-edge-stream",
        robot_type="franka",
        base_url=base_url,
        capabilities=[],
    )
    robot_store.create(robot_id="robot-edge-stream", name="Edge Stream", model="franka")


class _FakeEdgeResponse:
    def __init__(self, url: str) -> None:
        self.url = url
        self.status_code = 200
        self.request = httpx.Request("GET", url)
        self.headers = {
            "content-type": (
                "text/event-stream"
                if url.endswith("/servo-stream")
                else "multipart/x-mixed-replace; boundary=frame"
            )
        }

    async def aiter_bytes(self):
        if self.url.endswith("/servo-stream"):
            yield b"data: {\"joints\": []}\n\n"
        else:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\njpeg\r\n"

    async def aclose(self) -> None:
        return None


class _FakeStreamContext:
    def __init__(self, url: str) -> None:
        self.response = _FakeEdgeResponse(url)

    async def __aenter__(self) -> _FakeEdgeResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.response.aclose()


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def stream(self, method: str, url: str):
        assert method == "GET"
        return _FakeStreamContext(url)

    def build_request(self, method: str, url: str) -> httpx.Request:
        return httpx.Request(method, url)

    async def send(self, request: httpx.Request, *, stream: bool = False) -> _FakeEdgeResponse:
        assert stream is True
        return _FakeEdgeResponse(str(request.url))

    async def aclose(self) -> None:
        return None


class _FailingAsyncClient(_FakeAsyncClient):
    def stream(self, method: str, url: str):
        raise httpx.ConnectError("edge down")

    async def send(self, request: httpx.Request, *, stream: bool = False) -> _FakeEdgeResponse:
        raise httpx.ConnectError("edge down")


@pytest.mark.asyncio
async def test_stream_requires_connect_gate(client, monkeypatch):
    _pair_robot()
    monkeypatch.setattr("api.robot_stream_routes.httpx.AsyncClient", _FakeAsyncClient)

    resp = await client.get("/api/v1/robots/robot-edge-stream/stream/servo")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ROBOT_042"


@pytest.mark.asyncio
async def test_connect_marks_robot_online(client, monkeypatch):
    _pair_robot()
    monkeypatch.setattr("api.robot_stream_routes.httpx.AsyncClient", _FakeAsyncClient)

    resp = await client.post("/api/v1/robots/robot-edge-stream/connect")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["connection_status"] == "ONLINE"
    assert robot_store.is_stream_connected("robot-edge-stream") is True


@pytest.mark.asyncio
async def test_connect_without_edge_base_url(client):
    _pair_robot(base_url="")

    resp = await client.post("/api/v1/robots/robot-edge-stream/connect")

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "ROBOT_040"


@pytest.mark.asyncio
async def test_connect_probe_failure(client, monkeypatch):
    _pair_robot()
    monkeypatch.setattr("api.robot_stream_routes.httpx.AsyncClient", _FailingAsyncClient)

    resp = await client.post("/api/v1/robots/robot-edge-stream/connect")

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "ROBOT_041"


@pytest.mark.asyncio
async def test_relay_servo_after_connect(client, monkeypatch):
    _pair_robot()
    monkeypatch.setattr("api.robot_stream_routes.httpx.AsyncClient", _FakeAsyncClient)
    await client.post("/api/v1/robots/robot-edge-stream/connect")

    resp = await client.get("/api/v1/robots/robot-edge-stream/stream/servo")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert b"joints" in resp.content


@pytest.mark.asyncio
async def test_disconnect_closes_gate(client, monkeypatch):
    _pair_robot()
    monkeypatch.setattr("api.robot_stream_routes.httpx.AsyncClient", _FakeAsyncClient)
    await client.post("/api/v1/robots/robot-edge-stream/connect")

    resp = await client.post("/api/v1/robots/robot-edge-stream/disconnect")

    assert resp.status_code == 200
    assert resp.json()["data"]["connection_status"] == "OFFLINE"
    assert robot_store.is_stream_connected("robot-edge-stream") is False
