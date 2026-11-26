"""Terminal presentation layer for LangChain-style Verbose logging."""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from config import Settings

from .sanitizer import sanitize_payload

# Initialize logger for verbose output
logger = logging.getLogger("agent.verbose")

# Best-effort root for relative log paths (monorepo P-172/… or Docker /app).
def _log_path_root() -> Path:
    service_root = Path(__file__).resolve().parent.parent  # …/cloud-agent or /app
    src_dir = service_root.parent
    if service_root.name == "cloud-agent" and src_dir.name == "src":
        return src_dir.parent  # monorepo root → paths like src/cloud-agent/agents/…
    return service_root


_REPO_ROOT = _log_path_root()

# Check if stdout/stderr supports colors
_USE_COLORS = sys.stderr.isatty()

# ANSI colors
C_BLUE = "\033[94m" if _USE_COLORS else ""
C_GREEN = "\033[92m" if _USE_COLORS else ""
C_RED = "\033[91m" if _USE_COLORS else ""
C_YELLOW = "\033[93m" if _USE_COLORS else ""
C_DIM = "\033[2m" if _USE_COLORS else ""
C_BOLD = "\033[1m" if _USE_COLORS else ""
C_RESET = "\033[0m" if _USE_COLORS else ""


def _relpath(pathname: str) -> str:
    path = Path(pathname)
    try:
        return str(path.resolve().relative_to(_REPO_ROOT))
    except ValueError:
        return path.name


