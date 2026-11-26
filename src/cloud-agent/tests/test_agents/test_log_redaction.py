import logging
from unittest.mock import MagicMock, patch

import pytest
from agents.events import LoggingEmitter


class LogCaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))

@pytest.fixture
def capture_logs():
    handler = LogCaptureHandler()
    loggers = [logging.getLogger(name) for name in ["agent", "agent.think", "agent.events", "agent.llm"]]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    yield handler
    for logger in loggers:
        logger.removeHandler(handler)

def test_events_redacts_secrets(capture_logs):
    from datetime import UTC, datetime

    from agents.trace_repo import trace_repository
    from models.schemas import RunDetail, RunSource, RunStatus

    # Persist requires a parent run; without it FK errors leak raw payloads
    # into ERROR logs and defeat the redaction assertion.
    trace_repository.create_run(
        RunDetail(
            id="run_1",
            trace_id="trc_1",
            session_id="ses_1",
            robot_id="rob_1",
            user_id="user_1",
            source=RunSource.CHAT,
            status=RunStatus.RUNNING,
            input_text="placeholder",
            created_at=datetime.now(UTC).isoformat(),
        )
    )

    emitter = LoggingEmitter("run_1", "trc_1")
    emitter.emit("run.started", {"input_text": "RAW_USER_SECRET_123"})
    emitter.emit("tool.started", {"args": {"s": "RAW_TOOL_SECRET_789"}})
    emitter.emit("tool.completed", {"summary": "RAW_TOOL_SECRET_789"})
    emitter.emit("run.failed", {"error": {"message": "RAW_PROVIDER_SECRET_XYZ"}})

    log_output = "\n".join(capture_logs.records)
    assert "RAW_USER_SECRET_123" not in log_output
    assert "RAW_TOOL_SECRET_789" not in log_output
    assert "RAW_PROVIDER_SECRET_XYZ" not in log_output

@pytest.mark.asyncio
async def test_llm_provider_exception_redaction(capture_logs):
    import openai
    from agents.context import RunContext
    from agents.events import LoggingEmitter
    from agents.llm import invoke_llm
    from agents.tools.mcp_client import McpClient
    from agents.tools.mock_transport import MockEdgeTransport
    from config import Settings

    settings = Settings(llm_provider="openai", agent_verbose="off")
    ctx = RunContext(
        run_id="run_1",
        trace_id="trc_1",
        session_id="ses_1",
        user_id="user_1",
        robot_id="rob_1",
        source="CHAT",
        mcp=McpClient(MockEdgeTransport()),
        emitter=LoggingEmitter("run_1", "trc_1"),
        settings=settings,
        episode_poll_interval_s=0,
    )

    with patch("agents.llm.get_llm") as mock_get:
        mock_llm = MagicMock()

        async def mock_ainvoke(*args, **kwargs):
            raise openai.BadRequestError("RAW_PROVIDER_SECRET_XYZ", response=MagicMock(), body=None)
            yield

        async def mock_ainvoke_func(*args, **kwargs):
            raise openai.BadRequestError("RAW_PROVIDER_SECRET_XYZ", response=MagicMock(), body=None)

        mock_llm.ainvoke = mock_ainvoke_func
        mock_llm.astream = mock_ainvoke
        mock_get.return_value = mock_llm

        caught = None
        try:
            await invoke_llm(ctx, [])
        except Exception as exc:
            caught = exc

    assert caught is not None
    error_payload = caught.to_object() if hasattr(caught, "to_object") else str(caught)
    assert "RAW_PROVIDER_SECRET_XYZ" not in str(error_payload)
