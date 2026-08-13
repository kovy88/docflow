"""Anthropic provider.

Uses the Messages API with `output_config.format` structured outputs, which
constrains the response to the supplied JSON Schema. This is the whole reason the
extraction step is reliable enough to build validation on top of: the model cannot
return prose where an object was requested, so "the model wrote an apology instead
of JSON" is not a failure mode we have to handle.

Notes on the current API surface that are easy to get wrong:

* **No sampling parameters.** `temperature`, `top_p` and `top_k` are rejected by
  current models. `LLMRequest.temperature` is deliberately ignored here — the
  determinism it used to approximate now comes from structured outputs plus a low
  effort setting.
* **No `budget_tokens`.** Reasoning depth is controlled by `output_config.effort`.
* **No assistant prefill.** Structured outputs replace it.
* **`max_tokens` bounds thinking *and* response text**, so it is sized with
  headroom above the expected payload rather than tightly around it.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import structlog

from docflow.domain.errors import (
    MalformedModelOutputError,
    ModelRefusalError,
    OutputTruncatedError,
    ProviderAuthError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from docflow.llm.base import LLMProvider, LLMRequest, LLMResponse
from docflow.llm.pricing import estimate_cost
from docflow.llm.schema import normalize_schema

logger = structlog.get_logger(__name__)

# Effort maps straight through; anything unrecognised falls back to a safe middle.
_VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        max_retries: int = 2,
        base_url: str | None = None,
    ) -> None:
        if not api_key:
            raise ProviderNotConfiguredError(
                "DOCFLOW_LLM_ANTHROPIC_API_KEY is required for the Anthropic provider"
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProviderNotConfiguredError("The `anthropic` package is not installed") from exc

        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": max_retries}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        self._model = model

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        import anthropic

        schema = normalize_schema(request.json_schema)
        effort = request.effort if request.effort in _VALID_EFFORTS else "medium"
        started = time.perf_counter()

        try:
            message = await self._client.with_options(
                timeout=request.timeout_seconds
            ).messages.create(
                model=self._model,
                max_tokens=request.max_output_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user_content}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": schema,
                    },
                    "effort": effort,
                },
            )
        except anthropic.AuthenticationError as exc:
            raise ProviderAuthError("Anthropic rejected the API key") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderAuthError("Anthropic API key lacks access to this model") from exc
        except anthropic.RateLimitError as exc:
            retry_after = _retry_after(exc)
            raise ProviderRateLimitError(
                "Anthropic rate limit reached",
                detail={"retry_after_seconds": retry_after},
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError("Anthropic request timed out") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Could not reach the Anthropic API") from exc
        except anthropic.APIStatusError as exc:
            # 5xx is transient; 4xx means we built a bad request and retrying it
            # will produce the same bad request.
            if exc.status_code >= 500:
                raise ProviderError(
                    "Anthropic returned a server error",
                    detail={"status": exc.status_code},
                ) from exc
            raise ProviderNotConfiguredError(
                "Anthropic rejected the request",
                detail={"status": exc.status_code, "type": getattr(exc, "type", None)},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_unexpected(exc) from exc

        latency_ms = self._elapsed_ms(started)
        stop_reason = getattr(message, "stop_reason", None)

        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise ModelRefusalError(
                "The model declined to process this document",
                detail={"category": getattr(details, "category", None)},
            )
        if stop_reason == "max_tokens":
            raise OutputTruncatedError(
                "The model hit the output token limit before completing the extraction",
                detail={"max_output_tokens": request.max_output_tokens},
            )

        text = _first_text_block(message)
        if not text:
            raise MalformedModelOutputError("The model returned no text content")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MalformedModelOutputError(
                "The model returned output that is not valid JSON",
                detail={"position": exc.pos},
            ) from exc

        if not isinstance(data, dict):
            raise MalformedModelOutputError(
                f"Expected a JSON object, got {type(data).__name__}"
            )

        usage = getattr(message, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        served_model = getattr(message, "model", None) or self._model

        return LLMResponse(
            data=data,
            provider=self.name,
            model=self._model,
            model_version=served_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            cost_usd=estimate_cost(
                served_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached,
            ),
            latency_ms=latency_ms,
            stop_reason=stop_reason,
            raw_text=text,
        )

    async def health_check(self) -> bool:
        try:
            await self._client.models.retrieve(self._model)
        except Exception:  # noqa: BLE001 — a probe must never raise
            logger.warning("llm.health_check_failed", provider=self.name, model=self._model)
            return False
        return True

    async def aclose(self) -> None:
        await self._client.close()


def _first_text_block(message: Any) -> str | None:
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", None)
    return None


def _retry_after(exc: Any) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


__all__ = ["AnthropicProvider", "Decimal"]
