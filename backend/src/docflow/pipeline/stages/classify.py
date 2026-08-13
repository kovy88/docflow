"""Classification and schema selection stages."""

from __future__ import annotations

import structlog

from docflow.config import LLMSettings
from docflow.documents.classification import (
    CLASSIFICATION_SCHEMA,
    ClassificationResult,
    build_llm_candidates,
    classify_heuristic,
    truncate_for_classification,
)
from docflow.domain.enums import ProcessingStage
from docflow.domain.errors import AIError, ProviderError
from docflow.llm.base import LLMProvider, LLMRequest
from docflow.pipeline.context import PipelineContext
from docflow.pipeline.stage import Stage
from docflow.prompts import extraction as prompts
from docflow.schemas.registry import SchemaRegistry

logger = structlog.get_logger(__name__)


class ClassificationStage(Stage):
    """Decide the document type, cheaply if possible.

    Order of resolution:
      1. The caller told us (`?document_type=invoice`) — trust it, charge nothing.
      2. The deterministic keyword classifier is confident — take it, charge nothing.
      3. Ask the model on a truncated sample.

    A failure of step 3 is not fatal: falling back to the heuristic's best guess
    with its own (low) confidence keeps the document moving, and the low confidence
    routes it to review, which is where an unclassifiable document belongs anyway.
    """

    stage = ProcessingStage.CLASSIFICATION

    def __init__(
        self,
        registry: SchemaRegistry,
        provider: LLMProvider,
        settings: LLMSettings,
    ) -> None:
        self._registry = registry
        self._provider = provider
        self._settings = settings

    async def run(self, ctx: PipelineContext) -> None:
        specs = self._registry.classifiable_specs(str(ctx.organization_id))

        if ctx.requested_type_key:
            try:
                self._registry.resolve(ctx.requested_type_key, str(ctx.organization_id))
            except Exception:  # noqa: BLE001 — unknown type falls through to detection
                logger.info("classification.unknown_requested_type", requested=ctx.requested_type_key)
            else:
                ctx.classification = ClassificationResult(
                    document_type_key=ctx.requested_type_key,
                    confidence=1.0,
                    method="explicit",
                    scores={},
                )
                return

        heuristic = classify_heuristic(ctx.document_text, specs)
        ctx.classification = heuristic

        if not self._settings.classification_enabled:
            return
        if heuristic.confidence >= self._settings.classification_llm_threshold:
            return

        try:
            ctx.classification = await self._classify_with_model(ctx, specs, heuristic)
        except (AIError, ProviderError) as exc:
            logger.warning(
                "classification.llm_failed",
                error_code=exc.code,
                falling_back_to=heuristic.document_type_key,
            )
            ctx.add_review_reason("Automatic type detection was inconclusive")

    async def _classify_with_model(
        self, ctx: PipelineContext, specs: list, heuristic: ClassificationResult
    ) -> ClassificationResult:
        from docflow.llm.fixture_provider import DOCUMENT_TYPE_KEY, SOURCE_TEXT_KEY

        nonce = prompts.new_nonce()
        request = LLMRequest(
            system=prompts.CLASSIFICATION_SYSTEM.template,
            user_content=prompts.CLASSIFICATION_USER.render(
                candidates=build_llm_candidates(specs),
                nonce=nonce,
                document_text=truncate_for_classification(ctx.document_text),
            ),
            json_schema=CLASSIFICATION_SCHEMA,
            schema_name="document_classification",
            # Classification is a single label choice. Reasoning depth buys nothing
            # here, and `low` is materially cheaper and faster.
            effort="low",
            max_output_tokens=512,
            timeout_seconds=self._settings.timeout_seconds,
            metadata={DOCUMENT_TYPE_KEY: "generic", SOURCE_TEXT_KEY: ctx.document_text},
        )

        response = await self._provider.complete_structured(request)
        ctx.llm_calls += 1
        ctx.total_cost_usd += response.cost_usd
        ctx.total_input_tokens += response.input_tokens
        ctx.total_output_tokens += response.output_tokens

        key = str(response.data.get("document_type") or heuristic.document_type_key)
        try:
            confidence = float(response.data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        valid_keys = {s.key for s in specs}
        if key not in valid_keys:
            logger.warning("classification.llm_returned_unknown_type", returned=key)
            return ClassificationResult("generic", 0.3, "llm", heuristic.scores)

        return ClassificationResult(
            document_type_key=key,
            confidence=max(0.0, min(1.0, confidence)),
            method="llm",
            scores=heuristic.scores,
            runner_up=heuristic.document_type_key if heuristic.document_type_key != key else None,
        )

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        if ctx.classification is None:
            return {}
        return {
            "document_type": ctx.classification.document_type_key,
            "confidence": ctx.classification.confidence,
            "method": ctx.classification.method,
        }


class SchemaSelectionStage(Stage):
    """Resolve the document type key to a concrete spec."""

    stage = ProcessingStage.SCHEMA_SELECTION

    def __init__(self, registry: SchemaRegistry) -> None:
        self._registry = registry

    async def run(self, ctx: PipelineContext) -> None:
        key = ctx.classification.document_type_key if ctx.classification else None
        ctx.spec = self._registry.resolve_or_fallback(key, str(ctx.organization_id))

        if ctx.spec.key == "generic":
            ctx.add_review_reason(
                "The document type could not be determined, so only generic fields were extracted"
            )

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        if ctx.spec is None:
            return {}
        return {
            "schema": ctx.spec.schema_id(),
            "fields": len(ctx.spec.fields),
            "rules": len(ctx.spec.rule_ids),
        }
