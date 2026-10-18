from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./docflow.db"
    storage_dir: Path = Path("./storage")
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen3.8:27b"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    llm_timeout_s: float = 600
    max_upload_mb: int = 20
    low_confidence: float = 0.6


settings = Settings()
