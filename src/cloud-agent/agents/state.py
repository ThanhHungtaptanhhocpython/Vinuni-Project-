"""AgentState cho LangGraph agent — theo DOC 1 §2.2."""

from __future__ import annotations

from typing import Annotated, Literal

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ToolCallRecord(TypedDict):
    tool_call_id: str  # tc_...
    tool_name: str
    arguments: dict
    status: str  # PENDING|RUNNING|SUCCEEDED|FAILED|CANCELLED|TIMEOUT
    started_at: str
    ended_at: str | None
    duration_ms: int | None
    error_code: str | None


class AgentState(TypedDict, total=False):
    # --- Định danh ---
    run_id: str
    session_id: str
    user_id: str
    robot_id: str
    trace_id: str
    source: Literal["CHAT", "SCHEDULE", "MANUAL_RUN", "SYSTEM"]

    # --- Hội thoại ---
    messages: Annotated[list, add_messages]

    # --- Tool ---
    available_tools: list[dict]  # capability nạp từ Edge
    tool_call_history: list[ToolCallRecord]
    pending_tool_results: list  # transient: tool_node → observe_node trong cùng vòng lặp

    # --- Counter (dùng cho guard) ---
    iteration: int  # số vòng LLM
    tool_call_count: int
    take_action_count: int

    # --- Ngữ cảnh robot ---
    robot_state: dict | None  # RobotState [DOC 0 §3.5.3]
    current_episode_id: str | None
    scene_memory: dict  # ephemeral object/observation memory for this run
    task_memory: dict  # persisted session-level task memory snapshot
    long_term_memories: list[dict]  # retrieved user-level semantic memory

    # --- Artifact sinh ra trong run ---
    artifacts: list[dict]  # ImageRef [DOC 0 §3.5.6]

    # --- Kiểm soát ---
    started_at: str
    deadline_at: str  # started_at + AGENT_MAX_RUN_DURATION_S
    cancel_requested: bool
    final_status: str | None  # RunStatus
    error: dict | None  # ErrorObject
