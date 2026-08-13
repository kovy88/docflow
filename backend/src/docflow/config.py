"""Application configuration.

All configuration is environment-driven (12-factor). Secrets never live in code or in
version control; `.env.example` documents every key with a safe placeholder.

Settings are grouped by concern rather than kept in one flat blob, so that a component
can depend on the narrow slice it actually needs (e.g. the storage layer takes
`StorageSettings`, not the whole application config). That keeps unit tests cheap and
makes the dependency graph obvious.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


_BASE_CONFIG = SettingsConfigDict(
    env_file=(".env", "../.env"),
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=False,
)


def _config(prefix: str = "") -> SettingsConfigDict:
    """Base settings config with an env-var prefix applied.

    `_Base.model_config` cannot simply be splatted into a new `SettingsConfigDict`
    because pydantic-settings has already populated every key with a default,
    including `env_prefix` — passing it again is a duplicate-keyword error.
    """
    return SettingsConfigDict(**{**_BASE_CONFIG, "env_prefix": prefix})


class _Base(BaseSettings):
    model_config = _BASE_CONFIG


class DatabaseSettings(_Base):
    model_config = _config("DOCFLOW_DB_")

    url: PostgresDsn = Field(
        default="postgresql+asyncpg://docflow:docflow@localhost:5433/docflow"  # type: ignore[arg-type]
    )
    pool_size: int = 10
    max_overflow: int = 10
    pool_pre_ping: bool = True
    echo: bool = False
    statement_timeout_ms: int = 30_000

    @property
    def sync_url(self) -> str:
        """Alembic runs synchronously; strip the async driver."""
        return str(self.url).replace("+asyncpg", "+psycopg2").replace("+asyncpg", "")


class RedisSettings(_Base):
    model_config = _config("DOCFLOW_REDIS_")

    url: RedisDsn = Field(default="redis://localhost:6380/0")  # type: ignore[arg-type]
    # Separate logical DB for the queue keeps cache flushes from nuking pending jobs.
    queue_db: int = 1


class StorageSettings(_Base):
    model_config = _config("DOCFLOW_STORAGE_")

    backend: Literal["local", "s3"] = "local"
    local_root: str = "./.data/storage"

    # S3-compatible (AWS S3, Cloudflare R2, MinIO, Supabase Storage S3 endpoint)
    bucket: str = "docflow"
    endpoint_url: str | None = None
    region: str = "auto"
    access_key_id: str | None = None
    secret_access_key: str | None = None
    presign_ttl_seconds: int = 300


class SecuritySettings(_Base):
    model_config = _config("DOCFLOW_SECURITY_")

    # MUST be overridden outside local/test. Startup refuses to boot in production
    # with the default (see `Settings.validate_for_environment`).
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 30  # 30 min
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 120
    rate_limit_upload_per_minute: int = 30

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


class UploadSettings(_Base):
    model_config = _config("DOCFLOW_UPLOAD_")

    max_bytes: int = 20 * 1024 * 1024  # 20 MB
    max_pages: int = 50
    allowed_mime_types: list[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/tiff",
            "image/webp",
            "text/plain",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ]
    )

    @field_validator("allowed_mime_types", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


class LLMSettings(_Base):
    model_config = _config("DOCFLOW_LLM_")

    provider: Literal["anthropic", "openai", "fixture"] = "fixture"
    model: str = "claude-opus-5"
    # Reasoning depth. Current Anthropic models reject `temperature`/`top_p`, so
    # this is the knob that replaces them. `medium` is the default because
    # extraction from clean documents does not need deep reasoning, while messy
    # scans and unusual layouts measurably benefit from some. See docs/AI.md.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # Only used by providers that still accept a sampling temperature (OpenAI).
    # The Anthropic provider drops it — passing it would be a 400.
    temperature: float = 0.0
    # Bounds reasoning tokens *and* response text together, so this is sized with
    # headroom rather than tightly around the expected payload.
    max_output_tokens: int = 8192
    timeout_seconds: float = 90.0
    max_attempts: int = 3

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # Hard ceiling per document; a runaway retry loop must not be able to bill
    # an organisation for an unbounded amount.
    max_cost_usd_per_document: float = 0.50

    # Truncation guard: documents are chunked/truncated to this many characters
    # before hitting the model. Cost is roughly linear in input size.
    max_input_chars: int = 120_000

    classification_enabled: bool = True
    # Try the cheap deterministic classifier first and only escalate to the LLM
    # when its confidence is below this.
    classification_llm_threshold: float = 0.65


class ProcessingSettings(_Base):
    model_config = _config("DOCFLOW_PROCESSING_")

    max_attempts: int = 3
    retry_base_delay_seconds: float = 2.0
    retry_max_delay_seconds: float = 60.0
    job_timeout_seconds: int = 300
    worker_concurrency: int = 8

    ocr_enabled: bool = True
    # If native text extraction yields fewer than this many characters per page,
    # treat the page as scanned and route it to OCR.
    ocr_chars_per_page_threshold: int = 120
    ocr_dpi: int = 300
    ocr_language: str = "eng"

    # Documents whose overall confidence is below this always go to human review,
    # regardless of validation outcome.
    review_confidence_threshold: float = 0.85


class ObservabilitySettings(_Base):
    model_config = _config("DOCFLOW_OBS_")

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    metrics_enabled: bool = True
    # Emitting extracted values into logs would leak customer business data.
    log_document_content: bool = False


class Settings(_Base):
    model_config = _config("DOCFLOW_")

    environment: Environment = "local"
    app_name: str = "Docflow"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    public_base_url: str = "http://localhost:8000"

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_for_environment(self) -> list[str]:
        """Return a list of fatal misconfigurations for the current environment.

        Called at startup. In production these abort the boot rather than
        silently running with insecure defaults — a service that starts with a
        default signing key is worse than one that refuses to start.
        """
        problems: list[str] = []
        if not self.is_production:
            return problems

        if self.security.jwt_secret.startswith("dev-only"):
            problems.append("DOCFLOW_SECURITY_JWT_SECRET must be set in production")
        if len(self.security.jwt_secret) < 32:
            problems.append("DOCFLOW_SECURITY_JWT_SECRET must be at least 32 characters")
        if self.storage.backend == "local":
            problems.append("Local filesystem storage is not supported in production")
        if self.llm.provider == "fixture":
            problems.append("The fixture LLM provider must not be used in production")
        if self.llm.provider == "anthropic" and not self.llm.anthropic_api_key:
            problems.append("DOCFLOW_LLM_ANTHROPIC_API_KEY is required")
        if self.llm.provider == "openai" and not self.llm.openai_api_key:
            problems.append("DOCFLOW_LLM_OPENAI_API_KEY is required")
        if any(o == "*" for o in self.security.cors_origins):
            problems.append("Wildcard CORS origin is not allowed in production")
        if self.debug:
            problems.append("DOCFLOW_DEBUG must be false in production")
        return problems


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that every `Depends(get_settings)` resolves to the same object;
    tests clear the cache via `get_settings.cache_clear()`.
    """
    return Settings()
