"""Provider construction and lifecycle.

One place decides which provider the process uses, based on configuration. Nothing
else in the codebase branches on provider name — if you find such a branch, it
belongs behind the `LLMProvider` interface instead.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from docflow.config import LLMSettings, get_settings
from docflow.domain.errors import ProviderNotConfiguredError
from docflow.llm.base import LLMProvider

logger = structlog.get_logger(__name__)

_provider: LLMProvider | None = None


def build_provider(settings: LLMSettings) -> LLMProvider:
    if settings.provider == "anthropic":
        from docflow.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.model,
            max_retries=0,  # retries are owned by the pipeline, not the SDK
        )

    if settings.provider == "openai":
        from docflow.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.model,
            max_retries=0,
        )

    if settings.provider == "google":
        from docflow.llm.google_provider import GeminiProvider

        return GeminiProvider(
            api_key=settings.google_api_key,
            model=settings.model,
            max_retries=0,
            timeout_seconds=settings.timeout_seconds,
        )

    if settings.provider == "fixture":
        from docflow.llm.fixture_provider import FixtureProvider

        fixture_dir = Path(__file__).resolve().parents[3] / "fixtures" / "llm"
        return FixtureProvider(
            fixture_dir=fixture_dir if fixture_dir.exists() else None,
            allow_heuristic=True,
        )

    raise ProviderNotConfiguredError(f"Unknown LLM provider: {settings.provider!r}")


def get_provider() -> LLMProvider:
    """Process-wide provider singleton.

    A singleton because provider clients hold an HTTP connection pool; building one
    per request would defeat keep-alive and add a TLS handshake to every extraction.
    """
    global _provider
    if _provider is None:
        settings = get_settings().llm
        _provider = build_provider(settings)
        logger.info(
            "llm.provider_initialised",
            provider=_provider.name,
            model=settings.model,
        )
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Override the singleton. Used by tests and by the evaluation harness."""
    global _provider
    _provider = provider


async def close_provider() -> None:
    global _provider
    if _provider is not None:
        await _provider.aclose()
    _provider = None
