"""HTTP transport for MCP Streamable HTTP endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from config import Settings


class HttpMcpTransport:
    """MCP transport over Streamable HTTP.

    The LIBERO endpoint returns JSON-RPC responses as SSE events, so this client
    performs the MCP initialize handshake and extracts the `data:` payload.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        endpoint_url: str | None = None,
        synthesize_read_tools: bool = True,
    ) -> None:
        self.settings = settings
        self.endpoint_url = endpoint_url or settings.mcp_endpoint_url
        self.synthesize_read_tools = synthesize_read_tools
        self._session_id: str | None = None
        self._request_id = 0
        self._init_lock = asyncio.Lock()
        self._capabilities: list[dict] | None = None
        self._robot_state_summary: dict[str, Any] = {}
        self._current_episode_id: str | None = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        if self.settings.mcp_auth_token:
            headers["Authorization"] = f"Bearer {self.settings.mcp_auth_token}"
        return headers

    @staticmethod
    def _parse_sse_json(text: str, request_id: int | None = None) -> dict:
        events: list[str] = []
        current: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.rstrip("\r")
            if not line:
                if current:
                    events.append("\n".join(current))
                    current = []
                continue
            if line.startswith("data:"):
                current.append(line.removeprefix("data:").lstrip())
        if current:
            events.append("\n".join(current))

        last_payload: dict | None = None
        for data in events:
            payload = json.loads(data)
            last_payload = payload
            if request_id is None or payload.get("id") == request_id:
                return payload
        if last_payload is not None:
            return last_payload
        raise ValueError("MCP SSE response did not contain a data event")

    @staticmethod
    def _decode_content_json(result: dict) -> dict:
        for part in result.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                text = str(part.get("text") or "").strip()
                if text.startswith("{"):
                    return json.loads(text)
                return {"ok": not result.get("isError", False), "data": {"text": text}}
        return {"ok": not result.get("isError", False), "data": result}

    async def _post(self, payload: dict, timeout_s: float) -> dict | None:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout_s) as client:
            response = await client.post(
                self.endpoint_url,
                headers=self._headers(),
                json=payload,
            )

        if response.headers.get("mcp-session-id"):
            self._session_id = response.headers["mcp-session-id"]

        if response.status_code == 202 or not response.content:
            return None

        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            message = self._parse_sse_json(response.text, payload.get("id"))
        else:
            message = response.json()

        if "error" in message:
            err = message["error"]
            raise RuntimeError(err.get("message") or str(err))
        return message.get("result")

    async def _ensure_initialized(self) -> None:
        if self._session_id:
            return
        async with self._init_lock:
            if self._session_id:
                return
            init_id = self._next_id()
            await self._post(
                {
                    "jsonrpc": "2.0",
                    "id": init_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": self.settings.mcp_protocol_version,
                        "capabilities": {},
                        "clientInfo": {
                            "name": self.settings.mcp_client_name,
                            "version": self.settings.mcp_client_version,
                        },
                    },
                },
                timeout_s=20.0,
            )
            await self._post(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                timeout_s=10.0,
            )

    def _synthetic_robot_state(self) -> dict:
        current = self._robot_state_summary.get("current_episode_id") or self._current_episode_id
        return {
            "status": "RUNNING" if current else "IDLE",
            "error_flags": [],
            "temperature_c": 0.0,
            "payload_g": 0,
            "backend_mode": self._robot_state_summary.get("backend_mode", "libero_sim_eval"),
            "task_suite": self._robot_state_summary.get("task_suite"),
            "policy_path": self._robot_state_summary.get("policy_path"),
            "current_episode_id": current,
        }

    @staticmethod
    def _synthetic_capability(name: str, description: str) -> dict:
        return {
            "name": name,
            "version": "synthetic",
            "description": description,
            "inputSchema": {"type": "object", "properties": {}},
        }

    async def is_connected(self, robot_id: str) -> bool:
        try:
            await self._ensure_initialized()
            return True
        except Exception:
            self._session_id = None
            return False

    async def list_tools(self, robot_id: str) -> list[dict]:
        await self._ensure_initialized()
        if self._capabilities is not None:
            return self._capabilities

        result = await self._post(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}},
            timeout_s=20.0,
        )
        tools = list((result or {}).get("tools") or [])
        names = {tool.get("name") for tool in tools}
        if "get_robot_state" not in names:
            tools.append(
                self._synthetic_capability(
                    "get_robot_state",
                    "Return the current LIBERO simulator state summary.",
                )
            )
        if "health_check" not in names:
            tools.append(
                self._synthetic_capability(
                    "health_check",
                    "Return MCP connection health for the LIBERO simulator.",
                )
            )
        self._capabilities = tools
        return tools

    def _sanitize_arguments(self, tool_name: str, arguments: dict) -> dict:
        clean = dict(arguments)
        if not self.settings.mcp_libero_quirks:
            # SO-101 edge_vla/mcp_server nhận tham số nguyên bản theo DOC 0 — không rewrite.
            return clean
        if tool_name == "capture_tool":
            clean.pop("detail_level", None)
            if isinstance(clean.get("cameras"), list):
                clean["cameras"] = [
                    "agentview" if camera == "top" else camera for camera in clean["cameras"]
                ]
        elif tool_name == "take_action":
            if "max_duration_s" in clean and "timeout_s" not in clean:
                clean["timeout_s"] = clean.pop("max_duration_s")
        elif tool_name == "stop_robot":
            if "mode" in clean and "reason" not in clean:
                clean["reason"] = f"cloud_agent_{clean.pop('mode')}".lower()
        return clean

    # ToolResult's own top-level fields (mcp_client.py) — anything else native
    # edge_vla returns is a tool-specific field that belongs under `data`.
    _TOOL_RESULT_FIELDS = frozenset(
        {"ok", "tool_name", "data", "images", "robot_state", "error", "duration_ms", "isError"}
    )

    def _normalize_tool_result(self, tool_name: str, raw: dict, arguments: dict) -> dict:
        payload = dict(raw)
        payload.setdefault("ok", not payload.get("isError", False))
        payload.setdefault("tool_name", tool_name)
        payload.setdefault("data", {})

        # Native edge_vla (src/edge_vla/server.py) returns every tool's own fields
        # flat at the top level (e.g. take_action -> {"task_id":..., "status":...}),
        # never nested under `data` the way the old capture_tool/get_task_status
        # envelope (DOC 0 §3.5.5) did. `serialize_for_llm()` only reads
        # `result.data`/`robot_state`/`error` — anything left at the top level as a
        # Pydantic "extra" field is invisible to the LLM. Fold it into `data` here,
        # generically, instead of special-casing every tool's field names.
        for key, value in raw.items():
            if key not in self._TOOL_RESULT_FIELDS:
                payload["data"].setdefault(key, value)

        summary = payload.get("robot_state_summary")
        if isinstance(summary, dict):
            self._robot_state_summary = summary
        if tool_name == "capture_tool":
            # Old-naming Edge: ảnh trong `data.images` theo envelope chuẩn (DOC 0 §3.5.5 —
            # payload riêng của tool luôn nằm trong `data`). `ToolResult.images`
            # (mcp_client.py) là field top-level dùng để build nội dung multimodal cho LLM
            # (serialize.py) — phải lift lên đây, nếu không ảnh chụp được sẽ không bao giờ
            # tới được model.
            images = (payload.get("data") or {}).get("images")
            if isinstance(images, list):
                payload.setdefault("images", images)
        elif isinstance(payload.get("images"), dict):
            # Native edge_vla (src/edge_vla/server.py) puts `images` as
            # `{camera_id: base64_jpeg}` at the top level — not just on `capture`,
            # but on any tool whose result carries frames: `check_status`'s
            # `store.get(task_id)` includes the frames captured when a take_action
            # run finished (core/controller.py's `_run_task`), so the agent can see
            # the outcome without a separate capture() call. Either way the shape
            # is a dict, not `list[ImageRef]` (that's the old `capture_tool`
            # envelope's shape) — convert here rather than per tool_name.
            payload["images"] = [
                {"url": f"file://{camera_id}", "camera_id": camera_id, "thumbnail_b64": b64}
                for camera_id, b64 in payload["images"].items()
            ]
        if tool_name == "take_action":
            data = payload.get("data") or {}
            # Native edge_vla calls this `task_id`, not `episode_id` (see fold-in
            # above) — accept either so tracking works regardless of which Edge
            # naming is live.
            episode_id = data.get("episode_id") or data.get("task_id")
            if episode_id:
                self._current_episode_id = str(episode_id)
            # Native `take_action` has no `timeout_s` arg (real args: subgoal/k/
            # libero_task_id). `_monitor_episode` turns estimated_duration_s into
            # a hard deadline (+30s). Scale from k so slow headless sim (k=500
            # on osmesa) is not aborted at ~210s while still at step 497/500.
            from .policies import estimate_episode_duration_s

            if arguments.get("timeout_s"):
                default_est = float(arguments["timeout_s"])
            else:
                default_est = estimate_episode_duration_s(arguments.get("k"))
            payload["data"].setdefault("estimated_duration_s", default_est)
        elif tool_name in ("get_task_status", "check_status"):
            status = (payload.get("data") or {}).get("status")
            # Old naming: SUCCESS/STOPPED/MAX_STEPS/SAFETY_ABORT/FAILED. Native
            # edge_vla (core/task_store.py): DONE/ABORTED/TIMEOUT/SAFETY_STOP/
            # FAILED_MAX_RETRY. Accept both terminal-status vocabularies.
            if status in {
                "SUCCESS", "STOPPED", "TIMEOUT", "MAX_STEPS", "SAFETY_ABORT", "FAILED",
                "DONE", "ABORTED", "SAFETY_STOP", "FAILED_MAX_RETRY",
            }:
                self._current_episode_id = None

        payload.setdefault("robot_state", self._synthetic_robot_state())
        return payload

    async def call_tool(
        self, robot_id: str, tool_name: str, arguments: dict, timeout_s: float
    ) -> dict:
        await self._ensure_initialized()

        # Always call Edge for get_robot_state (synthetic IDLE wiped BUSY polls).
        # health_check may stay synthetic when synthesize_read_tools is on.
        if self.synthesize_read_tools and tool_name == "health_check":
            return {
                "ok": True,
                "tool_name": tool_name,
                "data": {"status": "healthy", "endpoint_url": self.endpoint_url},
                "robot_state": self._synthetic_robot_state(),
                "duration_ms": 0,
            }

        # Call Edge for get_robot_state too — a synthetic IDLE stub made
        # dashboard/robots polls wipe registry BUSY during take_action.
        clean_args = self._sanitize_arguments(tool_name, arguments)
        result = await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": clean_args},
            },
            timeout_s=timeout_s + 5.0,
        )
        raw = self._decode_content_json(result or {})
        return self._normalize_tool_result(tool_name, raw, clean_args)
