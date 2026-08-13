"""Extraction, validation, confidence and review-routing stages.

The confidence stage is where the interesting judgement lives — see
`docflow.domain.confidence` for the signal model. This module supplies the
signals; that module combines them.
"""

from __future__ import annotations

import structlog

from docflow.config import LLMSettings, ProcessingSettings
from docflow.domain.confidence import (
    ConfidenceSignals,
    aggregate,
    grounding_score,
    normalise_for_matching,
    score_field,
)
from docflow.domain.enums import ConfidenceBand, ProcessingStage, ValidationSeverity
from docflow.extraction.baseline import extract_baseline
from docflow.extraction.extractor import LLMExtractor
from docflow.llm.base import LLMProvider
from docflow.pipeline.context import PipelineContext
from docflow.pipeline.stage import Stage
from docflow.schemas.base import FieldKind, FieldSpec
from docflow.schemas.fields import parse_date_detailed, parse_decimal
from docflow.validation.engine import RuleContext, ValidationEngine
from docflow.validation.paths import MISSING, flatten, get_path, to_template

logger = structlog.get_logger(__name__)


class LLMExtractionStage(Stage):
    stage = ProcessingStage.LLM_EXTRACTION

    def __init__(self, provider: LLMProvider, settings: LLMSettings) -> None:
        self._extractor = LLMExtractor(provider, settings)

    async def run(self, ctx: PipelineContext) -> None:
        assert ctx.spec is not None

        outcome = await self._extractor.extract(
            spec=ctx.spec,
            document_text=ctx.document_text,
            page_count=ctx.page_count,
        )
        ctx.extraction = outcome
        ctx.llm_calls += outcome.attempts
        ctx.total_cost_usd += outcome.cost_usd
        ctx.total_input_tokens += outcome.input_tokens
        ctx.total_output_tokens += outcome.output_tokens

        if outcome.repaired:
            ctx.add_review_reason("The first extraction attempt needed correction")

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        if ctx.extraction is None:
            return {}
        return {
            "provider": ctx.extraction.provider,
            "model": ctx.extraction.model,
            "prompt_version": ctx.extraction.prompt_version,
            "input_tokens": ctx.extraction.input_tokens,
            "output_tokens": ctx.extraction.output_tokens,
            "cost_usd": str(ctx.extraction.cost_usd),
            "attempts": ctx.extraction.attempts,
            "repaired": ctx.extraction.repaired,
        }


class BaselineCrossCheckStage(Stage):
    """Run the deterministic extractor as an independent second opinion.

    Cheap (microseconds, no network) and genuinely informative: two methods that
    share no failure mode agreeing on a bank account number is much stronger
    evidence than either alone. Disagreement is equally useful — it marks exactly
    the fields worth a human glance.

    **Independence is a precondition, and it is checked.** When the configured
    provider is the fixture heuristic, the "model" output *is* baseline output —
    the two agree by construction, and scoring that agreement as corroboration
    would manufacture confidence out of nothing. The stage skips itself in that
    case rather than producing a flattering number.
    """

    stage = ProcessingStage.BASELINE_CROSSCHECK
    optional = True

    # Extractors whose output the baseline cannot independently corroborate,
    # because it produced that output.
    NON_INDEPENDENT_MODELS = frozenset({"fixture-heuristic"})

    def should_run(self, ctx: PipelineContext) -> bool:
        if ctx.spec is None or not ctx.document_text:
            return False
        if ctx.extraction and ctx.extraction.model in self.NON_INDEPENDENT_MODELS:
            logger.debug(
                "baseline.crosscheck_skipped",
                reason="extractor_is_baseline",
                model=ctx.extraction.model,
            )
            return False
        return True

    async def run(self, ctx: PipelineContext) -> None:
        assert ctx.spec is not None
        ctx.baseline = extract_baseline(
            ctx.document_text, ctx.spec.key, day_first=ctx.spec.day_first_dates
        )

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        return {"baseline_fields": ctx.baseline.field_count if ctx.baseline else 0}


