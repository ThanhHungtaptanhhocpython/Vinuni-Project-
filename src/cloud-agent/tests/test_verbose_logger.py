import logging

import pytest
from agents.verbose_logger import AgentVerboseLogger
from config import get_settings


@pytest.fixture
def base_settings():
    settings = get_settings()
    settings.agent_verbose_max_value_length = 100
    return settings

def test_verbose_off(base_settings, caplog):
    base_settings.agent_verbose = "off"
    logger = AgentVerboseLogger("run_123", base_settings)

    with caplog.at_level(logging.INFO, logger="agent.verbose"):
        logger.run_start("robot_1", "ses_1", "gemini")
        logger.llm_start("model", "provider", 1, 1)
        logger.run_completed("COMPLETED")

    assert len(caplog.records) == 0

def test_verbose_standard_and_truncation(base_settings, caplog):
    base_settings.agent_verbose = "standard"
    # Ensure maximum length forces truncation
    base_settings.agent_verbose_max_value_length = 50
    logger = AgentVerboseLogger("run_123", base_settings)

    long_string = "A " * 100
    payload = {"key": long_string}

    with caplog.at_level(logging.INFO, logger="agent.verbose"):
        logger.tool_start("long_tool", payload)

    assert len(caplog.records) == 1
    log_text = caplog.records[0].message
    assert "[run_123]" in log_text
    assert "long_tool" in log_text
    assert "result truncated" in log_text

def test_verbose_redaction(base_settings, caplog):
    base_settings.agent_verbose = "standard"
    base_settings.agent_verbose_max_value_length = 1000
    logger = AgentVerboseLogger("run_456", base_settings)

    sensitive_payload = {
        "api_key": "super-secret-key",
        "authorization": "Bearer token123",
        "nested": {
            "password": "my_password",
            "normal_field": "hello"
        }
    }

    with caplog.at_level(logging.INFO, logger="agent.verbose"):
        logger.tool_end("auth_tool", sensitive_payload, True)

    assert len(caplog.records) == 1
    log_text = caplog.records[0].message
    assert "super-secret-key" not in log_text
    assert "Bearer token123" not in log_text
    assert "my_password" not in log_text
    assert "hello" in log_text
    assert "[REDACTED_SECRET]" in log_text

def test_verbose_run_summary(base_settings, caplog):
    base_settings.agent_verbose = "debug"
    logger = AgentVerboseLogger("run_789", base_settings)

    with caplog.at_level(logging.INFO, logger="agent.verbose"):
        logger.run_start("robot_1", "ses_1", "gemini")
        logger.llm_start("model", "provider", 2, 2)
        logger.llm_end("tool_calls")
        logger.tool_start("my_tool", {})
        logger.tool_end("my_tool", {"ok": True}, True)
        logger.llm_start("model", "provider", 3, 2, attempt=2, fallback=True)
        logger.llm_end("stop")
        logger.final_answer("The result is good.")
        logger.run_completed("COMPLETED")

    log_text = "\n".join(r.message for r in caplog.records)

    # Check LLM and tool counts in summary
    assert "llm_calls: 2" in log_text
    assert "tool_calls: 1" in log_text
    assert "COMPLETED" in log_text
    assert "attempt: 2" in log_text
    assert "fallback: true" in log_text

def test_location_formatter_prefixes_path_and_line():
    from pathlib import Path

    from agents.verbose_logger import LocationFormatter

    repo = Path(__file__).resolve().parents[3]
    record = logging.LogRecord(
        name="agent.verbose",
        level=logging.INFO,
        pathname=str(repo / "src/cloud-agent/agents/verbose_logger.py"),
        lineno=84,
        msg="[run_123] hello",
        args=(),
        exc_info=None,
    )
    formatted = LocationFormatter("%(message)s").format(record)
    assert formatted.startswith("src/cloud-agent/agents/verbose_logger.py:84 ")
    assert "[run_123] hello" in formatted


def test_configure_agent_logging():
    from agents.verbose_logger import configure_agent_logging

    # Test OFF
    configure_agent_logging(agent_verbose="off")
    assert logging.getLogger("agent.verbose").level == logging.WARNING
    assert logging.getLogger("agent.events").level == logging.WARNING

    # Test STANDARD
    configure_agent_logging(agent_verbose="standard")
    assert logging.getLogger("agent.verbose").level == logging.INFO
    assert logging.getLogger("agent.events").level == logging.WARNING

    # Test DEBUG
    configure_agent_logging(agent_verbose="debug")
    assert logging.getLogger("agent.verbose").level == logging.DEBUG
    assert logging.getLogger("agent.events").level == logging.DEBUG



