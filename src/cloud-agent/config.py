"""Application settings (Cloud agent + Google SSO)."""

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_JWT_SECRETS = frozenset({"", "dev-only-change-me", "change-me-in-production"})
# Stable across pytest processes; never used when APP_ENV=production.
_TEST_JWT_SECRET = "test-only-jwt-secret-do-not-use-in-production-32"


def _is_weak_jwt_secret(value: str) -> bool:
    return value.strip() in _WEAK_JWT_SECRETS


def _load_or_create_jwt_secret(path: Path) -> str:
    """Reuse a gitignored local file so dev restarts keep signing the same key."""
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing and not _is_weak_jwt_secret(existing) and len(existing) >= 32:
                return existing
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"

    # LLM (DOC 1 §1.6)
    # openai | openrouter | gemini
    llm_provider: Literal["openai", "openrouter", "gemini"] = "openai"
    openai_api_key: str = ""
    # OpenAI-compatible endpoint override (vLLM, Azure OpenAI-compatible, Together, Groq, …).
    # Empty = default OpenAI API endpoint.
    openai_base_url: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_http_referer: str = ""
    openrouter_app_title: str = "AI20K VLM Agent"
    # Google AI Studio / Gemini API key (https://aistudio.google.com/apikey)
    gemini_api_key: str = ""
    model_name: str = "gpt-4o"
    model_fallback: str = "gpt-4o-mini"
    llm_timeout_s: int = 45
    llm_max_tokens: int = 1024
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    def resolve_model(self, model: str | None = None) -> str:
        name = model or self.model_name
        if self.llm_provider == "openrouter" and "/" not in name:
            return f"openai/{name}"
        # Gemini API uses short names: gemini-2.0-flash, gemini-1.5-pro, …
        if self.llm_provider == "gemini" and name.startswith("google/"):
            return name.removeprefix("google/")
        return name

    def llm_api_key(self) -> str:
        if self.llm_provider == "openrouter":
            return self.openrouter_api_key or self.openai_api_key
        if self.llm_provider == "gemini":
            return self.gemini_api_key
        return self.openai_api_key

    # Agent limits (DOC 1 §1.6) — tighter so free multi-tool runs finish sooner
    agent_max_iterations: int = 10
    agent_max_tool_calls: int = 30
    agent_max_take_action: int = 4
    agent_max_run_duration_s: int = 900
    agent_context_token_budget: int = 100_000

    # Agent Verbose Terminal Logging
    agent_verbose: Literal["off", "standard", "debug"] = "standard"
    agent_verbose_max_value_length: int = 2000

    # Edge / MCP
    # Empty = use MockEdgeTransport for local UI demos and tests.
    # Example: https://local.screenrecorder.io.vn/mcp
    mcp_endpoint_url: str = ""
    mcp_protocol_version: str = "2025-03-26"
    mcp_client_name: str = "p172-cloudagent"
    mcp_client_version: str = "0.1.0"
    # True chỉ khi MCP_ENDPOINT_URL trỏ tới libero_eval/mcp_server (Panda/robosuite,
    # camera "agentview", tham số take_action/stop_robot khác tên) — bật các quirk dịch
    # tham số riêng cho LIBERO trong HttpMcpTransport. False (mặc định) = SO-101
    # edge_vla/mcp_server thật, gửi tham số nguyên bản theo DOC 0.
    mcp_libero_quirks: bool = False
    # Bearer token gửi tới Edge MCP server khi Edge chạy EDGE_VLA_AUTH_MODE=bearer
    # (phải khớp EDGE_VLA_MCP_TOKEN phía edge_vla). Để trống = không gửi Authorization.
    mcp_auth_token: str = ""

    # How the Edge MCP server is running (used to seed the initial robot list).
    # In production single-VM deploy we only support max 1 robot for now.
    #
    # Deploy note: `deploy/docker-compose.yml` sets Edge `ROBOT_MODE=sim`; we also
    # set `EDGE_ROBOT_MODE=sim` here for consistent UI bootstrapping.
    edge_robot_mode: Literal["sim", "real"] = "sim"

    # Shared secret to verify Edge self-registration JWTs (`POST /api/v1/edge/register`).
    # Must match EDGE_JWT_SECRET on edge_vla (HS256). Separate from `jwt_secret`
    # (user access tokens) so Edge vs browser trust can be rotated independently.
    edge_jwt_secret: str = ""

    # Database — PostgreSQL primary (SQLAlchemy + psycopg)
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5433/p172"
    chroma_persist_dir: str = "./data/chroma"
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "long_term_memories"
    embedding_model: str = "text-embedding-3-small"
    long_term_memory_enabled: bool = True
    long_term_memory_top_k: int = Field(default=5, ge=1, le=20)

    # Auth / Google OIDC (DOC 1 §4.2)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    ui_base: str = "http://localhost:3000"
    jwt_secret: str = ""
    jwt_secret_file: str = "data/.jwt_secret"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = Field(default=15, ge=1)
    jwt_refresh_expire_days: int = Field(default=14, ge=1)
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 60 * 60 * 24 * 14
    allowed_email_domains: str = ""
    auth_required: bool = False
    auth_cookie_secure: bool = True

    # Langfuse observability (disabled by default; never required for tests/local)
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"
    # Legacy alias used by older Langfuse docs/deployments. Prefer BASE_URL.
    langfuse_host: str = ""
    langfuse_environment: str = "development"
    @model_validator(mode="after")
    def _require_jwt_secret(self) -> Self:
        """JWT_SECRET must come from the environment in production.

        Development persists a generated secret to jwt_secret_file so restarts
        do not invalidate existing access/refresh tokens.
        """
        weak = _is_weak_jwt_secret(self.jwt_secret)
        if self.app_env == "production":
            if weak or len(self.jwt_secret) < 32:
                raise ValueError(
                    "JWT_SECRET must be set to a strong random value (min 32 chars) in production."
                )
        elif weak:
            if self.app_env == "test":
                self.jwt_secret = _TEST_JWT_SECRET
            else:
                self.jwt_secret = _load_or_create_jwt_secret(Path(self.jwt_secret_file))
        # Cookie-JWT helpers in auth.security follow the TTL seconds settings.
        self.jwt_access_expire_minutes = max(1, self.access_token_ttl_s // 60)
        self.jwt_refresh_expire_days = max(1, self.refresh_token_ttl_s // 86400)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
