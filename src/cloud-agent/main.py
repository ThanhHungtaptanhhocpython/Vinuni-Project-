"""ASGI entrypoint for the standalone cloud agent service."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env before LangChain/LangGraph imports (LangSmith reads os.environ at init).
# Prefer local `src/cloud-agent/.env`; fall back to repo-root `.env` without override.
# In Docker the app root is `/app` — there is no monorepo ancestor, so skip safely.
_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env")
try:
    _ROOT_ENV = _HERE.parents[1] / ".env"  # …/P-172/.env when run from src/cloud-agent
except IndexError:
    _ROOT_ENV = None
if _ROOT_ENV is not None and _ROOT_ENV.is_file():
    load_dotenv(_ROOT_ENV, override=False)


def _normalize_langsmith_env() -> None:
    """Keep LangSmith off unless explicitly enabled with a real API key.

    Repo-root `.env` may still set legacy ``LANGCHAIN_TRACING_V2=true`` with a
    placeholder key. LangChain honors that flag even when ``LANGSMITH_TRACING``
    is false, which produces 403 ingest noise and can stall chat runs.
    """
    key = (
        os.getenv("LANGSMITH_API_KEY")
        or os.getenv("LANGCHAIN_API_KEY")
        or ""
    ).strip()
    placeholder = not key or key.startswith("your-") or key == "lsv2_pt_xxxxx"
    want = (
        os.getenv("LANGSMITH_TRACING", os.getenv("LANGCHAIN_TRACING_V2", ""))
        .strip()
        .lower()
        == "true"
    )
    if not want or placeholder:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"


_normalize_langsmith_env()

from agents.verbose_logger import configure_agent_logging  # noqa: E402
from api.auth_routes import router as auth_router  # noqa: E402
from api.edge_routes import router as edge_router  # noqa: E402
from api.robot_stream_routes import router as robot_stream_router  # noqa: E402
from api.robots_routes import router as robots_router  # noqa: E402
from api.routes import router  # noqa: E402
from api.runs_routes import router as runs_router  # noqa: E402
from api.schedules_routes import router as schedules_router  # noqa: E402
from api.sessions_routes import router as sessions_router  # noqa: E402
from config import get_settings  # noqa: E402
from observability.langfuse import langfuse_status  # noqa: E402
from services.scheduler import scheduler_loop  # noqa: E402


def _langsmith_status() -> str:
    tracing = (
        os.getenv("LANGSMITH_TRACING", os.getenv("LANGCHAIN_TRACING_V2", ""))
        .strip()
        .lower()
        == "true"
    )
    key = (
        os.getenv("LANGSMITH_API_KEY")
        or os.getenv("LANGCHAIN_API_KEY")
        or ""
    ).strip()
    project = (
        os.getenv("LANGSMITH_PROJECT")
        or os.getenv("LANGCHAIN_PROJECT")
        or "default"
    )
    endpoint = os.getenv("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com"
    workspace = os.getenv("LANGSMITH_WORKSPACE_ID")
    placeholder = not key or key.startswith("your-") or key == "lsv2_pt_xxxxx"
    if not tracing:
        return "LangSmith: OFF (set LANGSMITH_TRACING=true)"
    if placeholder:
        return (
            "LangSmith: OFF (replace LANGSMITH_API_KEY with a real key "
            "from smith.langchain.com)"
        )
    workspace_label = ", workspace=<set>" if workspace else ""
    return f"LangSmith: ON -> project={project}, endpoint={endpoint}{workspace_label}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_agent_logging(
        agent_verbose=settings.agent_verbose,
        fallback_level=logging.DEBUG if settings.log_level == "DEBUG" else logging.INFO,
    )
    from stores.sessions import session_store

    print(f"Starting {settings.app_name} in {settings.app_env} mode")

    agent_verbose = settings.agent_verbose
    event_trace = "ON" if agent_verbose == "debug" else "OFF"
    verbose_mode = agent_verbose.upper()
    print(f"Agent verbose logging: {verbose_mode}")
    print(f"Agent event trace: {event_trace}")

    print(_langsmith_status())
    print(langfuse_status(settings))
    print(f"Sessions DB: {session_store.ensure_db()}")
    from stores.robots import robot_store

    seeded = robot_store.ensure_default_seeded()
    print(f"Robots DB: seeded id={seeded['id']} model={seeded['model']}")
    stop = asyncio.Event()
    tick_task: asyncio.Task | None = None
    if settings.app_env != "test":
        tick_task = asyncio.create_task(scheduler_loop(stop))
    try:
        yield
    finally:
        stop.set()
        if tick_task is not None:
            tick_task.cancel()
            try:
                await tick_task
            except asyncio.CancelledError:
                pass
        print("Shutting down...")


app = FastAPI(
    title="AI20K Agent",
    description="AI Agent built with LangGraph",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(edge_router, prefix="/api/v1")
app.include_router(robots_router, prefix="/api/v1")
app.include_router(robot_stream_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")
app.include_router(schedules_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}


__all__ = ["app"]
