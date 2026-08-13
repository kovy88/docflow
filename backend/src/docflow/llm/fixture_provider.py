"""Deterministic local provider — no network, no API key, no cost.

Two jobs:

1. **Replay.** Recorded provider responses, keyed by a hash of the request, so an
   integration test or a regression check can exercise the exact bytes a real model
   returned without spending money or depending on the network.
2. **Heuristic fallback.** When there is no recording, run the rule-based baseline
   extractor and return its output in the shape a provider response would take.

## Honesty guarantee

This provider is **not an LLM and never pretends to be one.** Every extraction it
produces is stamped `provider="fixture"` and `model="fixture-heuristic"` (or
`fixture-replay`) in the database, in the API response and in the UI, and its cost
is genuinely zero because no call is made. Evaluation reports label it explicitly.

That matters because the whole project's credibility rests on not fabricating
measurements. A demo that runs without an API key is useful; a demo that lets a
deterministic regex parser be mistaken for model output would be a lie told by
architecture. The type stamped on the row is what prevents it.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import structlog

from docflow.extraction.baseline import extract_baseline
from docflow.llm.base import LLMProvider, LLMRequest, LLMResponse
from docflow.llm.pricing import estimate_tokens

logger = structlog.get_logger(__name__)

# Marker the extractor reads back out of `LLMRequest.metadata` so the heuristic
# knows which rule set to apply. Real providers ignore it.
DOCUMENT_TYPE_KEY = "document_type"
SOURCE_TEXT_KEY = "source_text"


class FixtureProvider(LLMProvider):
    name = "fixture"

    def __init__(
        self,
        *,
        fixture_dir: Path | None = None,
        allow_heuristic: bool = True,
        simulated_latency_ms: int = 0,
    ) -> None:
        self._dir = fixture_dir
        self._allow_heuristic = allow_heuristic
        self._latency_ms = simulated_latency_ms
        self._memory: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ recording

    def record(self, request: LLMRequest, data: dict[str, Any]) -> None:
        """Register an in-memory response. Used by tests to script exact output."""
        self._memory[fixture_key(request)] = data

    # -------------------------------------------------------------------- reading

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        key = fixture_key(request)

        payload = self._memory.get(key) or self._load_from_disk(key)
        model = "fixture-replay"

        if payload is None:
            if not self._allow_heuristic:
                from docflow.domain.errors import ProviderNotConfiguredError

                raise ProviderNotConfiguredError(
                    "No fixture recorded for this request and heuristic fallback is disabled",
                    detail={"fixture_key": key},
                )
            payload = self._heuristic(request)
            model = "fixture-heuristic"

        if self._latency_ms:
            import asyncio

            await asyncio.sleep(self._latency_ms / 1000)

        raw = json.dumps(payload, ensure_ascii=False, default=str)
        return LLMResponse(
            data=payload,
            provider=self.name,
            model=model,
            model_version=model,
            # Token counts are estimates so that the usage dashboard has plausible
            # shape in a demo. Cost stays exactly zero — no call was made, so
            # inventing a cost would be inventing a metric.
            input_tokens=estimate_tokens(request.system) + estimate_tokens(request.user_content),
            output_tokens=estimate_tokens(raw),
            cost_usd=__import__("decimal").Decimal("0"),
            latency_ms=self._elapsed_ms(started),
            stop_reason="end_turn",
            raw_text=raw,
        )

    def _load_from_disk(self, key: str) -> dict[str, Any] | None:
        if self._dir is None:
            return None
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("fixture.unreadable", path=str(path))
            return None

    def _heuristic(self, request: LLMRequest) -> dict[str, Any]:
        document_type = request.metadata.get(DOCUMENT_TYPE_KEY, "generic")
        source = request.metadata.get(SOURCE_TEXT_KEY) or request.user_content
        result = extract_baseline(source, document_type)
        logger.debug(
            "fixture.heuristic_used",
            document_type=document_type,
            fields_found=result.field_count,
        )
        return result.data

    async def health_check(self) -> bool:
        return True


def fixture_key(request: LLMRequest) -> str:
    """Stable content hash of the parts of a request that determine its answer.

    Deliberately excludes timeouts and token limits: changing `max_output_tokens`
    should not invalidate a recording, because it does not change what the correct
    answer is.
    """
    digest = hashlib.sha256()
    digest.update(request.schema_name.encode())
    digest.update(b"\x00")
    digest.update(request.system.encode())
    digest.update(b"\x00")
    digest.update(request.user_content.encode())
    return digest.hexdigest()[:32]
