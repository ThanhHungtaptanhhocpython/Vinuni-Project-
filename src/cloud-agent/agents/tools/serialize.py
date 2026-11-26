"""Chuyển ToolResult thành nội dung cho LLM.

Mọi dữ liệu từ Edge được bọc trong <tool_result untrusted="true"> để đánh dấu
ranh giới dữ liệu không tin cậy [DOC 0 §6.7]; ảnh đưa vào theo DOC 1 §2.4.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, ToolMessage

from .mcp_client import ToolResult

IMAGE_PLACEHOLDER = "[Ảnh từ camera {camera} chụp lúc {captured_at} — đã lược bỏ khỏi ngữ cảnh]"


def serialize_for_llm(result: ToolResult) -> str:
    """Phần text của ToolResult, bọc khối untrusted (không gồm ảnh)."""
    payload: dict = {"ok": result.ok, "tool": result.tool_name}
    if result.data:
        payload["data"] = result.data
    if result.robot_state is not None:
        payload["robot_state"] = result.robot_state
    if result.error is not None:
        payload["error"] = result.error.model_dump(exclude_none=True)
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f'<tool_result untrusted="true">\n{body}\n</tool_result>'


def build_tool_message(result: ToolResult, tool_call_id: str) -> ToolMessage:
    """Dựng ToolMessage cho LLM: text bọc untrusted (không gồm ảnh)."""
    return ToolMessage(
        content=serialize_for_llm(result),
        tool_call_id=tool_call_id,
        name=result.tool_name,
        status="success" if result.ok else "error",
    )


def build_image_message(result: ToolResult) -> HumanMessage | None:
    """Dựng HumanMessage chứa ảnh từ ToolResult nếu có."""
    if not result.images:
        return None

    content: list[dict] = [
        {"type": "text", "text": f"Anh moi nhat sau tool `{result.tool_name}`:"}
    ]
    for img in result.images:
        url = img.url
        if not url.startswith(("http://", "https://", "data:")):
            # Pre-Phase-8 dev: `url` là `file://` local (chưa có presigned URL thật).
            # Dùng thumbnail base64 (luôn là JPEG — capture.py) thay thế nếu có.
            if not img.thumbnail_b64:
                continue
            url = f"data:image/jpeg;base64,{img.thumbnail_b64}"
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": url,
                    "detail": img.detail,
                    # `context_builder.py` (strip_all_images/strip_old_images) đọc lại 2 field
                    # này để dựng IMAGE_PLACEHOLDER khi ảnh bị cắt khỏi context.
                    "camera": img.camera_id,
                    "captured_at": img.captured_at,
                },
            }
        )

    if not content:
        return None

    return HumanMessage(content=content)
