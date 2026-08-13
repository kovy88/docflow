"""LLM provider abstraction.

The application never imports a vendor SDK outside `docflow.llm.*`. Everything above
this layer speaks in `LLMRequest` / `LLMResponse`, which are provider-neutral.

Why this abstraction earns its place — it is not speculative generality:

1. **Outage isolation.** A provider incident is survivable by changing one env var
   rather than by shipping code.
2. **Cost control.** The same document can be run through a cheaper model when an
   organization's plan calls for it. That decision belongs in configuration.
3. **Evaluation.** The evaluation harness runs the identical pipeline across
   providers and against a deterministic fixture provider. Without a common
   interface, "is the new model better?" is not a question you can answer.
4. **Testing.** CI has no API key. `FixtureProvider` makes the whole pipeline
   testable end to end without one.

The interface is deliberately narrow — one method. A provider is not a place to put
business logic; it converts a request into a response and reports what it cost.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from docflow.domain.errors import ProviderError


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """A single structured-output call.

    `system` and `user_content` are kept separate because the trust boundary runs
    between them: system text is ours, user content contains untrusted document
    text. See `docflow.prompts` and `docs/SECURITY.md`.
    """

    system: str
    user_content: str
    json_schema: dict[str, Any]
    schema_name: str
    max_output_tokens: int = 4096
    # Provider-neutral reasoning-depth hint. Providers map it onto whatever their
    # API calls it (or ignore it).
    effort: str = "medium"
    timeout_seconds: float = 90.0
    # Only used by providers that still accept a sampling temperature. Current
    # Anthropic models reject it outright, so the Anthropic provider drops it.
    temperature: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponse:
    data: dict[str, Any]
    provider: str
    model: str
    # The model identifier the API actually served, which can differ from the one
    # requested (aliases, server-side fallbacks). Recorded for reproducibility.
    model_version: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    latency_ms: int = 0
    stop_reason: str | None = None
    raw_text: str | None = None
    attempts: int = 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMProvider(abc.ABC):
    """Interface every provider implements."""

    name: str = "abstract"

    @abc.abstractmethod
    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        """Return structured data matching `request.json_schema`.

        Implementations must translate provider-specific failures into the
        `docflow.domain.errors` taxonomy so that the retry policy above this layer
        works identically regardless of provider.
        """

    async def health_check(self) -> bool:
        """Cheap liveness probe used by `/readiness`. Never raises."""
        return True

    async def aclose(self) -> None:
        """Release connections. Called on application shutdown."""
        return

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _wrap_unexpected(self, exc: Exception) -> ProviderError:
        return ProviderError(
            f"{self.name} provider call failed",
            detail={"error": type(exc).__name__},
        )
