import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# The .env lives at the REPO ROOT, but local commands run from
# harmonix360/backend/ (alembic, pytest, uvicorn, the verify_* scripts). A bare
# env_file=".env" resolves against the CWD, so it silently found nothing and
# every setting fell back to its default â€” including DATABASE_URL's
# "@postgres:5432", a hostname that only resolves inside docker compose. Anchor
# the path to this file instead so it works from any working directory.
#
# Both locations are listed; a backend-local .env (if anyone adds one) wins over
# the repo-root file. OS environment variables still take precedence over both,
# so docker compose's `environment:` block continues to override this unchanged.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
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
    
    # Security
    JWT_SECRET: str = Field(default="harmonix360-dev-jwt-secret-change-in-production", validation_alias="JWT_SECRET")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # AI Providers
    GROQ_API_KEY: str = Field(default="", validation_alias="GROQ_API_KEY")
    CEREBRAS_API_KEY: str = Field(default="", validation_alias="CEREBRAS_API_KEY")
    GROQ_MODEL: str = Field(default="llama-3.1-8b-instant", validation_alias="GROQ_MODEL")
    CEREBRAS_MODEL: str = Field(default="llama3.1-8b", validation_alias="CEREBRAS_MODEL")
    AI_CACHE_DEFAULT_TTL: int = Field(default=600, validation_alias="AI_CACHE_DEFAULT_TTL")
    AI_PROVIDER_TIMEOUT: int = Field(default=30, validation_alias="AI_PROVIDER_TIMEOUT")
    # Rate limits (requests per minute per provider)
    GROQ_RPM: int = Field(default=30, validation_alias="GROQ_RPM")
    CEREBRAS_RPM: int = Field(default=30, validation_alias="CEREBRAS_RPM")

    # Observability
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(default="http://signoz-otel-collector:4318", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    SENTRY_DSN: str = Field(default="", validation_alias="SENTRY_DSN")
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
