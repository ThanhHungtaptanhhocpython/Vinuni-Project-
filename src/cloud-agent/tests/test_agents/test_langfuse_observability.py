from agents.runner import _agent_run_config, build_default_context
from observability.langfuse import (
    get_langfuse_callbacks,
    langfuse_metadata,
    langfuse_status,
)


def test_langfuse_disabled_returns_no_callbacks() -> None:
    ctx = build_default_context(
        robot_id="robot_test",
        session_id="session_test",
        user_id="user_test",
    )
    ctx.settings.langfuse_enabled = False

    assert get_langfuse_callbacks(ctx) == []
    assert "Langfuse: OFF" in langfuse_status(ctx.settings)


def test_langfuse_missing_keys_returns_no_callbacks() -> None:
    ctx = build_default_context()
    ctx.settings.langfuse_enabled = True
    ctx.settings.langfuse_public_key = "your-langfuse-public-key"
    ctx.settings.langfuse_secret_key = "your-langfuse-secret-key"

    assert get_langfuse_callbacks(ctx) == []
    assert "set LANGFUSE_PUBLIC_KEY" in langfuse_status(ctx.settings)


def test_langfuse_metadata_is_operational_only() -> None:
    ctx = build_default_context(
        robot_id="robot_test",
        session_id="session_test",
        user_id="user_test",
        source="CHAT",
    )

    metadata = langfuse_metadata(ctx)

    assert metadata["run_id"] == ctx.run_id
    assert metadata["trace_id"] == ctx.trace_id
    assert metadata["session_id"] == ctx.session_id
    assert metadata["robot_id"] == ctx.robot_id
    assert metadata["user_id"] == ctx.user_id
    assert metadata["source"] == ctx.source
    assert metadata["llm_provider"] == ctx.settings.llm_provider
    assert metadata["model"] == ctx.settings.resolve_model()

    serialized = repr(metadata)
    assert "prompt" not in serialized.lower()
    assert "messages" not in serialized
    assert "content" not in serialized.lower()
    assert "reasoning" not in serialized.lower()


def test_agent_run_config_preserves_langsmith_metadata_without_prompt() -> None:
    ctx = build_default_context(
        robot_id="robot_test",
        session_id="session_test",
        user_id="user_test",
    )
    ctx.settings.langfuse_enabled = False

    config = _agent_run_config(ctx)

    assert config["metadata"]["run_id"] == ctx.run_id
    assert config["metadata"]["trace_id"] == ctx.trace_id
    assert config["configurable"]["run_ctx"] is ctx
    assert config["configurable"]["thread_id"] == ctx.run_id
    assert "callbacks" not in config

    serialized = repr(config["metadata"])
    assert "prompt" not in serialized.lower()
    assert "messages" not in serialized
