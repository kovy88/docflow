"""OpenAI provider.

Present to prove the abstraction is real. A provider interface with exactly one
implementation is a claim, not a design — the second implementation is what forces
the leaks out (schema strictness rules differ, error taxonomies differ, sampling
parameters are accepted here and rejected on Anthropic).

Uses Chat Completions with `response_format={"type": "json_schema", strict: true}`.
Strict mode requires every property to appear in `required`, so the schema goes
through `require_all_properties` + `ensure_nullable`: fields the model cannot find
come back as explicit `null` rather than being omitted.
"""

from __future__ import annotations

import json
import time
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
from docflow.llm.schema import ensure_nullable, normalize_schema

logger = structlog.get_logger(__name__)


class OpenAIProvider(LLMProvider):
    name = "openai"

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
                "DOCFLOW_LLM_OPENAI_API_KEY is required for the OpenAI provider"
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise ProviderNotConfiguredError("The `openai` package is not installed") from exc

        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": max_retries}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._model = model

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        import openai

        schema = ensure_nullable(
            normalize_schema(request.json_schema, require_all_properties=True)
        )
        started = time.perf_counter()

        try:
            completion = await self._client.chat.completions.create(
                model=self._model,
                max_completion_tokens=request.max_output_tokens,
                temperature=request.temperature,
                timeout=request.timeout_seconds,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user_content},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
        except openai.AuthenticationError as exc:
            raise ProviderAuthError("OpenAI rejected the API key") from exc
        except openai.PermissionDeniedError as exc:
            raise ProviderAuthError("OpenAI API key lacks access to this model") from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError("OpenAI rate limit reached") from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError("OpenAI request timed out") from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("Could not reach the OpenAI API") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderError(
                    "OpenAI returned a server error", detail={"status": exc.status_code}
                ) from exc
            raise ProviderNotConfiguredError(
                "OpenAI rejected the request", detail={"status": exc.status_code}
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._wrap_unexpected(exc) from exc

        latency_ms = self._elapsed_ms(started)
        choice = completion.choices[0]
        finish_reason = choice.finish_reason

        if getattr(choice.message, "refusal", None):
            raise ModelRefusalError(
                "The model declined to process this document",
                detail={"refusal": str(choice.message.refusal)[:200]},
            )
        if finish_reason == "length":
            raise OutputTruncatedError(
                "The model hit the output token limit before completing the extraction"
            )

        content = choice.message.content
        if not content:
            raise MalformedModelOutputError("The model returned no content")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise MalformedModelOutputError(
                "The model returned output that is not valid JSON",
                detail={"position": exc.pos},
            ) from exc
        if not isinstance(data, dict):
            raise MalformedModelOutputError(f"Expected a JSON object, got {type(data).__name__}")

        usage = completion.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cached = int(
            getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        )

        return LLMResponse(
            data=data,
            provider=self.name,
            model=self._model,
            model_version=completion.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            cost_usd=estimate_cost(
                self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached,
            ),
            latency_ms=latency_ms,
            stop_reason=finish_reason,
            raw_text=content,
        )

    async def health_check(self) -> bool:
        try:
            await self._client.models.retrieve(self._model)
        except Exception:  # noqa: BLE001
            logger.warning("llm.health_check_failed", provider=self.name, model=self._model)
            return False
        return True

    async def aclose(self) -> None:
        await self._client.close()
