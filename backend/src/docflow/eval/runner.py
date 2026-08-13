"""Evaluation runner.

Runs the **real pipeline components** against the labelled corpus — the same
classifier, extractor, validation engine and confidence scorer the product uses.
An evaluation harness that reimplements the logic it measures reports on code
nobody ships.

Storage and the queue are bypassed (documents are already text), which is the only
shortcut taken and does not affect any measured quantity.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal

import structlog

from docflow.config import LLMSettings, ProcessingSettings, Settings, get_settings
from docflow.documents.classification import classify_heuristic
from docflow.domain.confidence import (
    ConfidenceSignals,
    aggregate,
    grounding_score,
    normalise_for_matching,
    score_field,
)
from docflow.eval.dataset import GroundTruth
from docflow.eval.metrics import DocumentOutcome, EvaluationReport, build_field_outcomes
from docflow.extraction.baseline import extract_baseline
from docflow.extraction.extractor import LLMExtractor
from docflow.llm.base import LLMProvider
from docflow.schemas.base import FieldKind
from docflow.schemas.registry import SchemaRegistry, get_registry
from docflow.validation.engine import RuleContext, ValidationEngine, validate_syntax
from docflow.validation.paths import flatten, to_template

logger = structlog.get_logger(__name__)


@dataclass
class RunnerConfig:
    label: str
    concurrency: int = 4
    classify: bool = True
    score_confidence: bool = True


class BaselineRunner:
    """Evaluates the rule-based extractor. No network, no cost."""

    def __init__(self, registry: SchemaRegistry | None = None) -> None:
        self._registry = registry or get_registry()
        self._validator = ValidationEngine()

    async def run(
        self, corpus: list[GroundTruth], *, config: RunnerConfig | None = None
    ) -> EvaluationReport:
        config = config or RunnerConfig(label="baseline (rules)")
        started = time.perf_counter()
        report = EvaluationReport(
            label=config.label,
            extractor="baseline",
            provider="none",
            model="rule-based",
            prompt_version="n/a",
            corpus_size=len(corpus),
        )

        for item in corpus:
            report.documents.append(self._evaluate(item, config))

        report.wall_clock_seconds = time.perf_counter() - started
        return report

    def _evaluate(self, item: GroundTruth, config: RunnerConfig) -> DocumentOutcome:
        clock = time.perf_counter()

        predicted_type = item.document_type
        if config.classify:
            predicted_type = classify_heuristic(
                item.text, self._registry.classifiable_specs()
            ).document_type_key

        spec = self._registry.resolve_or_fallback(predicted_type)
        result = extract_baseline(item.text, spec.key, day_first=spec.day_first_dates)

        normalized, syntax_issues = validate_syntax(spec, result.data)
        data = normalized if normalized is not None else result.data
        issues = list(syntax_issues)
        if normalized is not None:
            issues.extend(
                self._validator.validate(
                    RuleContext(data=normalized, spec=spec, source_text=item.text)
                ).issues
            )

        latency_ms = int((time.perf_counter() - clock) * 1000)
        # Compare against the *ground-truth* spec even when classification was
        # wrong. Grading a misclassified document against the schema it was
        # mistakenly given would hide the misclassification entirely.
        truth_spec = self._registry.resolve_or_fallback(item.document_type)

        return DocumentOutcome(
            document_id=item.document_id,
            document_type=item.document_type,
            predicted_type=predicted_type,
            fields=build_field_outcomes(
                document_id=item.document_id,
                spec=truth_spec,
                expected=item.fields,
                actual=data,
            ),
            latency_ms=latency_ms,
            cost_usd=Decimal("0"),
            validation_errors=sum(1 for i in issues if i.severity.value == "error"),
            needs_review=any(i.severity.value == "error" for i in issues),
            difficulty=item.difficulty,
        )


class ExtractorRunner:
    """Evaluates the LLM extraction path, including validation and confidence."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        settings: Settings | None = None,
        registry: SchemaRegistry | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._registry = registry or get_registry()
        self._validator = ValidationEngine()
        self._extractor = LLMExtractor(provider, self._settings.llm)

    async def run(
        self, corpus: list[GroundTruth], *, config: RunnerConfig | None = None
    ) -> EvaluationReport:
        config = config or RunnerConfig(label=f"{self._provider.name}/{self._settings.llm.model}")
        started = time.perf_counter()

        report = EvaluationReport(
            label=config.label,
            extractor="llm",
            provider=self._provider.name,
            model=self._settings.llm.model,
            prompt_version="v1",
            corpus_size=len(corpus),
        )

        # Bounded concurrency: enough to keep provider round trips overlapping,
        # low enough not to trip a rate limit and turn the run into a retry storm.
        semaphore = asyncio.Semaphore(config.concurrency)

        async def guarded(item: GroundTruth) -> DocumentOutcome:
            async with semaphore:
                return await self._evaluate(item, config)

        report.documents = list(
            await asyncio.gather(*(guarded(item) for item in corpus))
        )
        report.wall_clock_seconds = time.perf_counter() - started
        return report

    async def _evaluate(self, item: GroundTruth, config: RunnerConfig) -> DocumentOutcome:
        clock = time.perf_counter()
        truth_spec = self._registry.resolve_or_fallback(item.document_type)

        predicted_type = item.document_type
        if config.classify:
            predicted_type = classify_heuristic(
                item.text, self._registry.classifiable_specs()
            ).document_type_key
        spec = self._registry.resolve_or_fallback(predicted_type)

        try:
            outcome = await self._extractor.extract(
                spec=spec, document_text=item.text, page_count=1
            )
        except Exception as exc:  # noqa: BLE001 — one bad document must not end the run
            logger.warning(
                "eval.document_failed",
                document_id=item.document_id,
                error=type(exc).__name__,
            )
            return DocumentOutcome(
                document_id=item.document_id,
                document_type=item.document_type,
                predicted_type=predicted_type,
                failed=True,
                error_code=getattr(exc, "code", type(exc).__name__),
                latency_ms=int((time.perf_counter() - clock) * 1000),
                difficulty=item.difficulty,
            )

        issues = outcome.issues
        confidences: dict[str, tuple[float | None, str | None]] = {}
        needs_review = any(i.severity.value == "error" for i in issues)

        if config.score_confidence:
            confidences, overall = self._score(outcome.data, spec, item.text, issues)
            needs_review = needs_review or (
                overall is not None and overall < spec.review_threshold
            )

        return DocumentOutcome(
            document_id=item.document_id,
            document_type=item.document_type,
            predicted_type=predicted_type,
            fields=build_field_outcomes(
                document_id=item.document_id,
                spec=truth_spec,
                expected=item.fields,
                actual=outcome.data,
                confidences=confidences,
            ),
            latency_ms=int((time.perf_counter() - clock) * 1000),
            cost_usd=outcome.cost_usd,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            needs_review=needs_review,
            validation_errors=sum(1 for i in issues if i.severity.value == "error"),
            difficulty=item.difficulty,
        )

    def _score(self, data, spec, text, issues):
        """Reuses the production confidence signals so calibration is measured, not modelled."""
        source_normalised = normalise_for_matching(text)
        error_paths = {i.field_path for i in issues if i.field_path and i.severity.value == "error"}
        warn_paths = {i.field_path for i in issues if i.field_path and i.severity.value == "warning"}

        scored = []
        confidences: dict[str, tuple[float | None, str | None]] = {}

        for path, value in flatten(data):
            field_spec = spec.field_by_path(to_template(path))
            if field_spec is None or value is None:
                continue

            grounding = (
                grounding_score(value, text, source_normalised=source_normalised)
                if field_spec.groundable
                else None
            )
            validation = 0.05 if path in error_paths else (0.45 if path in warn_paths else 1.0)
            format_signal = 1.0 if field_spec.kind is not FieldKind.DATE else _date_signal(value)

            confidence = score_field(
                path,
                signals=ConfidenceSignals(
                    grounding=grounding,
                    format_cleanliness=format_signal,
                    validation=validation,
                    context=0.95,
                ),
            )
            scored.append(confidence)
            confidences[path] = (confidence.score, confidence.band.value)

        overall = aggregate(scored, required_paths=spec.required_paths) if scored else None
        return confidences, overall


def _date_signal(value) -> float:
    from docflow.schemas.fields import parse_date_detailed

    parsed = parse_date_detailed(value)
    if parsed.value is None:
        return 0.1
    if parsed.ambiguous:
        return 0.5
    return 0.8 if parsed.was_fuzzy else 1.0


_SETTINGS_TYPES = (LLMSettings, ProcessingSettings)
