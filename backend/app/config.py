from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./docflow.db"
    db_schema: str | None = None  # Postgres only, e.g. "docflow"
    llm_provider: Literal["ollama", "openai", "openrouter"] = "ollama"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen3.8:27b"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    openrouter_api_key: str | None = None
    # The model that won the local evaluation, hosted (see README).
    openrouter_model: str = "qwen/qwen3.6-35b-a3b"
    llm_timeout_s: float = 600
    access_code: str | None = None  # required for documents and model calls when set
    pilot_mode: bool = False
    daily_llm_limit: int = 100
    cors_origins: str = "http://localhost:3000"  # comma-separated
    max_upload_mb: int = 20
    low_confidence: float = 0.6
    # Fuzzy match needed for a quote to count as found; OCR text has misread characters,
    # so its pages get a looser threshold (measured on the scan set, see README).
    quote_threshold: float = 0.9
    ocr_quote_threshold: float = 0.85

    @model_validator(mode="after")
    def pilot_requires_access(self):
        if self.pilot_mode and not (self.access_code and self.access_code.strip()):
            raise ValueError("PILOT_MODE requires a non-empty ACCESS_CODE")
        return self


settings = Settings()
# Hosting dashboards (Supabase, Render) hand out postgres:// or postgresql:// URLs; the
# installed driver is psycopg 3, so the URL can be pasted as is.
for prefix in ("postgres://", "postgresql://"):
    if settings.database_url.startswith(prefix):
        settings.database_url = "postgresql+psycopg://" + settings.database_url[len(prefix) :]
