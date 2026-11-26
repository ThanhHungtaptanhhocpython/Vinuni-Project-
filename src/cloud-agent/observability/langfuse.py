from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.context import RunContext
    from config import Settings


_PLACEHOLDER_VALUES = {
    "",
    "your-langfuse-public-key",
    "your-langfuse-secret-key",
    "pk-lf-...",
    "sk-lf-...",
}


def _configured_base_url(settings: Settings) -> str:
    return settings.langfuse_base_url or settings.langfuse_host or "https://cloud.langfuse.com"


def _is_placeholder(value: str) -> bool:
    normalized = value.strip()
    return normalized in _PLACEHOLDER_VALUES or normalized.startswith("your-")


def langfuse_status(settings: Settings) -> str:
    if not settings.langfuse_enabled:
        return "Langfuse: OFF (set LANGFUSE_ENABLED=true)"
    if _is_placeholder(settings.langfuse_public_key) or _is_placeholder(settings.langfuse_secret_key):
        return "Langfuse: OFF (set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)"
    return (
        "Langfuse: ON -> "
        f"base_url={_configured_base_url(settings)}, "
        f"environment={settings.langfuse_environment}"
    )


def langfuse_metadata(ctx: RunContext) -> dict[str, Any]:
    settings = ctx.settings
    return {
        "run_id": ctx.run_id,
        "trace_id": ctx.trace_id,
        "session_id": ctx.session_id,
        "robot_id": ctx.robot_id,
        "user_id": ctx.user_id,
        "source": ctx.source,
        "llm_provider": settings.llm_provider,
        "model": settings.resolve_model(),
        "app_env": settings.app_env,
    }


def _export_langfuse_env(settings: Settings) -> None:
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key.strip())
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key.strip())
    os.environ.setdefault("LANGFUSE_BASE_URL", _configured_base_url(settings).strip())
    # Older examples use LANGFUSE_HOST. Set it too for compatibility.
    os.environ.setdefault("LANGFUSE_HOST", _configured_base_url(settings).strip())
    os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", settings.langfuse_environment.strip())


def get_langfuse_callbacks(ctx: RunContext) -> list[Any]:
    settings = ctx.settings
    if not settings.langfuse_enabled:
        return []
    if _is_placeholder(settings.langfuse_public_key) or _is_placeholder(settings.langfuse_secret_key):
        return []

    _export_langfuse_env(settings)
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        return []
    return [CallbackHandler()]
