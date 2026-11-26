import re
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated

from agents.errors import AppError
from agents.run_control import register_run, unregister_run
from agents.runner import build_default_context, run_agent
from agents.sanitizer import sanitize_payload
from agents.tools.http_transport import HttpMcpTransport
from agents.tools.mcp_client import McpClient
from agents.trace_repo import trace_repository
from auth.deps import require_user_if_configured
from auth.store import User
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from models.schemas import ChatRequest, ChatResponse, ChatStartResponse, RunDetail, RunSource, RunStatus, TraceEvent
from services.memory import long_term_memory_service
from services.robot_status import is_robot_busy, mark_busy, release_if_idle
from stores.robots import robot_store
from stores.runs import run_store
from stores.sessions import session_store
from stores.task_memory import task_memory_store

from api.common import api_error

router = APIRouter()

_TASK_ID_RE = re.compile(r"\btask_id\s*(?:=|:)?\s*(\d+)\b", re.IGNORECASE)
_BUSY_CHAT = ("ROBOT_010", "Robot đang thực hiện nhiệm vụ khác.", 409)


def _reject_busy_robot(robot_id: str) -> None:
    if is_robot_busy(robot_id):
        raise api_error(*_BUSY_CHAT)


def _final_text(result: dict) -> str:
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return message.text if isinstance(message.text, str) else str(message.content)
    return ""


def _error_text(error: object) -> str:
    if isinstance(error, dict):
        code = error.get("code", "")
        if isinstance(code, str) and (code.startswith("AGENT_") or code.startswith("MCP_") or code.startswith("SYS_")):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return ""


def _assistant_reply(result: dict, status: str) -> str:
    """User-facing bubble text; never persist a raw terminal enum as the reply."""
    text = (_final_text(result) or "").strip()
    if text and text not in {"FAILED", "TIMEOUT", "CANCELLED", "COMPLETED"}:
        return text
    err = _error_text(result.get("error"))
    if err:
        return err
    if status == "TIMEOUT":
        return "Agent chạy quá thời gian chờ."
    if status == "CANCELLED":
        return "Đã huỷ."
    if status == "FAILED":
        return "Không thể hoàn thành yêu cầu."
    return text or status


def _duration_s(duration_ms: int) -> int:
    return max(0, round(duration_ms / 1000))


def _has_tool_calls(result: dict) -> bool:
    """True nếu lượt chạy có gọi tool NÀO đó — chạm tới robot thật hoặc đọc
    trạng thái/camera của nó. False nghĩa là agent chỉ trả lời bằng lời
    (chào hỏi, đổi tên xưng hô, trả lời một câu hỏi chung...): §P1.2 gọi đây
    là `no_action_taken`, và `MessageStatusLine` ở frontend dựa đúng vào giá
    trị này để ẩn dòng "Thực thi: Hoàn thành" — hiện nó cho một câu chat
    thuần đọc như đang xác nhận robot vừa làm được gì đó, trong khi cánh tay
    không hề nhúc nhích. """
    for message in result.get("messages", []):
        if isinstance(message, ToolMessage):
            return True
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            return True
    return False


def _require_owned_robot(robot_id: str) -> None:
    if not robot_store.exists(robot_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "SCHED_004", "message": "Robot đã chọn không còn tồn tại."},
        )


def _session_history(session_id: str, *, user_id: str) -> list[HumanMessage | AIMessage]:
    rows = session_store.list_messages(session_id, user_id=user_id) or []
    history: list[HumanMessage | AIMessage] = []
    for row in rows:
        role = row.get("role")
        content = row.get("content") or ""
        if role == "user":
            history.append(HumanMessage(content=content))
        elif role == "assistant":
            history.append(AIMessage(content=content))
    return history


def _finish_console_run(
    run_id: str,
    session_id: str,
    *,
    status: RunStatus | str,
    duration_ms: int,
    error: dict | None = None,
    outcome: str | None = None,
    robot_id: str | None = None,
) -> None:
    session_store.set_active_run(session_id, None)
    run_store.finish(
        run_id,
        status=str(status),
        duration_s=_duration_s(duration_ms),
        error=error,
        outcome=outcome,
    )
    rid = robot_id
    if rid is None:
        row = run_store.get(run_id)
        if row:
            rid = row.get("robot_id")
    if rid:
        release_if_idle(rid)


