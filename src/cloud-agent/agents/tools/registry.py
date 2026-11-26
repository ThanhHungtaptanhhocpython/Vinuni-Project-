"""Tool Registry — nạp tool từ MCP capability và chuyển thành LangChain tool.

Theo DOC 1 §2.6: capability do Edge cung cấp (cache Redis trong production),
lọc theo allowlist, chuyển inputSchema (JSON Schema) → Pydantic, và bổ sung
hint an toàn vào description trước khi bind cho LLM.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from ..context import RunContext
from ..errors import AppError
from .mcp_client import ToolResult
from .policies import ALLOWED_TOOLS, TOOL_TIMEOUT
from .serialize import serialize_for_llm

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "object": dict,
}

# Hint hành vi Cloud bổ sung vào description của Edge (DOC 1 §2.6)
_CLOUD_HINTS: dict[str, str] = {
    "take_action": (
        "CẢNH BÁO: Công cụ này gây chuyển động vật lý thật. "
        "Chỉ gọi sau khi đã capture/capture_tool và xác nhận robot IDLE. "
        "Công cụ trả về ngay lập tức với episode_id; nhiệm vụ chạy nền. "
        "Dùng check_status để theo dõi tiến độ một cách blocking. "
        "Không gọi lại take_action khi episode trước chưa kết thúc. "
        "Chọn k đủ lớn: k quá thấp khiến episode bị cắt giữa chừng trước khi "
        "hoàn thành và bị tính là thất bại — dùng khoảng 60-100 cho pick-place "
        "đơn giản, cao hơn cho nhiệm vụ nhiều thao tác."
    ),
    "go_home": (
        "Gây chuyển động vật lý thật. Chỉ gọi khi không có vật trong gripper "
        "và không có vật cản trên đường về home."
    ),
    "stop_robot": "Dùng khi cần dừng khẩn cấp hoặc trước khi kết thúc do bất thường.",
    "capture_tool": "Luôn gọi trước mọi hành động vật lý để quan sát hiện trạng.",
    "capture": "Luôn gọi trước mọi hành động vật lý để quan sát hiện trạng.",
    "check_status": "Chặn (blocking) và chờ để kiểm tra trạng thái nhiệm vụ (chỉ đọc).",
}


def _field_type(spec: dict) -> Any:
    """Chuyển JSON Schema property → annotation Python/Pydantic sạch.

    Tránh `anyOf: [T, null]` và `items: {}` — nhiều free provider (Nvidia)
    reject schema đó với BadRequest 400.
    """
    if "anyOf" in spec:
        choices = [
            item
            for item in spec.get("anyOf", [])
            if isinstance(item, dict) and item.get("type") != "null"
        ]
        if choices:
            return _field_type(choices[0])

    if "enum" in spec:
        values = tuple(spec["enum"])
        if not values:
            return str
        return Literal[values]  # type: ignore[valid-type]

    type_name = spec.get("type", "string")
    if type_name == "array":
        items = spec.get("items") or {}
        item_type = _field_type(items) if items else str
        return list[item_type]  # type: ignore[valid-type]
    return _JSON_TYPE_MAP.get(type_name, str)


def jsonschema_to_pydantic(name: str, schema: dict) -> type[BaseModel]:
    """Chuyển JSON Schema (subset: object + primitive/enum/array) thành Pydantic model."""
    properties: dict = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}

    for key, spec in properties.items():
        py_type = _field_type(spec)
        description = spec.get("description")
        if key in required:
            fields[key] = (py_type, Field(..., description=description))
        elif "default" in spec:
            # Có default tường minh → không bọc optional (tránh anyOf null)
            fields[key] = (py_type, Field(spec["default"], description=description))
        else:
            fields[key] = (py_type | None, Field(default=None, description=description))

    # Tool không có arg: model rỗng, extra=forbid để schema hợp lệ
    if not fields:

        class EmptyArgs(BaseModel):
            model_config = ConfigDict(extra="forbid")

        EmptyArgs.__name__ = name
        EmptyArgs.__qualname__ = name
        return EmptyArgs
    return create_model(name, **fields)


def enrich_description(mcp_tool: dict) -> str:
    """MCP description do Edge cung cấp + hint an toàn của Cloud."""
    base = (mcp_tool.get("description") or "").strip()
    hint = _CLOUD_HINTS.get(mcp_tool["name"])
    return f"{base}\n{hint}" if hint else base


def mcp_to_langchain_tool(mcp_tool: dict, ctx: RunContext) -> StructuredTool:
    """Adapter MCP tool → LangChain StructuredTool (DOC 1 §2.6)."""
    if mcp_tool["name"] not in ALLOWED_TOOLS:
        raise AppError("MCP_005", detail={"tool": mcp_tool["name"]})

    args_schema = jsonschema_to_pydantic(
        f"{mcp_tool['name'].title().replace('_', '')}Args",
        mcp_tool.get("inputSchema") or {"type": "object", "properties": {}},
    )

    async def _call(**kwargs) -> str:
        real_tool_name = mcp_tool.get("_real_name", mcp_tool["name"])
        real_kwargs = dict(kwargs)

        wait_s = 0.0
        if real_tool_name == "get_task_status" and mcp_tool["name"] == "check_status":
            if "task_id" in real_kwargs:
                real_kwargs["episode_id"] = real_kwargs.pop("task_id")
            wait_s = float(real_kwargs.pop("wait_s", 0))
            if wait_s:
                import asyncio
                await asyncio.sleep(wait_s)

        result: ToolResult = await ctx.mcp.call_tool(
            robot_id=ctx.robot_id,
            tool_name=real_tool_name,
            arguments={k: v for k, v in real_kwargs.items() if v is not None},
            trace_id=ctx.trace_id,
            timeout_s=TOOL_TIMEOUT[mcp_tool["name"]] + wait_s,
        )
        return serialize_for_llm(result)  # bọc <tool_result untrusted="true">

    metadata = {}
    if "_real_name" in mcp_tool:
        metadata["real_name"] = mcp_tool["_real_name"]

    return StructuredTool(
        name=mcp_tool["name"],
        description=enrich_description(mcp_tool),
        args_schema=args_schema,
        coroutine=_call,
        metadata=metadata if metadata else None,
    )


async def load_tools(ctx: RunContext) -> tuple[list[dict], list[StructuredTool]]:
    """Nạp capability từ Edge, lọc allowlist, trả (capabilities, langchain_tools).

    get_task_status không bind cho LLM — vòng theo dõi episode trong tool_node
    tự gọi nó (DOC 1 §2.7).
    """
    capabilities = await ctx.mcp.list_tools(ctx.robot_id)
    allowed = [c for c in capabilities if c["name"] in ALLOWED_TOOLS]

    has_native_capture = any(c["name"] == "capture" for c in allowed)
    has_native_check_status = any(c["name"] == "check_status" for c in allowed)

    synthetic_capabilities = []
    for c in allowed:
        if c["name"] == "capture_tool" and not has_native_capture:
            cap_copy = dict(c)
            cap_copy["name"] = "capture"
            cap_copy["_real_name"] = "capture_tool"
            synthetic_capabilities.append(cap_copy)
        elif c["name"] == "get_task_status" and not has_native_check_status:
            cap_copy = dict(c)
            cap_copy["name"] = "check_status"
            cap_copy["_real_name"] = "get_task_status"
            cap_copy["inputSchema"] = {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "ID of the task/episode"},
                    "wait_s": {"type": "integer", "description": "Seconds to wait", "default": 0}
                },
                "required": ["task_id"]
            }
            synthetic_capabilities.append(cap_copy)

    allowed.extend(synthetic_capabilities)

    tools = [
        mcp_to_langchain_tool(c, ctx) for c in allowed if c["name"] != "get_task_status"
    ]
    return allowed, tools