class LocationFormatter(logging.Formatter):
    """Prefix the first line with `path:line` (dim when the tty supports color)."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        loc = f"{_relpath(record.pathname)}:{record.lineno}"
        tag = f"{C_DIM}{loc}{C_RESET}"
        if not rendered:
            return tag
        first, _, rest = rendered.partition("\n")
        headed = f"{tag} {first}"
        return headed if not rest else f"{headed}\n{rest}"


class AgentVerboseLogger:
    """Formats and prints agent lifecycle events to the terminal."""

    def __init__(self, run_id: str, settings: Settings):
        self.run_id = run_id
        self.settings = settings
        self.mode = settings.agent_verbose
        self.max_length = settings.agent_verbose_max_value_length
        self.prefix = f"{C_DIM}[{run_id[:10]}]{C_RESET}"

        # State tracking for final summary
        self.start_time = time.perf_counter()
        self.llm_calls = 0
        self.tool_calls = 0

        # We store partial LLM start time to calculate latency
        self._llm_start_time = 0.0

    def _truncate(self, text: str) -> str:
        if len(text) > self.max_length:
            return f"{text[:self.max_length]}\n\n(result truncated, {len(text)} chars)"
        return text

    def _format_json(self, data: Any) -> str:
        """Sanitize and format JSON payload with truncation."""
        try:
            sanitized = sanitize_payload(data)
            json_str = json.dumps(sanitized, ensure_ascii=False, indent=2, default=str)
            return self._truncate(json_str)
        except Exception:
            return str(data)

    def _safe_log(self, text: str):
        """Prints log safely suppressing formatting errors."""
        if self.mode == "off":
            return
        try:
            # Prefix every line to support concurrency
            prefixed = "\n".join(f"{self.prefix} {line}" for line in text.split("\n"))
            logger.info(prefixed, stacklevel=2)
        except Exception as e:
            # Log the error instead of suppressing it completely
            logger.error(f"Agent logging failed: {e}")

    def run_start(self, robot_id: str, session_id: str, model: str):
        if self.mode == "off":
            return
        out = (
            f"\n{C_BOLD}────────────────────────────────────────────────────────{C_RESET}\n"
            f"{C_BOLD}Agent Run:{C_RESET} {self.run_id}\n"
            f"{C_BOLD}────────────────────────────────────────────────────────{C_RESET}\n\n"
            f"{C_GREEN}> Entering Agent run...{C_RESET}\n\n"
            f"  robot:   {robot_id}\n"
            f"  session: {session_id}\n"
            f"  model:   {model}\n"
        )
        self._safe_log(out)

    def preflight(self, tool_count: int, robot_status: str | None):
        out = (
            f"{C_GREEN}✓ Preflight completed{C_RESET}\n"
            f"  robot_status: {robot_status or 'unknown'}\n"
            f"  tools: {tool_count}\n"
        )
        self._safe_log(out)

    def think_loop(self, iteration: int):
        self._safe_log(f"{C_BLUE}> Entering think loop #{iteration}...{C_RESET}\n")

    def llm_start(self, model: str, provider: str, messages_count: int, tool_count: int, attempt: int = 1, fallback: bool = False):
        self.llm_calls += 1
        self._llm_start_time = time.perf_counter()

        out = (
            f"Invoking LLM: `{C_BOLD}{model}{C_RESET}`\n"
            f"  provider: {provider}\n"
            f"  messages: {messages_count}\n"
            f"  tools: {tool_count}\n"
        )
        if self.mode == "debug" or attempt > 1 or fallback:
            out += f"  attempt: {attempt}\n"
            out += f"  fallback: {str(fallback).lower()}\n"

        self._safe_log(out)

    def llm_end(self, finish_reason: str):
        latency_ms = int((time.perf_counter() - self._llm_start_time) * 1000)
        out = (
            f"LLM completed\n"
            f"  latency: {latency_ms}ms\n"
            f"  finish_reason: {finish_reason}\n"
        )
        self._safe_log(out)

    def llm_failed(self, model: str, error: Any, attempt: int, fallback: bool):
        out = (
            f"{C_RED}LLM failed:{C_RESET} `{model}`\n\n"
            f"Error:\n  {self._format_json(error)}\n\n"
            f"  attempt: {attempt}\n"
            f"  fallback: {str(fallback).lower()}\n"
        )
        self._safe_log(out)

    def tool_start(self, tool: str, args: Any):
        self.tool_calls += 1
        out = (
            f"Invoking tool: `{C_BOLD}{tool}{C_RESET}`\n\n"
            f"Arguments:\n  {self._format_json(args)}\n"
        )
        self._safe_log(out)

    def tool_end(self, tool: str, result: Any, success: bool):
        status_color = C_GREEN if success else C_RED
        status_text = "result" if success else "failed"

        out = (
            f"Tool {status_color}{status_text}{C_RESET}: `{tool}`\n\n"
            f"{self._format_json(result)}\n"
        )
        self._safe_log(out)

    def final_answer(self, text: str):
        out = (
            f"Final Answer:\n"
            f"  {self._truncate(text)}\n"
        )
        self._safe_log(out)

    def run_completed(self, status: str):
        duration_s = round(time.perf_counter() - self.start_time, 2)
        out = (
            f"{C_GREEN}> Finished Agent run.{C_RESET}\n\n"
            f"  status: {status}\n"
            f"  llm_calls: {self.llm_calls}\n"
            f"  tool_calls: {self.tool_calls}\n"
            f"  duration: {duration_s}s\n"
            f"{C_BOLD}────────────────────────────────────────────────────────{C_RESET}\n"
        )
        self._safe_log(out)

    def run_failed(self, error: Any):
        duration_s = round(time.perf_counter() - self.start_time, 2)
        out = (
            f"{C_RED}> Agent run failed.{C_RESET}\n\n"
            f"Error:\n  {self._format_json(error)}\n\n"
            f"Run:\n"
            f"  status: FAILED\n"
            f"  duration: {duration_s}s\n"
            f"{C_BOLD}────────────────────────────────────────────────────────{C_RESET}\n"
        )
        self._safe_log(out)


def configure_agent_logging(agent_verbose: str = "standard", fallback_level: int = logging.INFO) -> None:
    """Attach one StreamHandler per agent logger (visible under uvicorn)."""
    fmt = LocationFormatter("%(message)s")

    # Determine levels based on AGENT_VERBOSE mode
    if agent_verbose == "debug":
        verbose_level = logging.DEBUG
        events_level = logging.DEBUG
    elif agent_verbose == "standard":
        verbose_level = logging.INFO
        events_level = logging.WARNING  # Hide raw event trace in standard
    else:  # "off"
        verbose_level = logging.WARNING # Hide standard info
        events_level = logging.WARNING  # Hide raw event trace

    loggers_config = {
        "agent": fallback_level,
        "agent.verbose": verbose_level,
        "agent.events": events_level,
        "agent.llm": fallback_level,
    }

    for name, level in loggers_config.items():
        log = logging.getLogger(name)
        log.setLevel(level)
        log.propagate = False
        for handler in list(log.handlers):
            log.removeHandler(handler)
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(fmt)
        log.addHandler(handler)