async def _retrieve_long_term_memories(user_id: str, message: str, history: list[HumanMessage | AIMessage] | None) -> list[dict]:
    recent = "\n".join(str(item.content) for item in (history or [])[-4:])
    query = f"{recent}\n{message}".strip()
    try:
        return await long_term_memory_service.retrieve_memories(user_id=user_id, query=query)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("long-term memory retrieval skipped", exc_info=True)
        return []


async def _record_long_term_memory_safe(
    *,
    user_id: str,
    session_id: str,
    run_id: str,
    user_message: str,
    assistant_reply: str,
    task_memory: dict | None,
) -> None:
    await long_term_memory_service.record_run_memory(
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        user_message=user_message,
        assistant_reply=assistant_reply,
        task_memory=task_memory,
    )


async def _execute_chat_context(
    *,
    request: ChatRequest,
    session: dict,
    ctx,
    user_id: str,
    history: list[HumanMessage | AIMessage] | None = None,
) -> ChatResponse:

    from config import get_settings

    settings = get_settings()
    robot_id = session["robot_id"]

    if settings.app_env == "development" and settings.mcp_endpoint_url:
        task_match = _TASK_ID_RE.search(request.message)
        if task_match:
            task_id = int(task_match.group(1))
            run_id = f"run_dev_mcp_{uuid.uuid4().hex[:12]}"
            trace_id = f"trc_dev_{uuid.uuid4().hex[:12]}"
            start_time = datetime.now(UTC)
            perf_start = time.perf_counter()

            trace_repository.create_run(RunDetail(
                id=run_id,
                trace_id=trace_id,
                session_id=session["id"],
                robot_id=robot_id,
                user_id=user_id,
                source=RunSource.CHAT,
                status=RunStatus.RUNNING,
                input_text=request.message,
                created_at=start_time.isoformat(timespec="milliseconds"),
            ))
            session_store.append_message(
                session["id"],
                role="user",
                content=request.message,
                run_id=run_id,
            )
            run_store.start(
                run_id=run_id,
                user_id=user_id,
                session_id=session["id"],
                robot_id=session["robot_id"],
                source="CHAT",
                input_text=request.message,
                trace_id=trace_id,
            )
            mark_busy(session["robot_id"])
            trace_repository.add_event(TraceEvent(
                id=f"evt_{task_id}_start",
                run_id=run_id,
                trace_id=trace_id,
                sequence=0,
                timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
                type="tool.started",
                tool_name="take_action",
                arguments={"task_id": task_id},
            ))

            try:
                mcp = McpClient(HttpMcpTransport(settings))
                result = await mcp.call_tool(
                    robot_id=robot_id,
                    tool_name="take_action",
                    arguments={"task_id": task_id},
                    trace_id=trace_id,
                    timeout_s=15.0,
                )
            except AppError as exc:
                duration_ms = int((time.perf_counter() - perf_start) * 1000)
                error = sanitize_payload(exc.to_object())
                end_time = datetime.now(UTC)
                trace_repository.add_event(TraceEvent(
                    id=f"evt_{task_id}_fail",
                    run_id=run_id,
                    trace_id=trace_id,
                    sequence=0,
                    timestamp=end_time.isoformat(timespec="milliseconds"),
                    type="tool.failed",
                    tool_name="take_action",
                    error=error,
                    duration_ms=duration_ms,
                ))
                trace_repository.update_run(run_id, {
                    "status": RunStatus.FAILED,
                    "completed_at": end_time.isoformat(timespec="milliseconds"),
                    "duration_ms": duration_ms,
                    "error": error,
                })
                session_store.append_message(session["id"], role="assistant", content=_assistant_reply({"error": error}, "FAILED"), run_id=run_id)
                _finish_console_run(
                    run_id,
                    session["id"],
                    status=RunStatus.FAILED,
                    duration_ms=duration_ms,
                    error=error,
                    robot_id=session["robot_id"],
                )
                return ChatResponse(
                    response="",
                    run_id=run_id,
                    trace_id=trace_id,
                    status=RunStatus.FAILED,
                    error=error,
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                duration_ms = int((time.perf_counter() - perf_start) * 1000)
                error = sanitize_payload({"message": str(exc)})
                end_time = datetime.now(UTC)
                trace_repository.add_event(TraceEvent(
                    id=f"evt_{task_id}_fail",
                    run_id=run_id,
                    trace_id=trace_id,
                    sequence=0,
                    timestamp=end_time.isoformat(timespec="milliseconds"),
                    type="tool.failed",
                    tool_name="take_action",
                    error=error,
                    duration_ms=duration_ms,
                ))
                trace_repository.update_run(run_id, {
                    "status": RunStatus.FAILED,
                    "completed_at": end_time.isoformat(timespec="milliseconds"),
                    "duration_ms": duration_ms,
                    "error": error,
                })
                session_store.append_message(session["id"], role="assistant", content=_assistant_reply({"error": error}, "FAILED"), run_id=run_id)
                _finish_console_run(
                    run_id,
                    session["id"],
                    status=RunStatus.FAILED,
                    duration_ms=duration_ms,
                    error=error,
                    robot_id=session["robot_id"],
                )
                return ChatResponse(
                    response="",
                    run_id=run_id,
                    trace_id=trace_id,
                    status=RunStatus.FAILED,
                    error=error,
                    duration_ms=duration_ms,
                )

            duration_ms = int((time.perf_counter() - perf_start) * 1000)
            end_time = datetime.now(UTC)
            result_error = None
            if result.ok:
                trace_repository.add_event(TraceEvent(
                    id=f"evt_{task_id}_done",
                    run_id=run_id,
                    trace_id=trace_id,
                    sequence=0,
                    timestamp=end_time.isoformat(timespec="milliseconds"),
                    type="tool.completed",
                    tool_name="take_action",
                    duration_ms=duration_ms,
                    result_summary={"ok": True},
                ))
            else:
                result_error = sanitize_payload(
                    result.error.model_dump() if result.error else {"message": "take_action failed"}
                )
                trace_repository.add_event(TraceEvent(
                    id=f"evt_{task_id}_fail",
                    run_id=run_id,
                    trace_id=trace_id,
                    sequence=0,
                    timestamp=end_time.isoformat(timespec="milliseconds"),
                    type="tool.failed",
                    tool_name="take_action",
                    duration_ms=duration_ms,
                    error=result_error,
                ))

            episode_id = result.data.get("episode_id")
            final_resp = (
                f"Đã gửi take_action tới MCP cho task_id {task_id}."
                + (f" Episode: {episode_id}." if episode_id else "")
                + " Mở viewer và bấm Connect để xem stream."
            )
            status = RunStatus.COMPLETED if result.ok else RunStatus.FAILED
            trace_repository.update_run(run_id, {
                "status": status,
                "completed_at": end_time.isoformat(timespec="milliseconds"),
                "duration_ms": duration_ms,
                "final_response": final_resp,
                "error": result_error,
            })
            session_store.append_message(session["id"], role="assistant", content=final_resp, run_id=run_id)
            _finish_console_run(
                run_id,
                session["id"],
                status=status,
                duration_ms=duration_ms,
                error=result_error,
                robot_id=session["robot_id"],
            )
            return ChatResponse(
                response=final_resp,
                run_id=run_id,
                trace_id=trace_id,
                status=status,
                duration_ms=duration_ms,
                error=result_error,
            )

    ctx.verbose.run_start(
        robot_id=robot_id,
        session_id=ctx.session_id,
        model=settings.resolve_model(),
    )

    perf_start = time.perf_counter()
    task_memory = task_memory_store.get(session["id"])
    long_term_memories = await _retrieve_long_term_memories(user_id, request.message, history)
    try:
        result = await run_agent(
            request.message,
            ctx=ctx,
            history=history,
            initial_overrides={
                "task_memory": task_memory,
                "long_term_memories": long_term_memories,
            },
        )
    except TimeoutError:
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        error = {"code": "AGENT_TIMEOUT", "message": "Agent run timed out."}
        ctx.verbose.run_failed(error)
        reply = _assistant_reply({"error": error}, "TIMEOUT")
        session_store.append_message(session["id"], role="assistant", content=reply, run_id=ctx.run_id)
        await _record_long_term_memory_safe(
            user_id=user_id,
            session_id=session["id"],
            run_id=ctx.run_id,
            user_message=request.message,
            assistant_reply=reply,
            task_memory=task_memory,
        )
        _finish_console_run(
            ctx.run_id,
            session["id"],
            status=RunStatus.TIMEOUT,
            duration_ms=duration_ms,
            error=error,
            robot_id=session["robot_id"],
        )
        if not trace_repository.get_run(ctx.run_id):
            raise HTTPException(status_code=500, detail="Run could not be created in repository.")
        return ChatResponse(
            response="",
            run_id=ctx.run_id,
            trace_id=ctx.trace_id,
            status=RunStatus.TIMEOUT,
            duration_ms=duration_ms,
            error=error,
        )
    except AppError as exc:
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        error = sanitize_payload(exc.to_object())
        ctx.verbose.run_failed(error)
        reply = _assistant_reply({"error": error}, "FAILED")
        session_store.append_message(session["id"], role="assistant", content=reply, run_id=ctx.run_id)
        await _record_long_term_memory_safe(
            user_id=user_id,
            session_id=session["id"],
            run_id=ctx.run_id,
            user_message=request.message,
            assistant_reply=reply,
            task_memory=task_memory,
        )
        _finish_console_run(
            ctx.run_id,
            session["id"],
            status=RunStatus.FAILED,
            duration_ms=duration_ms,
            error=error,
            robot_id=session["robot_id"],
        )
        if not trace_repository.get_run(ctx.run_id):
            raise HTTPException(status_code=500, detail="Run could not be created in repository.")
        return ChatResponse(
            response="",
            run_id=ctx.run_id,
            trace_id=ctx.trace_id,
            status=RunStatus.FAILED,
            duration_ms=duration_ms,
            error=error,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        error = sanitize_payload({"message": str(exc)})
        ctx.verbose.run_failed(error)
        reply = _assistant_reply({"error": error}, "FAILED")
        session_store.append_message(session["id"], role="assistant", content=reply, run_id=ctx.run_id)
        await _record_long_term_memory_safe(
            user_id=user_id,
            session_id=session["id"],
            run_id=ctx.run_id,
            user_message=request.message,
            assistant_reply=reply,
            task_memory=task_memory,
        )
        _finish_console_run(
            ctx.run_id,
            session["id"],
            status=RunStatus.FAILED,
            duration_ms=duration_ms,
            error=error,
            robot_id=session["robot_id"],
        )
        if not trace_repository.get_run(ctx.run_id):
            raise HTTPException(status_code=500, detail="Run could not be created in repository.") from exc
        return ChatResponse(
            response="",
            run_id=ctx.run_id,
            trace_id=ctx.trace_id,
            status=RunStatus.FAILED,
            duration_ms=duration_ms,
            error=error,
        )

    duration_ms = int((time.perf_counter() - perf_start) * 1000)
    status = RunStatus(result.get("final_status") or RunStatus.COMPLETED)
    reply = _assistant_reply(result, status)
    if status != RunStatus.COMPLETED:
        ctx.verbose.run_completed(status)
    else:
        ctx.verbose.run_completed(status)
    error = sanitize_payload(result.get("error")) if result.get("error") else None
    session_store.append_message(session["id"], role="assistant", content=reply, run_id=ctx.run_id)
    updated_task_memory = task_memory_store.record_run(
        session["id"],
        run_id=ctx.run_id,
        user_message=request.message,
        assistant_reply=reply,
        status=str(status),
        agent_state=result,
    )
    await _record_long_term_memory_safe(
        user_id=user_id,
        session_id=session["id"],
        run_id=ctx.run_id,
        user_message=request.message,
        assistant_reply=reply,
        task_memory=updated_task_memory,
    )
    _finish_console_run(
        ctx.run_id,
        session["id"],
        status=status,
        duration_ms=duration_ms,
        error=error,
        robot_id=session["robot_id"],
    )
    return ChatResponse(
        response=reply,
        run_id=ctx.run_id,
        trace_id=ctx.trace_id,
        status=status,
        duration_ms=duration_ms,
        error=error,
    )



async def _run_bg_task(
    request: ChatRequest,
    session: dict,
    ctx,
    user_id: str,
    history: list[HumanMessage | AIMessage] | None = None,
) -> None:
    try:
        await _execute_chat_context(
            request=request,
            session=session,
            ctx=ctx,
            user_id=user_id,
            history=history,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("background chat run failed")
    finally:
        await unregister_run(ctx.run_id)

@router.post("/chat/start", response_model=ChatStartResponse)
async def start_chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> ChatStartResponse:
    """Create a chat run and return run_id immediately so FE can subscribe to trace SSE."""
    user_id = user.id if user is not None else "user_dev"
    # Serialize turns on one robot (active run / BUSY), not sessions: another
    # chat may use the same sim after take_action finishes, but not during it.
    _reject_busy_robot(request.robot_id)
    session = session_store.get_or_create(
        request.session_id, request.robot_id, user_id=user_id
    )
    robot_id = session["robot_id"]
    history = _session_history(session["id"], user_id=user_id)
    ctx = build_default_context(
        robot_id=robot_id,
        session_id=session["id"],
        user_id=user_id,
    )
    await register_run(ctx)
    created_at = datetime.now(UTC).isoformat(timespec="milliseconds")
    trace_repository.create_run(RunDetail(
        id=ctx.run_id,
        trace_id=ctx.trace_id,
        session_id=session["id"],
        robot_id=robot_id,
        user_id=user_id,
        source=RunSource.CHAT,
        status=RunStatus.RUNNING,
        input_text=request.message,
        created_at=created_at,
    ))
    trace_repository.add_event(TraceEvent(
        id=f"evt_{uuid.uuid4().hex[:12]}",
        run_id=ctx.run_id,
        trace_id=ctx.trace_id,
        sequence=0,
        timestamp=created_at,
        type="run.started",
        status=RunStatus.RUNNING,
        metadata={"source": "CHAT"},
    ))
    session_store.append_message(
        session["id"],
        role="user",
        content=request.message,
        run_id=ctx.run_id,
    )
    run_store.start(
        run_id=ctx.run_id,
        user_id=user_id,
        session_id=session["id"],
        robot_id=session["robot_id"],
        source="CHAT",
        input_text=request.message,
        trace_id=ctx.trace_id,
    )
    background_tasks.add_task(
        _run_bg_task,
        request=request,
        session=session,
        ctx=ctx,
        user_id=user_id,
        history=history,
    )
    return ChatStartResponse(
        run_id=ctx.run_id,
        trace_id=ctx.trace_id,
        session_id=session["id"],
    )

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: Annotated[User | None, Depends(require_user_if_configured)],
) -> ChatResponse:
    user_id = user.id if user is not None else "user_dev"
    _reject_busy_robot(request.robot_id)
    session = session_store.get_or_create(
        request.session_id, request.robot_id, user_id=user_id
    )
    robot_id = session["robot_id"]
    history = _session_history(session["id"], user_id=user_id)
    ctx = build_default_context(
        robot_id=robot_id,
        session_id=session["id"],
        user_id=user_id,
    )
    session_store.append_message(
        session["id"],
        role="user",
        content=request.message,
        run_id=ctx.run_id,
    )
    run_store.start(
        run_id=ctx.run_id,
        user_id=user_id,
        session_id=session["id"],
        robot_id=session["robot_id"],
        source="CHAT",
        input_text=request.message,
        trace_id=ctx.trace_id,
    )
    return await _execute_chat_context(
        request=request,
        session=session,
        ctx=ctx,
        user_id=user_id,
        history=history,
    )

@router.get("/status")
async def agent_status():
    """Check agent and auth flags for the UI."""
    from config import get_settings

    settings = get_settings()
    return {
        "status": "ready",
        "agent": "VLM Cloud Agent (LangGraph)",
        "llm_provider": settings.llm_provider,
        "model": settings.resolve_model(),
        "fallback_model": settings.resolve_model(settings.model_fallback),
        "app_env": settings.app_env,
        "auth_required": settings.auth_required,
        "google_sso_configured": bool(
            settings.google_client_id and settings.google_client_secret
        ),
        "google_redirect_uri": settings.google_redirect_uri,
        "ui_base": settings.ui_base,
    }
