"""Google Gemini provider.

Uses `response_mime_type="application/json"` + `response_json_schema` for
structured output. This is deliberately *not* the `response_schema` path,
which wants Gemini's own OpenAPI-subset `Schema` dialect (uppercase type
enums, no `$ref`). `response_json_schema` accepts a real JSON Schema subset
($defs, $ref, anyOf, properties, required, additionalProperties) — exactly
what `normalize_schema()` already produces for the Anthropic path. No second
schema dialect to build or maintain.

Notes on the API surface that are easy to get wrong:

* **Error taxonomy is coarser than Anthropic/OpenAI's.** The SDK raises only
  `ClientError` (any 4xx) and `ServerError` (any 5xx) — there is no distinct
  `AuthenticationError`/`RateLimitError`. Branch on `exc.code` (the HTTP
  status) instead of catching separate exception types.
* **`finish_reason` covers several distinct safety-block reasons** (SAFETY,
  RECITATION, PROHIBITED_CONTENT, BLOCKLIST, SPII, IMAGE_SAFETY, LANGUAGE),
  all mapped to `ModelRefusalError` — the same collapse Anthropic's single
  `refusal` stop reason gets.
* **A fully-blocked prompt returns zero candidates**, not a candidate with a
  refusal finish reason — checked separately, via `prompt_feedback`.
"""

from __future__ import annotations

import json
import time

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

_REFUSAL_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "LANGUAGE",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
    }
)


class GeminiProvider(LLMProvider):
    name = "google"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        max_retries: int = 2,
        timeout_seconds: float = 90.0,
    ) -> None:
        if not api_key:
            raise ProviderNotConfiguredError(
                "DOCFLOW_LLM_GOOGLE_API_KEY is required for the Google provider"
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProviderNotConfiguredError("The `google-genai` package is not installed") from exc

        self._types = types
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=max_retries + 1),
            ),
        )
        self._model = model

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        from google.genai import errors as genai_errors

        types = self._types
        schema = normalize_schema(request.json_schema)
        started = time.perf_counter()

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=request.user_content,
                config=types.GenerateContentConfig(
                    system_instruction=request.system,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    max_output_tokens=request.max_output_tokens,
                ),
            )
        except genai_errors.ClientError as exc:
            if exc.code in (401, 403):
                raise ProviderAuthError("Google rejected the API key") from exc
            if exc.code == 429:
                raise ProviderRateLimitError("Google rate limit reached") from exc
            raise ProviderNotConfiguredError(
                "Google rejected the request", detail={"status": exc.code}
            ) from exc
        except genai_errors.ServerError as exc:
            raise ProviderError(
                "Google returned a server error", detail={"status": exc.code}
            ) from exc
        except TimeoutError as exc:
            raise ProviderTimeoutError("Google request timed out") from exc
        except Exception as exc:
            raise self._wrap_unexpected(exc) from exc

        latency_ms = self._elapsed_ms(started)

        candidates = response.candidates or []
        if not candidates:
            # A prompt blocked before generation starts yields zero candidates,
            # not a candidate carrying a refusal finish_reason.
            feedback = getattr(response, "prompt_feedback", None)
            raise ModelRefusalError(
                "The model declined to process this document",
                detail={"block_reason": str(getattr(feedback, "block_reason", None))},
            )

        # `finish_reason` is an Enum whose default `str()` gives the qualified
        # form ("FinishReason.STOP", not "STOP") — `.value` is the plain string
        # both branches below actually compare against.
        finish_reason_raw = getattr(candidates[0], "finish_reason", None)
        finish_reason = getattr(finish_reason_raw, "value", None) or str(finish_reason_raw or "")
        if finish_reason in _REFUSAL_FINISH_REASONS:
            raise ModelRefusalError(
                "The model declined to process this document",
                detail={"finish_reason": finish_reason},
            )
        if finish_reason == "MAX_TOKENS":
            raise OutputTruncatedError(
                "The model hit the output token limit before completing the extraction",
                detail={"max_output_tokens": request.max_output_tokens},
            )

        text = response.text
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
            raise MalformedModelOutputError(f"Expected a JSON object, got {type(data).__name__}")

        usage = response.usage_metadata
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        cached = int(getattr(usage, "cached_content_token_count", 0) or 0)

        return LLMResponse(
            data=data,
            provider=self.name,
            model=self._model,
            model_version=getattr(response, "model_version", None) or self._model,
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
            stop_reason=finish_reason or None,
            raw_text=text,
        )

    async def health_check(self) -> bool:
        try:
            await self._client.aio.models.get(model=self._model)
        except Exception:
            logger.warning("llm.health_check_failed", provider=self.name, model=self._model)
            return False
        return True

    async def aclose(self) -> None:
        # The SDK's async client owns its own httpx transport internally and
        # exposes no explicit close — nothing to release here.
        return


__all__ = ["GeminiProvider"]