class ValidationStage(Stage):
    """Canonical validation pass over the final data.

    The extractor validates internally to drive its repair loop; this stage
    produces the record of record. Re-running is nearly free (pure functions over
    a small dict) and means the persisted issues always correspond to the persisted
    data — including later, when a human edits a field and validation is re-run
    through this same code path.
    """

    stage = ProcessingStage.BUSINESS_VALIDATION

    def __init__(self, engine: ValidationEngine | None = None) -> None:
        self._engine = engine or ValidationEngine()

    def should_run(self, ctx: PipelineContext) -> bool:
        return ctx.extraction is not None and ctx.spec is not None

    async def run(self, ctx: PipelineContext) -> None:
        assert ctx.extraction is not None and ctx.spec is not None

        syntax_issues = [i for i in ctx.extraction.issues if i.rule_id == "schema"]
        result = self._engine.validate(
            RuleContext(
                data=ctx.extraction.data,
                spec=ctx.spec,
                source_text=ctx.document_text,
            )
        )
        ctx.issues = [*syntax_issues, *result.issues]

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        errors = sum(1 for i in ctx.issues if i.severity is ValidationSeverity.ERROR)
        warnings = sum(1 for i in ctx.issues if i.severity is ValidationSeverity.WARNING)
        return {"errors": errors, "warnings": warnings, "total_issues": len(ctx.issues)}


class ConfidenceScoringStage(Stage):
    """Score every extracted field from independent signals."""

    stage = ProcessingStage.CONFIDENCE_SCORING

    def should_run(self, ctx: PipelineContext) -> bool:
        return ctx.extraction is not None and ctx.spec is not None

    async def run(self, ctx: PipelineContext) -> None:
        assert ctx.extraction is not None and ctx.spec is not None
        spec = ctx.spec
        data = ctx.extraction.data

        # Normalise the source text once. Doing it per field turned an O(fields)
        # loop into O(fields × document_length).
        source_normalised = normalise_for_matching(ctx.document_text)

        error_paths = {
            i.field_path for i in ctx.issues
            if i.field_path and i.severity is ValidationSeverity.ERROR
        }
        warning_paths = {
            i.field_path for i in ctx.issues
            if i.field_path and i.severity is ValidationSeverity.WARNING
        }
        baseline_data = ctx.baseline.data if ctx.baseline else {}

        # Extraction from OCR text starts lower across the board: character-level
        # OCR errors produce values that look plausible and are wrong.
        context_signal = 0.65 if ctx.used_ocr else 0.95

        confidences = []
        for path, value in flatten(data):
            field_spec = self._spec_for(spec, path)
            if field_spec is None:
                continue
            if value is None:
                # A null is not low confidence — it is a confident "not present",
                # and the required-field rule decides whether that is a problem.
                continue

            reasons: list[str] = []
            grounding = None
            if field_spec.groundable:
                grounding = grounding_score(
                    value, ctx.document_text, source_normalised=source_normalised
                )
                if grounding <= 0.10:
                    reasons.append("Value does not appear in the document text")

            format_signal, format_reason = self._format_signal(field_spec, value)
            if format_reason:
                reasons.append(format_reason)

            validation_signal = 1.0
            if path in error_paths:
                validation_signal = 0.05
                reasons.append("Failed validation")
            elif path in warning_paths:
                validation_signal = 0.45
                reasons.append("Flagged by a validation warning")

            agreement, agreement_reason = self._baseline_agreement(baseline_data, path, value)
            if agreement_reason:
                reasons.append(agreement_reason)

            # Baseline agreement is folded into grounding rather than added as a
            # sixth weighted signal: both answer "is this value supported by
            # something other than the model's say-so?", and giving it its own
            # weight would double-count that evidence.
            if agreement is not None:
                grounding = (
                    max(grounding, agreement) if grounding is not None else agreement
                )

            confidence = score_field(
                path,
                signals=ConfidenceSignals(
                    grounding=grounding,
                    model_reported=None,  # not requested; see docs/AI.md
                    format_cleanliness=format_signal,
                    validation=validation_signal,
                    context=context_signal,
                ),
                reasons=reasons,
            )
            confidences.append(confidence)

        ctx.field_confidences = confidences
        ctx.overall_confidence = aggregate(
            confidences, required_paths=self._concrete_required(spec, data)
        )

    # ------------------------------------------------------------------ signals

    @staticmethod
    def _format_signal(field_spec: FieldSpec, value: object) -> tuple[float, str | None]:
        """How cleanly did this value parse into its declared type?"""
        if field_spec.kind is FieldKind.DATE:
            parsed = parse_date_detailed(value)
            if parsed.value is None:
                return 0.1, "Date could not be parsed"
            if parsed.ambiguous:
                return 0.5, "Date format is ambiguous (day/month order unclear)"
            if parsed.was_fuzzy:
                return 0.8, None
            return 1.0, None

        if field_spec.kind in (FieldKind.MONEY, FieldKind.NUMBER):
            try:
                return (1.0, None) if parse_decimal(value) is not None else (0.2, "Not a number")
            except Exception:  # noqa: BLE001
                return 0.1, "Value is not a valid number"

        if field_spec.kind is FieldKind.CURRENCY:
            from docflow.schemas.fields import normalize_currency

            return (1.0, None) if normalize_currency(value) else (0.1, "Unrecognised currency")

        if field_spec.kind in (FieldKind.STRING, FieldKind.TEXT, FieldKind.IDENTIFIER):
            text = str(value).strip()
            if not text:
                return 0.1, "Empty value"
            # A single character is almost always an extraction artefact.
            if len(text) == 1:
                return 0.4, "Suspiciously short value"
            return 1.0, None

        return 1.0, None

    @staticmethod
    def _baseline_agreement(
        baseline: dict, path: str, value: object
    ) -> tuple[float | None, str | None]:
        """Compare against the deterministic extractor where it produced a value."""
        other = get_path(baseline, path)
        if other is MISSING or other is None:
            return None, None

        left, right = _comparable(value), _comparable(other)
        if left == right:
            return 1.0, None
        return 0.25, f"Rule-based extractor read this as {str(other)[:40]!r}"

    @staticmethod
    def _spec_for(spec, concrete_path: str) -> FieldSpec | None:
        return spec.field_by_path(to_template(concrete_path))

    @staticmethod
    def _concrete_required(spec, data: dict) -> set[str]:
        """Required template paths expanded to the concrete paths present."""
        from docflow.validation.paths import expand

        out: set[str] = set()
        for template in spec.required_paths:
            for concrete, _ in expand(data, template):
                out.add(concrete)
        return out

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        bands = {b.value: 0 for b in ConfidenceBand}
        for confidence in ctx.field_confidences:
            bands[confidence.band.value] += 1
        return {
            "overall_confidence": ctx.overall_confidence,
            "fields_scored": len(ctx.field_confidences),
            **{f"band_{k}": v for k, v in bands.items()},
        }


