"""Chiến lược gọi LLM — theo DOC 1 §2.9.

- Model mặc định OPENAI_MODEL (vision + tool calling); fallback khi 429/503 quá 2 lần.
- Retry theo từng loại lỗi OpenAI; stream token và kiểm tra cancel giữa các chunk.
- Ghi nhận usage/chi phí vào RunContext.
- OpenRouter free models: non-stream + auto model fallback trên 400 tool-call errors
  (Nvidia free VL thường fail JSON tool args).
"""

from __future__ import annotations

import asyncio
import logging
import random

import openai
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from services.llm import get_llm

from .context import RunContext
from .context_builder import model_supports_vision, strip_all_images
from .errors import AppError, RunCancelledError

logger = logging.getLogger("agent.llm")


def _chunk_to_message(chunk: AIMessageChunk) -> AIMessage:
    return AIMessage(
        content=chunk.content,
        tool_calls=chunk.tool_calls or [],
        invalid_tool_calls=chunk.invalid_tool_calls or [],
        additional_kwargs=chunk.additional_kwargs,
        response_metadata=chunk.response_metadata,
        usage_metadata=chunk.usage_metadata,
    )


def _is_free_model(model: str) -> bool:
    return model.endswith(":free") or model == "openrouter/free"


def _prefer_stream(settings, model: str) -> bool:
    """Free OpenRouter providers and Gemini often break streaming tool-call JSON."""
    if settings.llm_provider == "gemini":
        return False
    # OpenAI provider routed through Google's OpenAI-compatible endpoint
    if settings.llm_provider == "openai" and "gemini" in model.lower():
        return False
    if settings.llm_provider == "openrouter" and _is_free_model(model):
        return False
    return True


async def _stream_once(
    ctx: RunContext, llm, messages: list[BaseMessage], *, message_id: str
) -> AIMessage:
    """Stream 1 lần gọi LLM, emit token.delta, kiểm tra cancel giữa các chunk (§2.8)."""
    acc: AIMessageChunk | None = None
    async for chunk in llm.astream(messages):
        if ctx.cancel_event.is_set():
            partial = acc.text() if acc is not None else None
            raise RunCancelledError(partial_content=partial)
        acc = chunk if acc is None else acc + chunk
        if chunk.content:
            ctx.emitter.emit(
                "token.delta",
                {"run_id": ctx.run_id, "message_id": message_id, "delta": chunk.text},
            )
    if acc is None:
        raise AppError("AGENT_004", detail={"reason": "LLM không trả về nội dung"})

    message = _chunk_to_message(acc)
    if message.response_metadata.get("finish_reason") == "content_filter":
        raise AppError("AGENT_030")
    return message


async def _complete_once(
    ctx: RunContext, llm, messages: list[BaseMessage], *, message_id: str
) -> AIMessage:
    """Non-stream call — ổn định hơn stream tool-calling với free providers."""
    if ctx.cancel_event.is_set():
        raise RunCancelledError()
    result = await llm.ainvoke(messages)
    if isinstance(result, AIMessage):
        message = result
    else:
        message = AIMessage(content=str(result.content))
    if message.response_metadata.get("finish_reason") == "content_filter":
        raise AppError("AGENT_030")
    if message.content:
        ctx.emitter.emit(
            "token.delta",
            {"run_id": ctx.run_id, "message_id": message_id, "delta": message.text},
        )
    return message


async def _call_model_once(
    ctx: RunContext,
    llm,
    messages: list[BaseMessage],
    *,
    prefer_stream: bool,
    message_id: str,
) -> AIMessage:
    if prefer_stream:
        try:
            return await _stream_once(ctx, llm, messages, message_id=message_id)
        except openai.BadRequestError as exc:
            logger.warning(
                "LLM stream BadRequest — retry non-stream: %s", exc
            )
    return await _complete_once(ctx, llm, messages, message_id=message_id)


