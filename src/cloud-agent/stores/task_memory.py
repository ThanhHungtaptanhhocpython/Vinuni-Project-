"""Session-level task memory persisted in the primary database."""

from __future__ import annotations

import json
import threading
from typing import Any

from stores.db import DbConnection, get_connection
from stores.ids import utc_now_iso

MAX_RECENT_EVENTS = 20
MAX_COMPLETED_TASKS = 20
MAX_FAILED_TASKS = 20
MAX_SUBGOALS = 50
MAX_TOOL_CALLS = 30


def empty_task_memory() -> dict[str, Any]:
    return {
        "active_goal": None,
        "completed_tasks": [],
        "failed_tasks": [],
        "interrupted_tasks": [],
        "completed_subgoals": [],
        "remaining_tasks": [],
        "last_failed_action": None,
        "recent_events": [],
        "tool_calls": [],
        "last_run_id": None,
        "last_updated_at": None,
    }


def _load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return empty_task_memory()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return empty_task_memory()
    return {**empty_task_memory(), **data} if isinstance(data, dict) else empty_task_memory()


def render_task_memory(memory: dict[str, Any] | None) -> str:
    memory = memory or empty_task_memory()
    active_goal = memory.get("active_goal") or "Chua co active goal ro rang."
    completed = memory.get("completed_tasks") or []
    failed = memory.get("failed_tasks") or []
    interrupted = memory.get("interrupted_tasks") or []
    completed_subgoals = memory.get("completed_subgoals") or []
    remaining = memory.get("remaining_tasks") or []
    last_failed = memory.get("last_failed_action")
    recent = memory.get("recent_events") or []
    tool_calls = memory.get("tool_calls") or []

    def _lines(items: list[dict[str, Any]], field: str) -> str:
        if not items:
            return "- Khong co."
        return "\n".join(f"- {item.get(field) or item.get('user_message') or 'unknown'}" for item in items[-5:])

    recent_lines = "- Khong co."
    if recent:
        recent_lines = "\n".join(f"- {item.get('status')}: {item.get('user_message')}" for item in recent[-5:])

    tool_call_lines = "- Khong co."
    if tool_calls:
        tool_call_lines = "\n".join(
            f"- {item.get('tool_name')}: {item.get('status')}"
            + (f", error={item.get('error_code')}" if item.get("error_code") else "")
            for item in tool_calls[-10:]
        )

    return (
        "## Session task memory\n"
        f"Active goal: {active_goal}\n\n"
        f"Completed tasks:\n{_lines(completed, 'task')}\n\n"
        f"Failed tasks:\n{_lines(failed, 'task')}\n\n"
        f"Interrupted/cancelled tasks:\n{_lines(interrupted, 'task')}\n\n"
        f"Completed subgoals:\n{_lines(completed_subgoals, 'subgoal')}\n\n"
        f"Remaining tasks:\n{_lines(remaining, 'task')}\n\n"
        f"Last failed action:\n{last_failed or '- Khong co.'}\n\n"
        f"Recent task events:\n{recent_lines}\n\n"
        f"Recent tool calls (across turns):\n{tool_call_lines}\n\n"
        "Quy tac: dung task memory de hieu nhiem vu hien tai; "
        "neu task nam trong Interrupted/cancelled tasks thi KHONG tu tiep tuc task do "
        "tru khi user noi ro 'tiep tuc'/'lam tiep'. "
        "Truoc moi hanh dong vat ly van phai quan sat robot/camera moi."
    )


class TaskMemoryStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._lock = threading.Lock()
        self._database_url = database_url
        self._conn: DbConnection | None = None

    def _db(self) -> DbConnection:
        conn, _ = get_connection(self._database_url)
        self._conn = conn
        return conn

    def reset(self) -> None:
        with self._lock:
            conn = self._db()
            conn.execute("DELETE FROM session_task_memory")
            conn.commit()

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db().execute(
                "SELECT memory_json FROM session_task_memory WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _load_json(row["memory_json"] if row else None)

    def save(self, session_id: str, memory: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        memory = {**empty_task_memory(), **memory, "last_updated_at": now}
        raw = json.dumps(memory, ensure_ascii=False, default=str)
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                INSERT INTO session_task_memory (session_id, memory_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    memory_json = excluded.memory_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, raw, now),
            )
            conn.commit()
        return memory

    def record_run(
        self,
        session_id: str,
        *,
        run_id: str,
        user_message: str,
        assistant_reply: str,
        status: str,
        agent_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        memory = self.get(session_id)
        structured = extract_task_update(
            run_id=run_id,
            user_message=user_message,
            status=status,
            agent_state=agent_state or {},
        )
        event = {
            "run_id": run_id,
            "user_message": user_message,
            "assistant_reply": assistant_reply,
            "status": status,
            "subgoals": structured["subgoals"],
            "recorded_at": utc_now_iso(),
        }
        memory["last_run_id"] = run_id
        if status == "CANCELLED":
            memory["active_goal"] = None
        else:
            memory["active_goal"] = user_message.strip() or memory.get("active_goal")
        memory["recent_events"] = [*(memory.get("recent_events") or []), event][-MAX_RECENT_EVENTS:]
        memory["remaining_tasks"] = [] if status == "CANCELLED" else structured["remaining_tasks"]
        if structured["last_failed_action"] is not None:
            memory["last_failed_action"] = structured["last_failed_action"]

        if status == "COMPLETED":
            memory["completed_tasks"] = [
                *(memory.get("completed_tasks") or []),
                {"task": user_message, "run_id": run_id, "completed_at": event["recorded_at"]},
            ][-MAX_COMPLETED_TASKS:]
            memory["completed_subgoals"] = [
                *(memory.get("completed_subgoals") or []),
                *structured["completed_subgoals"],
            ][-MAX_SUBGOALS:]
        elif status == "CANCELLED":
            memory["interrupted_tasks"] = [
                *(memory.get("interrupted_tasks") or []),
                {
                    "task": user_message,
                    "run_id": run_id,
                    "status": status,
                    "interrupted_at": event["recorded_at"],
                    "reason": "user_cancelled",
                },
            ][-MAX_FAILED_TASKS:]
        elif status in {"FAILED", "TIMEOUT"}:
            memory["failed_tasks"] = [
                *(memory.get("failed_tasks") or []),
                {"task": user_message, "run_id": run_id, "status": status, "failed_at": event["recorded_at"]},
            ][-MAX_FAILED_TASKS:]
        memory["tool_calls"] = [
            *(memory.get("tool_calls") or []),
            *structured["tool_calls"],
        ][-MAX_TOOL_CALLS:]
        return self.save(session_id, memory)


def _take_action_text(arguments: dict[str, Any]) -> str:
    return str(arguments.get("subgoal") or arguments.get("instruction") or arguments.get("task") or "").strip()


def extract_task_update(*, run_id: str, user_message: str, status: str, agent_state: dict[str, Any]) -> dict[str, Any]:
    records = agent_state.get("tool_call_history") or []
    valid_records = [r for r in records if isinstance(r, dict)]
    take_actions = [r for r in valid_records if r.get("tool_name") == "take_action"]
    tool_calls = [
        {
            "tool_name": r.get("tool_name") or "unknown_tool",
            "status": r.get("status") or "UNKNOWN",
            "error_code": r.get("error_code"),
            "run_id": run_id,
        }
        for r in valid_records
    ]
    subgoals = [text for text in (_take_action_text(r.get("arguments") or {}) for r in take_actions) if text]
    failed_records = [r for r in take_actions if r.get("status") in {"FAILED", "TIMEOUT", "CANCELLED"}]

    last_failed_action = None
    if failed_records:
        failed = failed_records[-1]
        last_failed_action = {
            "subgoal": _take_action_text(failed.get("arguments") or {}) or user_message,
            "run_id": run_id,
            "status": failed.get("status"),
            "error_code": failed.get("error_code"),
        }
    elif status in {"FAILED", "TIMEOUT", "CANCELLED"}:
        error = agent_state.get("error")
        last_failed_action = {
            "subgoal": subgoals[-1] if subgoals else user_message,
            "run_id": run_id,
            "status": status,
            "error_code": error.get("code") if isinstance(error, dict) else None,
        }

    completed_subgoals = []
    if status == "COMPLETED":
        completed_subgoals = [{"subgoal": s, "run_id": run_id, "status": "COMPLETED"} for s in subgoals]

    remaining_tasks = []
    if status in {"FAILED", "TIMEOUT"}:
        remaining_tasks.append({"task": user_message, "run_id": run_id, "reason": "run_not_completed"})

    return {
        "subgoals": subgoals,
        "completed_subgoals": completed_subgoals,
        "remaining_tasks": remaining_tasks,
        "last_failed_action": last_failed_action,
        "tool_calls": tool_calls,
    }


task_memory_store = TaskMemoryStore()
