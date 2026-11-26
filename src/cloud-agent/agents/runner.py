"""Entrypoint chạy một run của agent.

Production: agent-worker consume Redis Stream runs:queue rồi gọi hàm này.
Skeleton: API /chat gọi trực tiếp với MockEdgeTransport.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime

from langchain_core.messages import BaseMessage, HumanMessage
from models.schemas import RunDetail, RunStatus
from observability.langfuse import get_langfuse_callbacks

from .context import RunContext
from .events import LoggingEmitter
from .graph import agent
from .tools.http_transport import HttpMcpTransport
from .tools.mcp_client import McpClient
from .tools.mock_transport import MockEdgeTransport
from .trace_repo import trace_repository

_RECURSION_LIMIT = 60  # đủ cho AGENT_MAX_ITERATIONS=12 vòng (mỗi vòng ~4 node)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _langsmith_run_config(ctx: RunContext) -> dict:
    """Return LangChain RunnableConfig fields used by LangSmith tracing.

    Keep this metadata operational only. Do not include user prompts, raw model
    messages, chain-of-thought, tool outputs, or credentials here.
    """
    settings = ctx.settings
    return {
        "run_name": "p172-agent-run",
        "tags": [
            "p172",
            f"env:{settings.app_env}",
            f"provider:{settings.llm_provider}",
            f"source:{ctx.source}",
            f"robot:{ctx.robot_id}",
        ],
        "metadata": {
            "run_id": ctx.run_id,
            "trace_id": ctx.trace_id,
            "session_id": ctx.session_id,
            "robot_id": ctx.robot_id,
            "user_id": ctx.user_id,
            "source": ctx.source,
            "llm_provider": settings.llm_provider,
            "model": settings.resolve_model(),
        },
    }


def _agent_run_config(ctx: RunContext) -> dict:
    config = {
        **_langsmith_run_config(ctx),
        "configurable": {"run_ctx": ctx, "thread_id": ctx.run_id},
        "recursion_limit": _RECURSION_LIMIT,
    }
    langfuse_callbacks = get_langfuse_callbacks(ctx)
    if langfuse_callbacks:
        config["callbacks"] = [
            *config.get("callbacks", []),
            *langfuse_callbacks,
        ]
    return config


def build_default_context(
    robot_id: str = "robot_mock",
    session_id: str | None = None,
    user_id: str = "user_dev",
    source: str = "CHAT",
    run_id: str | None = None,
    trace_id: str | None = None,
) -> RunContext:
    """Build RunContext with real MCP transport when configured, else mock.

    Callers that already minted a console `runs` row (e.g. the scheduler) must
    pass that same `run_id` / `trace_id` so trace_runs and Nhật ký share one id.
    """
    from config import get_settings

    settings = get_settings()
    transport = (
        HttpMcpTransport(settings)
        if settings.mcp_endpoint_url and settings.app_env != "test"
        else MockEdgeTransport()
    )
    resolved_run_id = run_id or _new_id("run")
    if trace_id:
        resolved_trace_id = trace_id
    elif resolved_run_id.startswith("run_"):
        resolved_trace_id = resolved_run_id.replace("run_", "trc_", 1)
    else:
        resolved_trace_id = _new_id("trc")
    return RunContext(
        run_id=resolved_run_id,
        session_id=session_id or _new_id("ses"),
        user_id=user_id,
        robot_id=robot_id,
        trace_id=resolved_trace_id,
        source=source,
        mcp=McpClient(transport),
        emitter=LoggingEmitter(run_id=resolved_run_id, trace_id=resolved_trace_id),
        settings=settings,
        episode_poll_interval_s=2.0 if settings.mcp_endpoint_url else 0.2,
    )


async def run_agent(
    user_message: str,
    ctx: RunContext | None = None,
    history: list[BaseMessage] | None = None,
    initial_overrides: dict | None = None,
) -> dict:
    """Chạy một run hoàn chỉnh, trả về AgentState cuối cùng."""
    ctx = ctx or build_default_context()
    initial_state = {
        "run_id": ctx.run_id,
        "session_id": ctx.session_id,
        "user_id": ctx.user_id,
        "robot_id": ctx.robot_id,
        "trace_id": ctx.trace_id,
        "source": ctx.source,
        "messages": [*(history or []), HumanMessage(user_message)],
    }
    if initial_overrides:
        initial_state.update(initial_overrides)

    # Create the run in repo
    start_time = datetime.now(UTC)
    perf_start = time.perf_counter()
    run_detail = RunDetail(
        id=ctx.run_id,
        trace_id=ctx.trace_id,
        session_id=ctx.session_id,
        robot_id=ctx.robot_id,
        user_id=ctx.user_id,
        source=ctx.source,
        status=RunStatus.RUNNING,
        input_text=user_message,
        created_at=start_time.isoformat(timespec="milliseconds")
    )

    # Let create_run exception bubble up to return 500
    trace_repository.create_run(run_detail)

    from .guardrails import POLITICAL_DEFLECTION_MESSAGE, is_political_topic
    if is_political_topic(user_message):
        end_time = datetime.now(UTC)
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        from langchain_core.messages import AIMessage
        initial_state["messages"].append(AIMessage(content=POLITICAL_DEFLECTION_MESSAGE))
        initial_state["final_status"] = RunStatus.COMPLETED

        trace_repository.update_run(ctx.run_id, {
            "status": RunStatus.COMPLETED,
            "completed_at": end_time.isoformat(timespec="milliseconds"),
            "duration_ms": duration_ms,
            "final_response": POLITICAL_DEFLECTION_MESSAGE,
        })
        return initial_state

    try:
        result = await agent.ainvoke(
            initial_state,
            config=_agent_run_config(ctx),
        )
    except asyncio.CancelledError:
        end_time = datetime.now(UTC)
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        trace_repository.update_run(ctx.run_id, {
            "status": RunStatus.CANCELLED,
            "completed_at": end_time.isoformat(timespec="milliseconds"),
            "duration_ms": duration_ms,
        })
        raise
    except TimeoutError:
        end_time = datetime.now(UTC)
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        trace_repository.update_run(ctx.run_id, {
            "status": RunStatus.TIMEOUT,
            "completed_at": end_time.isoformat(timespec="milliseconds"),
            "duration_ms": duration_ms,
            "error": {
                "code": "AGENT_TIMEOUT",
                "message": "Agent run timed out.",
            },
        })
        raise
    except Exception as e:
        end_time = datetime.now(UTC)
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        err_obj = e.to_object() if hasattr(e, "to_object") else {"message": str(e)}
        trace_repository.update_run(ctx.run_id, {
            "status": RunStatus.FAILED,
            "completed_at": end_time.isoformat(timespec="milliseconds"),
            "duration_ms": duration_ms,
            "error": err_obj
        })
        raise

    # Finalize run
    end_time = datetime.now(UTC)
    duration_ms = int((time.perf_counter() - perf_start) * 1000)
    raw_final_status = result.get("final_status") or RunStatus.COMPLETED
    try:
        final_status = RunStatus(raw_final_status)
    except (TypeError, ValueError) as exc:
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        trace_repository.update_run(ctx.run_id, {
            "status": RunStatus.FAILED,
            "completed_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "duration_ms": duration_ms,
            "error": {
                "code": "INVALID_RUN_STATUS",
                "message": "Agent returned an invalid final status.",
            },
        })
        raise ValueError("Agent returned an invalid final status") from exc
    error = result.get("error")

    # Get final response text (extract from messages)
    from langchain_core.messages import AIMessage
    final_text = ""
    final_msg_idx = -1
    for i, message in reversed(list(enumerate(result.get("messages", [])))):
        if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            final_text = message.text if isinstance(message.text, str) else str(message.content)
            final_msg_idx = i
            break

    from .guardrails import has_mixed_language, rewrite_mixed_language
    if final_text and has_mixed_language(final_text):
        final_text = await rewrite_mixed_language(final_text, ctx)
        if final_msg_idx >= 0:
            result["messages"][final_msg_idx] = AIMessage(content=final_text)

    updates = {
        "status": final_status,
        "completed_at": end_time.isoformat(timespec="milliseconds"),
        "duration_ms": duration_ms,
        "final_response": final_text,
    }
    if error:
        updates["error"] = error
    if "outcome" in result:
        updates["outcome"] = result["outcome"]

    trace_repository.update_run(ctx.run_id, updates)

    return result