def _comparable(value: object) -> str:
    """Normalise for cross-extractor comparison.

    Numbers compare numerically (`"1234.50"` == `"1234.5"`), everything else
    compares case- and punctuation-insensitively.
    """
    try:
        number = parse_decimal(value)
    except Exception:  # noqa: BLE001
        number = None
    if number is not None and str(value).strip().replace(" ", "")[:1].isdigit():
        return str(number.normalize())
    return normalise_for_matching(str(value))


class ReviewRoutingStage(Stage):
    """Decide whether a human must look at this document.

    Any of the following sends it to review:

      * a validation ERROR — the data is known to be wrong
      * overall confidence below the type's threshold
      * any *critical* field below the type's stricter critical threshold
      * a required field missing entirely

    The asymmetry is deliberate. Sending a good document to review costs a few
    seconds of someone's attention. Auto-approving a wrong bank account costs a
    misdirected payment. When uncertain, route to review.
    """

    stage = ProcessingStage.REVIEW_ROUTING

    def __init__(self, settings: ProcessingSettings) -> None:
        self._settings = settings

    def should_run(self, ctx: PipelineContext) -> bool:
        return ctx.extraction is not None and ctx.spec is not None

    async def run(self, ctx: PipelineContext) -> None:
        assert ctx.spec is not None
        spec = ctx.spec

        errors = [i for i in ctx.issues if i.severity is ValidationSeverity.ERROR]
        if errors:
            ctx.needs_review = True
            shown = errors[:3]
            for issue in shown:
                ctx.add_review_reason(issue.message)
            if len(errors) > len(shown):
                ctx.add_review_reason(f"and {len(errors) - len(shown)} further validation errors")

        threshold = min(spec.review_threshold, self._settings.review_confidence_threshold)
        if ctx.overall_confidence is not None and ctx.overall_confidence < threshold:
            ctx.needs_review = True
            ctx.add_review_reason(
                f"Overall confidence {ctx.overall_confidence:.0%} is below the "
                f"{threshold:.0%} threshold for {spec.name.lower()}s"
            )

        critical = spec.critical_paths
        for confidence in ctx.field_confidences:
            template = to_template(confidence.field_path)
            if template in critical and confidence.score < spec.critical_field_threshold:
                ctx.needs_review = True
                field_spec = spec.field_by_path(template)
                label = field_spec.label if field_spec else confidence.field_path
                ctx.add_review_reason(f"{label} needs checking (confidence {confidence.score:.0%})")

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        return {
            "needs_review": ctx.needs_review,
            "reasons": len(ctx.review_reasons),
            "overall_confidence": ctx.overall_confidence,
        }
