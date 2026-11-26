from unittest.mock import MagicMock, patch

import pytest
from agents.context import RunContext
from agents.llm import invoke_llm
from langchain_core.messages import HumanMessage


@pytest.mark.asyncio
async def test_invoke_llm_emits_events():
    # Setup dummy context with emitter
    class DummySettings:
        llm_provider = "openai"
        model_name = "gpt-4"
        model_fallback = "gpt-3.5-turbo"
        llm_timeout_s = 10.0
        llm_max_tokens = 100
        agent_verbose = "off"
        agent_verbose_max_value_length = 2000

        def resolve_model(self, fallback=None):
            return fallback or self.model_name

    class DummyEmitter:
        def __init__(self):
            self.events = []
        def emit(self, event_type, payload):
            self.events.append((event_type, payload))

    emitter = DummyEmitter()

    ctx = RunContext(
        run_id="run_1",
        session_id="ses_1",
        user_id="user_1",
        robot_id="rob_1",
        trace_id="trc_1",
        source="CHAT",
        mcp=MagicMock(),
        emitter=emitter,
        settings=DummySettings(),
        episode_poll_interval_s=0.1
    )

    with patch("agents.llm.get_llm") as mock_get_llm:
        mock_llm = MagicMock()

        async def mock_astream(*args, **kwargs):
            from langchain_core.messages import AIMessageChunk
            yield AIMessageChunk(content="hello", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})

        mock_llm.astream = mock_astream
        mock_get_llm.return_value = mock_llm

        await invoke_llm(ctx, [HumanMessage(content="hi")])

        started_events = [e for e in emitter.events if e[0] == "llm.started"]
        completed_events = [e for e in emitter.events if e[0] == "llm.completed"]

        assert len(started_events) == 1
        assert len(completed_events) == 1

        start_payload = started_events[0][1]
        assert start_payload["provider"] == "openai"
        assert start_payload["model"] == "gpt-4"
        assert start_payload["attempt"] == 1
        assert not start_payload["is_fallback"]

        comp_payload = completed_events[0][1]
        assert comp_payload["usage"]["prompt_tokens"] == 10
        assert comp_payload["usage"]["completion_tokens"] == 5
        assert comp_payload["usage"]["total_tokens"] == 15
        assert "duration_ms" in comp_payload

@pytest.mark.asyncio
async def test_invoke_llm_fallback_and_failed_emits_events():
    class DummySettings:
        llm_provider = "openai"
        model_name = "gpt-4"
        model_fallback = "gpt-3.5-turbo"
        llm_timeout_s = 10.0
        llm_max_tokens = 100
        agent_verbose = "off"
        agent_verbose_max_value_length = 2000

        def resolve_model(self, fallback=None):
            return fallback or self.model_name

    class DummyEmitter:
        def __init__(self):
            self.events = []
        def emit(self, event_type, payload):
            self.events.append((event_type, payload))

    emitter = DummyEmitter()
    ctx = RunContext(
        run_id="run_1", session_id="ses_1", user_id="user_1", robot_id="rob_1", trace_id="trc_1",
        source="CHAT", mcp=MagicMock(), emitter=emitter, settings=DummySettings(), episode_poll_interval_s=0.1
    )

    with patch("agents.llm.get_llm") as mock_get_llm:
        mock_llm = MagicMock()

        # Simulate 400 BadRequest to force fallback, then APIStatusError to force failure
        import httpx
        import openai

        resp = httpx.Response(400, request=httpx.Request("POST", "http://x"))

        call_count = 0
        async def mock_astream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise openai.BadRequestError("bad request JSON", response=resp, body=None)
            else:
                raise openai.APIStatusError("status error", response=resp, body=None)
            yield

        mock_llm.astream = mock_astream

        async def mock_ainvoke(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise openai.BadRequestError("bad request JSON", response=resp, body=None)
            else:
                raise openai.APIStatusError("status error from ainvoke", response=resp, body=None)

        mock_llm.ainvoke = mock_ainvoke
        mock_get_llm.return_value = mock_llm

        from agents.errors import AppError
        with pytest.raises(AppError):
            await invoke_llm(ctx, [HumanMessage(content="hi")])

        started_events = [e for e in emitter.events if e[0] == "llm.started"]
        failed_events = [e for e in emitter.events if e[0] == "llm.failed"]

        assert len(started_events) == 2

        # We emit failed for astream and ainvoke, so 2 failures for primary model, 1 failure for fallback
        assert len(failed_events) >= 2

        # First call (primary model)
        assert started_events[0][1]["model"] == "gpt-4"
        assert started_events[0][1]["is_fallback"] is False
        assert failed_events[0][1]["error"]["type"] == "BadRequestError"

        # Second call (fallback model)
        assert started_events[1][1]["model"] == "gpt-3.5-turbo"
        assert started_events[1][1]["is_fallback"] is True
        assert failed_events[-1][1]["error"]["type"] == "APIStatusError"
        assert failed_events[-1][1]["model"] == "gpt-3.5-turbo"

@pytest.mark.asyncio
async def test_invoke_llm_nullable_usage():
    class DummySettings:
        llm_provider = "openai"
        model_name = "gpt-4"
        model_fallback = "gpt-3.5-turbo"
        llm_timeout_s = 10.0
        llm_max_tokens = 100
        agent_verbose = "off"
        agent_verbose_max_value_length = 2000
        def resolve_model(self, fallback=None):
            return fallback or self.model_name
    class DummyEmitter:
        def __init__(self):
            self.events = []
        def emit(self, event_type, payload):
            self.events.append((event_type, payload))

    emitter = DummyEmitter()
    ctx = RunContext(
        run_id="run_1", session_id="ses_1", user_id="user_1", robot_id="rob_1", trace_id="trc_1",
        source="CHAT", mcp=MagicMock(), emitter=emitter, settings=DummySettings(), episode_poll_interval_s=0.1
    )

    with patch("agents.llm.get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        async def mock_astream(*args, **kwargs):
            from langchain_core.messages import AIMessageChunk
            yield AIMessageChunk(content="hello", usage_metadata=None)

        mock_llm.astream = mock_astream
        mock_get_llm.return_value = mock_llm

        await invoke_llm(ctx, [HumanMessage(content="hi")])
        completed_events = [e for e in emitter.events if e[0] == "llm.completed"]

        assert completed_events[0][1]["usage"] is None
