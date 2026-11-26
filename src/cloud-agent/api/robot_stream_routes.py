from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import urlencode

import httpx
from agents.errors import AppError
from agents.tools.http_transport import HttpMcpTransport
from agents.tools.mcp_client import McpClient, ToolResult
from auth.deps import require_user_if_configured
from auth.store import User
from config import Settings, get_settings
from fastapi import APIRouter, Depends, Query
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from stores.edge_registry import edge_registry_store
from stores.robots import robot_store

from api.common import api_error, envelope

router = APIRouter(tags=["robot-streams"])
logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_S = 3.0
_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0)


def _base_url_for(robot_id: str) -> str:
    record = edge_registry_store.get(robot_id)
    if record is None:
        raise api_error(
            "ROBOT_003",
            "ID robot chưa đăng ký — bật thiết bị Edge lên trước khi ghép nối.",
            404,
        )
    base_url = str(record.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise api_error("ROBOT_040", "Edge chưa khai báo địa chỉ kết nối.", 503)
    return base_url


def _mcp_endpoint_for(base_url: str) -> str:
    if base_url.rstrip("/").endswith("/mcp"):
        return base_url.rstrip("/")
    return f"{base_url.rstrip('/')}/mcp"


def _require_robot(robot_id: str) -> dict:
    robot = robot_store.get(robot_id)
    if robot is None:
        raise api_error("SCHED_004", "Robot đã chọn không còn tồn tại.", 404)
    return robot


async def _call_edge_tool(
    *,
    settings: Settings,
    robot_id: str,
    tool_name: str,
    arguments: dict,
    timeout_s: float,
) -> ToolResult:
    base_url = _base_url_for(robot_id)
    mcp = McpClient(
        HttpMcpTransport(
            settings,
            endpoint_url=_mcp_endpoint_for(base_url),
            synthesize_read_tools=False,
        )
    )
    try:
        return await mcp.call_tool(
            robot_id=robot_id,
            tool_name=tool_name,
            arguments=arguments,
            trace_id=f"console_{tool_name}_{robot_id}",
            timeout_s=timeout_s,
        )
    except AppError as exc:
        raise api_error(
            exc.code,
            exc.message or "Không gọi được Edge.",
            exc.http_status,
        ) from exc
    except Exception as exc:
        raise api_error("ROBOT_041", "Không kết nối được tới Edge.", 503) from exc


def _backend_mode_from(result: ToolResult) -> str:
    if isinstance(result.data, dict) and result.data.get("backend_mode"):
        return str(result.data["backend_mode"])
    if isinstance(result.data, dict):
        nested_state = result.data.get("robot_state")
        if isinstance(nested_state, dict) and nested_state.get("backend_mode"):
            return str(nested_state["backend_mode"])
    if isinstance(result.robot_state, dict) and result.robot_state.get("backend_mode"):
        return str(result.robot_state["backend_mode"])
    return ""


def _is_virtual_environment_backend(backend_mode: str) -> bool:
    normalized = backend_mode.strip().lower()
    return normalized == "sim" or "libero" in normalized or "sim" in normalized


async def _probe_edge(base_url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT_S) as client:
            async with client.stream("GET", f"{base_url}/servo-stream"):
                return
    except httpx.HTTPError as exc:
        raise api_error("ROBOT_041", "Không kết nối được tới Edge.", 503) from exc


@router.post("/robots/{robot_id}/connect")
async def connect_robot_stream(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    _require_robot(robot_id)
    base_url = _base_url_for(robot_id)
    await _probe_edge(base_url)
    robot = robot_store.mark_connected(robot_id)
    return envelope(robot)


@router.post("/robots/{robot_id}/disconnect")
async def disconnect_robot_stream(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    _require_robot(robot_id)
    robot = robot_store.mark_disconnected(robot_id)
    return envelope(robot)


@router.get("/robots/{robot_id}/environment")
async def get_robot_environment(
    robot_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    _require_robot(robot_id)
    result = await _call_edge_tool(
        settings=settings,
        robot_id=robot_id,
        tool_name="get_robot_state",
        arguments={},
        timeout_s=5.0,
    )
    backend_mode = _backend_mode_from(result)
    return envelope(
        {
            "robot_id": robot_id,
            "backend_mode": backend_mode,
            "virtual": _is_virtual_environment_backend(backend_mode),
            "reset_available": _is_virtual_environment_backend(backend_mode),
        }
    )


@router.post("/robots/{robot_id}/environment/reset")
async def reset_robot_environment(
    robot_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> dict:
    _require_robot(robot_id)

    state = await _call_edge_tool(
        settings=settings,
        robot_id=robot_id,
        tool_name="get_robot_state",
        arguments={},
        timeout_s=5.0,
    )
    backend_mode = _backend_mode_from(state)
    if not _is_virtual_environment_backend(backend_mode):
        raise api_error(
            "ROBOT_043",
            "Reset môi trường chỉ khả dụng khi Edge chạy môi trường ảo LIBERO.",
            409,
        )
    result = await _call_edge_tool(
        settings=settings,
        robot_id=robot_id,
        tool_name="reset_episode",
        arguments={},
        timeout_s=10.0,
    )
    return envelope(
        {
            "robot_id": robot_id,
            "backend_mode": backend_mode,
            "reset": result.data,
        }
    )


async def _open_edge_stream(url: str) -> tuple[httpx.AsyncClient, httpx.Response]:
    client = httpx.AsyncClient(timeout=_STREAM_TIMEOUT)
    try:
        request = client.build_request("GET", url)
        response = await client.send(request, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise
    if response.status_code >= 400:
        await response.aclose()
        await client.aclose()
        raise httpx.HTTPStatusError(
            f"Edge stream returned HTTP {response.status_code}",
            request=response.request,
            response=response,
        )
    return client, response


async def _relay(
    robot_id: str,
    path: str,
    default_media_type: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    _require_robot(robot_id)
    if not robot_store.is_stream_connected(robot_id):
        raise api_error("ROBOT_042", "Chưa kết nối — bấm Kết nối robot trước.", 409)
    base_url = _base_url_for(robot_id)

    try:
        client, response = await _open_edge_stream(f"{base_url}{path}")
    except httpx.HTTPError as exc:
        raise api_error("ROBOT_041", "Không kết nối được tới Edge.", 503) from exc

    media_type = response.headers.get("content-type") or default_media_type
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",
    }
    if extra_headers:
        headers.update(extra_headers)

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        except httpx.HTTPError as exc:
            logger.info("Edge stream closed early: robot_id=%s path=%s error=%s", robot_id, path, exc)
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        media_type=media_type,
        headers=headers,
        background=BackgroundTask(client.aclose),
    )


@router.get("/robots/{robot_id}/stream/mjpeg")
async def stream_mjpeg(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
    camera: Annotated[str | None, Query(description="both|top|wrist|agentview")] = None,
) -> StreamingResponse:
    path = "/mjpeg"
    if camera:
        path = f"/mjpeg?{urlencode({'camera': camera})}"
    return await _relay(
        robot_id,
        path,
        "multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/robots/{robot_id}/stream/servo")
async def stream_servo(
    robot_id: str,
    _user: Annotated[User | None, Depends(require_user_if_configured)],
) -> StreamingResponse:
    return await _relay(robot_id, "/servo-stream", "text/event-stream")
