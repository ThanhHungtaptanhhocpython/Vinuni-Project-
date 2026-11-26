from agents.runner import _langsmith_run_config, build_default_context


def test_langsmith_run_config_includes_searchable_metadata_without_prompt() -> None:
    ctx = build_default_context(
        robot_id="robot_test",
        session_id="session_test",
        user_id="user_test",
        source="CHAT",
    )

    config = _langsmith_run_config(ctx)

    assert config["run_name"] == "p172-agent-run"
    assert f"source:{ctx.source}" in config["tags"]
    assert f"robot:{ctx.robot_id}" in config["tags"]

    metadata = config["metadata"]
    assert metadata["run_id"] == ctx.run_id
    assert metadata["trace_id"] == ctx.trace_id
    assert metadata["session_id"] == ctx.session_id
    assert metadata["robot_id"] == ctx.robot_id
    assert metadata["user_id"] == ctx.user_id
    assert metadata["source"] == ctx.source
    assert metadata["llm_provider"] == ctx.settings.llm_provider
    assert metadata["model"] == ctx.settings.resolve_model()

    serialized = repr(config)
    assert "user_message" not in serialized
    assert "messages" not in serialized
    assert "prompt" not in serialized.lower()


def test_build_default_context_reuses_provided_ids() -> None:
    ctx = build_default_context(
        robot_id="robot_test",
        session_id="ses_test",
        user_id="user_test",
        source="SCHEDULE",
        run_id="run_fixed123",
        trace_id="trc_fixed123",
    )
    assert ctx.run_id == "run_fixed123"
    assert ctx.trace_id == "trc_fixed123"
    assert ctx.emitter.run_id == "run_fixed123"


def test_build_default_context_derives_trace_id_from_run_id() -> None:
    ctx = build_default_context(run_id="run_abc", source="SCHEDULE")
    assert ctx.run_id == "run_abc"
    assert ctx.trace_id == "trc_abc"
