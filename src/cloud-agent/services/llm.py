"""LLM client factory — OpenAI, OpenRouter, or Google Gemini.

- OpenAI / OpenRouter: `langchain_openai.ChatOpenAI` (OpenAI-compatible).
- Gemini: `langchain_google_genai.ChatGoogleGenerativeAI` (install langchain-google-genai).
"""

from __future__ import annotations

from typing import Any

from config import Settings, get_settings
from langchain_core.language_models.chat_models import BaseChatModel


def get_llm(
    model: str | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
    settings: Settings | None = None,
) -> BaseChatModel:
    settings = settings or get_settings()
    resolved = settings.resolve_model(model)
    timeout_s = timeout or float(settings.llm_timeout_s)
    tokens = max_tokens or settings.llm_max_tokens

    if settings.llm_provider == "gemini":
        return _gemini_llm(
            model=resolved,
            api_key=settings.llm_api_key(),
            temperature=settings.llm_temperature,
            timeout_s=timeout_s,
            max_tokens=tokens,
        )

    kwargs: dict[str, Any] = {
        "model": resolved,
        "api_key": settings.llm_api_key(),
        "temperature": settings.llm_temperature,
        "timeout": timeout_s,
        "max_tokens": tokens,
        # stream_usage breaks some free OpenRouter providers
        "stream_usage": settings.llm_provider != "openrouter",
    }

    if settings.llm_provider == "openai" and settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url

    if settings.llm_provider == "openrouter":
        kwargs["base_url"] = settings.openrouter_base_url
        headers: dict[str, str] = {}
        if settings.openrouter_http_referer:
            headers["HTTP-Referer"] = settings.openrouter_http_referer
        if settings.openrouter_app_title:
            headers["X-Title"] = settings.openrouter_app_title
        if headers:
            kwargs["default_headers"] = headers

    from langchain_openai import ChatOpenAI
    return ChatOpenAI(**kwargs)


def _gemini_llm(
    *,
    model: str,
    api_key: str,
    temperature: float,
    timeout_s: float,
    max_tokens: int,
) -> BaseChatModel:
    # We no longer need to import os here as we are passing api_key directly

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise ImportError(
            "Gemini provider requires langchain-google-genai. Install with:\n"
            "  uv pip install langchain-google-genai\n"
            "or: pip install langchain-google-genai"
        ) from exc

    if not api_key:
        raise ValueError(
            "LLM_PROVIDER=gemini requires GEMINI_API_KEY "
            "(from https://aistudio.google.com/apikey)."
        )

    kwargs: dict[str, Any] = {
        "model": model,
        "google_api_key": api_key,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }

    # timeout supported on recent versions
    try:
        return ChatGoogleGenerativeAI(**kwargs, timeout=timeout_s)
    except TypeError:
        return ChatGoogleGenerativeAI(**kwargs)

