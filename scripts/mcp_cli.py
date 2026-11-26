"""Interactive CLI để gọi tay từng MCP tool của edge_vla — không qua agent.

Kết nối bằng streamable-http tới server đang chạy (`./scripts/run_edge_vla.sh`),
liệt kê tool, cho chọn + nhập tham số, in kết quả JSON. Dùng để kiểm tra nhanh
xem một tool cụ thể có hoạt động không mà không cần dựng backend/UI đầy đủ.

Chạy:
    ./scripts/run_edge_vla.sh                    # terminal 1: khởi động edge MCP server
    uv run --project src/edge_vla --directory src \\
        python ../../scripts/mcp_cli.py           # terminal 2: CLI này

Hoặc chỉ định URL khác:
    EDGE_VLA_MCP_URL=http://localhost:8931/mcp python scripts/mcp_cli.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_URL = os.environ.get("EDGE_VLA_MCP_URL", "http://localhost:8931/mcp")


def _print_json(label: str, data) -> None:
    print(f"\n=== {label} ===")
    try:
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    except TypeError:
        print(data)


def _coerce(raw: str, schema_type: str | None):
    raw = raw.strip()
    if raw == "":
        return None
    if schema_type == "integer":
        return int(raw)
    if schema_type == "number":
        return float(raw)
    if schema_type == "boolean":
        return raw.lower() in ("1", "true", "yes", "y")
    return raw


def _prompt_args(tool) -> dict:
    schema = tool.inputSchema or {}
    props: dict = schema.get("properties", {})
    required = set(schema.get("required", []))
    args: dict = {}
    if not props:
        return args
    print(f"\n-- Tham số cho {tool.name} (Enter để bỏ qua tham số optional) --")
    for name, prop in props.items():
        ptype = prop.get("type")
        opt_hint = "" if name in required else " (optional)"
        raw = input(f"  {name} [{ptype}]{opt_hint}: ")
        if raw.strip() == "":
            if name in required:
                print(f"  '{name}' là bắt buộc, thử lại.")
                raw = input(f"  {name} [{ptype}]: ")
            else:
                continue
        value = _coerce(raw, ptype)
        if value is not None:
            args[name] = value
    return args


async def run() -> None:
    url = DEFAULT_URL
    print(f"Đang kết nối MCP server tại {url} ...")
    try:
        async with streamablehttp_client(url) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tools = {t.name: t for t in tools_result.tools}
                print(f"Kết nối OK. {len(tools)} tool khả dụng.")
                await _menu_loop(session, tools)
    except Exception as exc:  # noqa: BLE001
        print(f"\nKhông kết nối được tới {url}: {exc}")
        print("Kiểm tra: server đã chạy chưa (./scripts/run_edge_vla.sh)?")
        sys.exit(1)


async def _menu_loop(session: ClientSession, tools: dict) -> None:
    names = list(tools.keys())
    while True:
        print("\n" + "=" * 50)
        for i, name in enumerate(names, start=1):
            desc = (tools[name].description or "").strip().splitlines()[0]
            print(f"  {i}. {name} — {desc}")
        print("  r. refresh danh sách tool")
        print("  q. thoát")
        choice = input("\nChọn tool: ").strip().lower()

        if choice in ("q", "quit", "exit"):
            print("Bye.")
            return
        if choice in ("r", "refresh"):
            tools_result = await session.list_tools()
            tools.clear()
            tools.update({t.name: t for t in tools_result.tools})
            names = list(tools.keys())
            continue

        try:
            idx = int(choice) - 1
            name = names[idx]
        except (ValueError, IndexError):
            print("Lựa chọn không hợp lệ.")
            continue

        tool = tools[name]
        try:
            args = _prompt_args(tool)
        except ValueError as exc:
            print(f"Tham số không hợp lệ: {exc}")
            continue

        print(f"\nGọi {name}({args}) ...")
        try:
            result = await session.call_tool(name, args)
        except Exception as exc:  # noqa: BLE001
            print(f"LỖI khi gọi tool: {exc}")
            continue

        if result.isError:
            _print_json(f"{name} — LỖI", [c.model_dump() for c in result.content])
            continue

        for content in result.content:
            if hasattr(content, "text"):
                try:
                    payload = json.loads(content.text)
                except (json.JSONDecodeError, TypeError):
                    payload = content.text
                _print_json(name, payload)
            else:
                _print_json(name, content.model_dump())


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nBye.")
