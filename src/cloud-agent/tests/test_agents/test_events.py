from unittest.mock import patch

from agents.events import LoggingEmitter
from agents.trace_repo import trace_repository


def test_logging_emitter_redaction():
    emitter = LoggingEmitter(run_id="run_1", trace_id="trc_1")
    trace_repository.reset_for_test()

    with patch("agents.events.logger") as mock_logger:
        emitter.emit("tool.started", {"input_text": "secret123", "normal": "abc"})
        mock_logger.debug.assert_called_once()
        log_call_args = mock_logger.debug.call_args[0]
        assert "secret123" not in str(log_call_args)
        assert "normal" in str(log_call_args)
        mock_logger.reset_mock()

        emitter.emit("run.failed", {"error": "raw_exception_text"})
        mock_logger.debug.assert_called_once()
        log_call_args = mock_logger.debug.call_args[0]
        assert "raw_exception_text" not in str(log_call_args)