async def invoke_llm(
    ctx: RunContext,
    messages: list[BaseMessage],
    tools: list | None = None,
) -> AIMessage:
    """Gọi LLM với retry/fallback theo bảng xử lý lỗi DOC 1 §2.9."""
    if ctx.llm_invoke is not None:  # hook cho test
        return await ctx.llm_invoke(messages, tools or [])

    # Một `message_id` ổn định cho suốt lượt gọi này (kể cả khi retry/fallback
    # bên trong vòng lặp while bên dưới) — FE dùng để gộp các `token.delta`
    # về đúng một khối text (`stores/index.ts` B3).
    message_id = ctx.next_message_id()

    settings = ctx.settings
    model = settings.resolve_model()
    fallback = settings.resolve_model(settings.model_fallback)
    timeout_s = float(settings.llm_timeout_s)
    conn_retries = 0
    timeout_retries = 0
    rate_limit_hits = 0
    bad_request_switched = False

    import time
    import uuid

    while True:
        llm = get_llm(model=model, timeout=timeout_s, max_tokens=settings.llm_max_tokens)
        bound = llm.bind_tools(tools) if tools else llm
        prefer_stream = _prefer_stream(settings, model)
        # Free text models reject image_url parts → strip trước khi gửi.
        # Also: Google's OpenAI-compatible endpoint rejects image_url in
        # ToolMessages, so when provider=openai but model contains "gemini"
        # we must strip images too.
        _openai_via_google = (
            settings.llm_provider == "openai"
            and "gemini" in model.lower()
        )
        call_messages = (
            messages
            if model_supports_vision(model) and not _openai_via_google
            else strip_all_images(messages)
        )

        call_id = f"llm_{uuid.uuid4().hex[:12]}"
        is_fallback = model != settings.resolve_model()
        attempt = conn_retries + timeout_retries + rate_limit_hits + (1 if bad_request_switched else 0) + 1

        ctx.emitter.emit("llm.started", {
            "run_id": ctx.run_id,
            "call_id": call_id,
            "provider": settings.llm_provider,
            "model": model,
            "attempt": attempt,
            "is_fallback": is_fallback
        })
        ctx.verbose.llm_start(model, settings.llm_provider, len(call_messages), len(tools or []), attempt, is_fallback)

        def _emit_failed(exc):
            dur = int((time.perf_counter() - start_t) * 1000)
            ctx.emitter.emit("llm.failed", {
                "run_id": ctx.run_id,
                "call_id": call_id,
                "duration_ms": dur,
                "provider": settings.llm_provider,
                "model": model,
                "attempt": attempt,
                "is_fallback": is_fallback,
                "error": {"type": type(exc).__name__, "code": getattr(exc, "status_code", "UNKNOWN")}
            })
            ctx.verbose.llm_failed(model, {"type": type(exc).__name__, "message": str(exc)}, attempt, is_fallback)
            return dur

        start_t = time.perf_counter()

        try:
            message = await _call_model_once(
                ctx, bound, call_messages, prefer_stream=prefer_stream, message_id=message_id
            )
        except openai.AuthenticationError as exc:
            _emit_failed(exc)
            raise AppError("SYS_001", detail={"provider": settings.llm_provider}) from exc
        except openai.BadRequestError as exc:
            _emit_failed(exc)
            # Nvidia free VL + tools → 400 JSON delimiter. Thử fallback model.
            logger.error("AGENT_004 BadRequest model=%s: %s", model, exc)
            if not bad_request_switched and model != fallback:
                logger.warning(
                    "AGENT_004: model=%s rejected request → fallback=%s", model, fallback
                )
                model = fallback
                ctx.degraded = True
                bad_request_switched = True
                continue
            raise AppError(
                "AGENT_004",
                detail={
                    "provider_error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", 400),
                    "model": model,
                    "hint": (
                        "Model free không hỗ trợ tool calling ổn định. "
                        "Đổi MODEL_NAME (vd openai/gpt-oss-20b:free) hoặc dùng OpenAI paid."
                    ),
                },
            ) from exc
        except openai.RateLimitError as exc:
            _emit_failed(exc)
            rate_limit_hits += 1
            if rate_limit_hits > 2:
                if model != fallback:
                    logger.warning(
                        "AGENT_002: rate-limit model=%s → fallback=%s", model, fallback
                    )
                    model = fallback
                    ctx.degraded = True
                    rate_limit_hits = 0
                    continue
                raise AppError(
                    "AGENT_002",
                    detail={
                        "model": model,
                        "hint": "Free model pool bị rate-limit. Đợi vài phút hoặc đổi MODEL_NAME.",
                    },
                ) from exc
            retry_after = 1.0
            response = getattr(exc, "response", None)
            if response is not None:
                retry_after = float(response.headers.get("retry-after") or 1.0)
            await asyncio.sleep(retry_after + random.random() * 0.5)
        except openai.APIStatusError as exc:
            _emit_failed(exc)
            if exc.status_code == 402:
                raise AppError(
                    "AGENT_002",
                    detail={
                        "provider": settings.llm_provider,
                        "model": model,
                        "status_code": 402,
                        "hint": (
                            "OpenRouter: insufficient credits. "
                            "Dùng MODEL_NAME / MODEL_FALLBACK kết thúc bằng :free, "
                            "hoặc nạp credit tại https://openrouter.ai/settings/credits"
                        ),
                    },
                    message=(
                        "Tài khoản LLM thiếu credit. Với OpenRouter free tier, "
                        "chọn model :free cho cả primary và fallback."
                    ),
                ) from exc
            # 400 đôi khi bọc trong APIStatusError (OpenRouter wrapper)
            # 404 OpenRouter: model không nhận image — strip ảnh + retry, hoặc fallback
            if exc.status_code == 404 and "image" in str(exc).lower():
                stripped = strip_all_images(messages)
                # list equality may fail on message objects → always strip once
                logger.warning("model=%s không nhận image — strip ảnh và retry", model)
                messages = stripped
                # avoid infinite loop: only one strip retry then fallback
                if not bad_request_switched:
                    bad_request_switched = True  # reuse flag as "already retried strip"
                    # mark we only strip once by comparing if next 404 after strip
                    continue
                if model != fallback:
                    model = fallback
                    ctx.degraded = True
                    continue
            if exc.status_code == 400 and not bad_request_switched and model != fallback:
                logger.warning(
                    "status 400 model=%s → fallback=%s (details redacted)",
                    model,
                    fallback,
                )
                model = fallback
                ctx.degraded = True
                bad_request_switched = True
                continue
            raise AppError(
                "AGENT_004",
                detail={
                    "status_code": exc.status_code,
                    "provider_error_type": type(exc).__name__,
                },
            ) from exc
        except openai.APITimeoutError as exc:
            _emit_failed(exc)
            timeout_retries += 1
            if timeout_retries > 1:
                raise AppError("AGENT_003", detail={"timeout_s": timeout_s}) from exc
            timeout_s *= 1.5
        except openai.APIConnectionError as exc:
            _emit_failed(exc)
            conn_retries += 1
            if conn_retries > 2:
                raise AppError("AGENT_001") from exc
            await asyncio.sleep([1.0, 4.0][conn_retries - 1] + random.random() * 0.5)
        except AppError:
            raise
        except Exception as exc:
            # Catch non-OpenAI SDK errors (e.g. ChatGoogleGenerativeAIError
            # from langchain-google-genai, google.genai.errors.ClientError).
            # These are thrown when LLM_PROVIDER=gemini.
            _emit_failed(exc)
            err_str = str(exc).lower()
            if "unauthenticated" in err_str or "401" in err_str or "api key" in err_str:
                raise AppError("SYS_001", detail={"provider": settings.llm_provider}) from exc

            # Capacity / quota errors — retry then fallback. Do NOT map to AGENT_004
            # ("invalid request"); that freezes the UI with a misleading message.
            capacity = any(
                token in err_str
                for token in (
                    "503",
                    "unavailable",
                    "high demand",
                    "resource_exhausted",
                    "resource exhausted",
                    "429",
                    "rate limit",
                    "quota",
                    "overloaded",
                )
            )
            if capacity:
                rate_limit_hits += 1
                logger.warning(
                    "LLM capacity error model=%s attempt=%s: %s",
                    model,
                    rate_limit_hits,
                    str(exc)[:200],
                )
                if rate_limit_hits <= 2:
                    await asyncio.sleep(min(2.0 * rate_limit_hits, 6.0) + random.random())
                    continue
                if model != fallback:
                    logger.warning(
                        "AGENT_002: capacity model=%s → fallback=%s", model, fallback
                    )
                    model = fallback
                    ctx.degraded = True
                    rate_limit_hits = 0
                    continue
                raise AppError(
                    "AGENT_002",
                    detail={
                        "model": model,
                        "provider_error_type": type(exc).__name__,
                        "hint": str(exc)[:200],
                    },
                ) from exc

            logger.error("LLM error (non-openai) model=%s: %s", model, exc)
            raise AppError(
                "AGENT_004",
                detail={
                    "provider_error_type": type(exc).__name__,
                    "model": model,
                    "hint": str(exc)[:200],
                },
            ) from exc
        else:
            dur = int((time.perf_counter() - start_t) * 1000)
            image_count = sum(
                1
                for m in messages
                if isinstance(m.content, list)
                for part in m.content
                if isinstance(part, dict) and part.get("type") == "image_url"
            )
            ctx.record_usage(message.usage_metadata, model, image_count)

            usage = message.usage_metadata
            ctx.emitter.emit("llm.completed", {
                "run_id": ctx.run_id,
                "call_id": call_id,
                "duration_ms": dur,
                "provider": settings.llm_provider,
                "model": model,
                "attempt": attempt,
                "is_fallback": is_fallback,
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0)
                } if usage else None
            })

            finish_reason = "tool_calls" if getattr(message, "tool_calls", None) else "stop"
            ctx.verbose.llm_end(finish_reason)

            # Gắn `message_id` để `agent_node` phát `message.completed` cùng id
            # với các `token.delta` đã emit cho lượt gọi này (A4).
            message.additional_kwargs = {**message.additional_kwargs, "message_id": message_id}
            return message


async def summarize_messages(ctx: RunContext, text: str) -> str:
    """Tóm tắt lịch sử hội thoại bằng model rẻ (DOC 1 §2.5, §2.9)."""
    llm = get_llm(model=ctx.settings.model_fallback, timeout=30.0, max_tokens=512)
    result = await llm.ainvoke(
        [
            (
                "system",
                "Tóm tắt hội thoại điều khiển robot sau đây trong tối đa 10 gạch đầu dòng, "
                "giữ lại: nhiệm vụ đã yêu cầu, hành động đã thực hiện, kết quả, lỗi nếu có.",
            ),
            ("user", text),
        ]
    )
    return str(result.content)
