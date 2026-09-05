import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# The .env lives at the REPO ROOT, but local commands run from
# harmonix360/backend/ (alembic, pytest, uvicorn). A bare env_file=".env"
# resolves against the CWD, so it silently found nothing and every setting fell
# back to its default — including DATABASE_URL's "@postgres:5432", a hostname
# that only resolves inside docker compose. Anchoring the paths to THIS FILE
# instead makes them work from any working directory.
#
# Both locations are listed; a backend-local .env (if anyone adds one) wins over
# the repo-root file. OS environment variables still take precedence over both,
# so docker compose's `environment:` block continues to override this unchanged.
#
# The walk up is computed defensively rather than as a fixed `parents[4]`. On a
# developer's machine this file sits at <repo>/harmonix360/backend/app/core/,
# four levels below the repo root — but the Docker image mounts the backend at
# /app, so the same file is only three levels below the filesystem root and a
# fixed index raises IndexError at import. Since `settings` is imported by
# alembic/env.py and app/main.py alike, that turns into a container that dies
# before it can report anything more useful.
_HERE = Path(__file__).resolve()
_BACKEND_ROOT = _HERE.parents[2]  # .../backend — always present
_REPO_ROOT = _HERE.parents[4] if len(_HERE.parents) > 4 else _BACKEND_ROOT
ENV_FILES = (_REPO_ROOT / ".env", _BACKEND_ROOT / ".env")


class Settings(BaseSettings):
    PROJECT_NAME: str = "Harmonix360"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = Field(default="development", validation_alias="ENVIRONMENT")
    
    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://harmonix360_app:app_password@postgres:5432/harmonix360",
        validation_alias="DATABASE_URL"
    )
    MIGRATION_DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@postgres:5432/harmonix360",
        validation_alias="MIGRATION_DATABASE_URL"
    )
    HASHID_SALT: str = Field(default="harmonix360-secret-salt-change-in-prod", validation_alias="HASHID_SALT")
    
    # Redis
    REDIS_URL: str = Field(default="redis://redis:6379/0", validation_alias="REDIS_URL")
    # B8: MailHog locally; override for an authenticated SMTP relay.
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_FROM_ADDRESS: str = "payroll@peoplepay360.com"
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = False
    PAYSLIP_FONT_DIR: str = "/usr/share/fonts/truetype/dejavu"

    #: Requests per minute per (client IP, path). Generous by default: a single
    #: screen fires several requests — the Employee form alone loads the record,
    #: its smart-button counts and a manager lookup — so a limit tuned to "one
    #: request per user action" throttles ordinary use. Lower it deliberately
    #: for an internet-facing deployment.
    RATE_LIMIT_PER_MINUTE: int = Field(default=600, validation_alias="RATE_LIMIT_PER_MINUTE")
    
    # Security
    JWT_SECRET: str = Field(default="harmonix360-dev-jwt-secret-change-in-production", validation_alias="JWT_SECRET")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # AI Provider — Groq only. Cerebras was removed 2026-09-06: the account
    # authenticates but every completion returns 402 Payment Required (unfunded
    # account, not a transient outage), so it was never a working fallback —
    # just a second failure mode. See app/ai/provider_router.py's module
    # docstring and app/api/v1/routers/ai.py's single-flight guard, which
    # exists because Groq alone has an 8000 TPM ceiling with no fallback.
    GROQ_API_KEY: str = Field(default="", validation_alias="GROQ_API_KEY")
    # Groq retired the Llama 3.1 8B id this default used to name
    # (`llama-3.1-8b-instant`). A retired id is a 404 `model_not_found`, and
    # because that is a provider error the router treats it as a dead
    # provider — so every AI answer degraded to "unavailable" while the key
    # was valid. Verified against Groq's live /v1/models before being changed.
    GROQ_MODEL: str = Field(default="openai/gpt-oss-120b", validation_alias="GROQ_MODEL")
    AI_CACHE_DEFAULT_TTL: int = Field(default=600, validation_alias="AI_CACHE_DEFAULT_TTL")
    AI_PROVIDER_TIMEOUT: int = Field(default=30, validation_alias="AI_PROVIDER_TIMEOUT")
    GROQ_RPM: int = Field(default=30, validation_alias="GROQ_RPM")

    # Observability
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(default="http://signoz-otel-collector:4318", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    SENTRY_DSN: str = Field(default="", validation_alias="SENTRY_DSN")
    #: Sentry's own verbose logging. Off by default — it prints a line per
    #: integration and per transport flush, which drowns out uvicorn's startup
    #: output in container logs.
    SENTRY_DEBUG: bool = Field(default=False, validation_alias="SENTRY_DEBUG")
    # Off by default: SigNoz only runs under `docker compose --profile advanced`
    # (Architecture §12), so the core profile, pytest and CI would otherwise
    # spend every request retrying an OTLP export against a host that isn't
    # there. Set OTEL_ENABLED=true in the advanced profile.
    OTEL_ENABLED: bool = Field(default=False, validation_alias="OTEL_ENABLED")
    # Span-to-stdout, for debugging the tracer itself. Never on in CI: it
    # writes to a stream pytest has already closed by teardown.
    OTEL_CONSOLE_EXPORT: bool = Field(default=False, validation_alias="OTEL_CONSOLE_EXPORT")

    # MCP Server
    MCP_AGENT_API_KEY: str = Field(default="harmonix360-mcp-dev-key", validation_alias="MCP_AGENT_API_KEY")
    MCP_SERVER_PORT: int = Field(default=8100, validation_alias="MCP_SERVER_PORT")
    
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
