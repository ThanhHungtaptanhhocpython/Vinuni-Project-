"""RunContext — dependency của một run, truyền qua config["configurable"].

Không nằm trong AgentState vì chứa object sống (MCP client, asyncio.Event...)
không thuộc diện checkpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config import Settings, get_settings

from .events import EventEmitter, LoggingEmitter
from .verbose_logger import AgentVerboseLogger

if TYPE_CHECKING:
    from langchain_core.messages import AIMessage, BaseMessage

    from .tools.mcp_client import McpClient

# Giá USD / 1M token — chỉ để ước lượng chi phí ghi vào runs.llm_usage (DOC 1 §2.9)
_PRICE_PER_1M = {
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
}


@dataclass
class RunContext:
    run_id: str
    session_id: str
    user_id: str
    robot_id: str
    trace_id: str
    source: str = "CHAT"
    user_tz: str = "Asia/Ho_Chi_Minh"

    mcp: McpClient | None = None
    emitter: EventEmitter = field(default_factory=LoggingEmitter)
    settings: Settings = field(default_factory=get_settings)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    verbose: AgentVerboseLogger = field(init=False)

    def __post_init__(self):
        self.verbose = AgentVerboseLogger(self.run_id, self.settings)

    # Chu kỳ poll get_task_status khi theo dõi episode (DOC 1 §2.7)
    episode_poll_interval_s: float = 2.0
    # Mỗi N lần poll, gọi get_robot_state (an toàn: error_flags / nhiệt độ)
    robot_state_poll_every: int = 1
    robot_state_temp_limit_c: float = 75.0

    # Hook cho test: thay thế toàn bộ chiến lược gọi LLM
    llm_invoke: Callable[[list[BaseMessage], list], Awaitable[AIMessage]] | None = None

    # Nạp trong preflight/load_context — object sống, không đưa vào AgentState
    lc_tools: list = field(default_factory=list)
    base_system_prompt: str = ""
    system_prompt: str = ""

    # Trạng thái tích luỹ trong run
    degraded: bool = False
    # Runtime-only activity snapshot used by emergency cancel. These fields are
    # intentionally outside AgentState/checkpoints because they describe what
    # this process is doing right now.
    active_tool_name: str | None = None
    active_tool_args: dict[str, Any] = field(default_factory=dict)
    active_episode_id: str | None = None
    safety_stop_attempted: bool = False
    # Đếm số lượt gọi LLM trong run — cấp `message_id` ổn định cho mỗi lượt
    # (một agent_node có thể gọi invoke_llm 2 lần khi retry invalid_tool_calls,
    # nên không thể dùng `state["iteration"]` một mình làm khoá duy nhất).
    _llm_call_counter: int = field(default=0, init=False, repr=False)

    def next_message_id(self) -> str:
        self._llm_call_counter += 1
        return f"{self.run_id}-m{self._llm_call_counter}"

    llm_usage: dict[str, Any] = field(
        default_factory=lambda: {
            "calls": 0,
            "model": "",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "image_count": 0,
            "estimated_cost_usd": 0.0,
        }
    )

    def record_usage(self, usage: dict | None, model: str, image_count: int = 0) -> None:
        """Ghi nhận chi phí mỗi lần gọi LLM — ghi vào runs.llm_usage (DOC 1 §2.9)."""
        u = self.llm_usage
        u["calls"] += 1
        u["model"] = model
        u["image_count"] += image_count
        if usage:
            u["prompt_tokens"] += usage.get("input_tokens", 0)
            u["completion_tokens"] += usage.get("output_tokens", 0)
            details = usage.get("input_token_details") or {}
            u["cached_tokens"] += details.get("cache_read", 0)
            price_in, price_out = _PRICE_PER_1M.get(model, (0.0, 0.0))
            u["estimated_cost_usd"] += round(
                usage.get("input_tokens", 0) * price_in / 1e6
                + usage.get("output_tokens", 0) * price_out / 1e6,
                6,
            )


def get_run_context(config: dict) -> RunContext:
    """Lấy RunContext từ RunnableConfig của LangGraph."""
    ctx = (config or {}).get("configurable", {}).get("run_ctx")
    if ctx is None:
        raise RuntimeError(
            "RunContext chưa được truyền vào config['configurable']['run_ctx']"
        )
    return ctx
