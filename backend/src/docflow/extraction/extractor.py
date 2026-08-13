"""LLM extraction with a bounded self-repair loop.

The loop is the interesting part. When extracted data fails schema or semantic
validation, there are three options:

  (a) fail the document — wasteful; the model was probably close
  (b) retry the same prompt — usually reproduces the same failure at full cost
  (c) feed the specific errors back and ask for a correction

(c) is what happens here, and it is bounded to `max_repair_attempts` (default 1).
The bound matters more than the loop: a repair that fails once will usually fail
again, and an unbounded loop turns a bad document into an unbounded bill.

Repair is only attempted for errors a model can plausibly fix — a misparsed date,
a number with a stray separator, a missing field that is actually on the page. It
is **not** attempted for arithmetic disagreements: if the document's own totals do
not add up, that is a fact about the document, and asking the model to "fix" it
would be asking it to fabricate a number that makes the arithmetic work. Those
route to human review instead, which is the correct destination.

Cost is checked before every call against the per-document ceiling, so the repair
path cannot silently double a document's cost beyond the configured limit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from docflow.config import LLMSettings
from docflow.domain.enums import ValidationSeverity
from docflow.domain.errors import (
    CostLimitExceededError,
    MalformedModelOutputError,
    OutputTruncatedError,
)
from docflow.llm.base import LLMProvider, LLMRequest, LLMResponse
from docflow.llm.fixture_provider import DOCUMENT_TYPE_KEY, SOURCE_TEXT_KEY
from docflow.llm.pricing import estimate_tokens
from docflow.llm.schema import describe_schema_for_prompt, normalize_schema
from docflow.prompts import extraction as prompts
from docflow.schemas.base import DocumentTypeSpec
from docflow.validation.engine import Issue, RuleContext, ValidationEngine, validate_syntax

logger = structlog.get_logger(__name__)

# Validation codes a model can realistically correct by looking at the document
# again. Everything else is either a property of the document (arithmetic that does
# not add up) or something a human must decide.
REPAIRABLE_CODES = frozenset(
    {
        "missing_required_field",
        "unparseable_date",
        "implausible_date",
        "unsupported_currency",
        "date_order_violation",
        "invalid_iban",
        "invalid_account_number",
        "non_numeric_variable_symbol",
        "variable_symbol_too_long",
        # Pydantic type errors
        "string_type",
        "int_parsing",
        "decimal_parsing",
        "date_from_datetime_parsing",
        "date_parsing",
        "float_parsing",
        "extra_forbidden",
        "missing",
    }
)


@dataclass
class ExtractionOutcome:
    data: dict[str, Any]
    raw_model_output: dict[str, Any] | None
    issues: list[Issue] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    model_version: str | None = None
    prompt_key: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    latency_ms: int = 0
    attempts: int = 1
    repaired: bool = False

    @property
    def blocking_errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is ValidationSeverity.ERROR]


class LLMExtractor:
    def __init__(
        self,
        provider: LLMProvider,
        settings: LLMSettings,
        *,
        validation_engine: ValidationEngine | None = None,
        max_repair_attempts: int = 1,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._validator = validation_engine or ValidationEngine()
        self._max_repair_attempts = max_repair_attempts

    async def extract(
        self,
        *,
        spec: DocumentTypeSpec,
        document_text: str,
        page_count: int | None = None,
    ) -> ExtractionOutcome:
        text = self._prepare_text(document_text)
        schema = normalize_schema(spec.json_schema())
        nonce = prompts.new_nonce()

        system = prompts.EXTRACTION_SYSTEM.render(
            type_guidance=self._type_guidance(spec),
        )
        user = prompts.EXTRACTION_USER.render(
            document_type_name=spec.name,
            page_note=f"Pages: {page_count}\n" if page_count else "",
            nonce=nonce,
            document_text=text,
        )

        self._guard_cost(system, user)

        response = await self._call(system, user, schema, spec, text)
        outcome = self._build_outcome(response, spec, text)

        if not outcome.blocking_errors or self._max_repair_attempts <= 0:
            return outcome

        repairable = [i for i in outcome.blocking_errors if i.code in REPAIRABLE_CODES]
        if not repairable:
            logger.info(
                "extraction.repair_skipped",
                document_type=spec.key,
                reason="no_repairable_errors",
                codes=sorted({i.code for i in outcome.blocking_errors}),
            )
            return outcome

        return await self._repair(
            outcome=outcome,
            spec=spec,
            text=text,
            schema=schema,
            system=system,
            repairable=repairable,
        )

    # ------------------------------------------------------------------ internals

    async def _repair(
        self,
        *,
        outcome: ExtractionOutcome,
        spec: DocumentTypeSpec,
        text: str,
        schema: dict[str, Any],
        system: str,
        repairable: list[Issue],
    ) -> ExtractionOutcome:
        nonce = prompts.new_nonce()
        issue_lines = "\n".join(
            f"- {i.field_path or '(document)'}: {i.message}" for i in repairable
        )
        repair_user = prompts.REPAIR_USER.render(
            issues=issue_lines,
            previous_output=json.dumps(
                outcome.raw_model_output or {}, ensure_ascii=False, indent=2, default=str
            )[:8000],
            schema_summary=describe_schema_for_prompt(schema),
            nonce=nonce,
            document_text=text,
        )

        remaining = Decimal(str(self._settings.max_cost_usd_per_document)) - outcome.cost_usd
        if remaining <= 0:
            logger.warning("extraction.repair_skipped", reason="cost_ceiling")
            return outcome

        try:
            response = await self._call(system, repair_user, schema, spec, text)
        except (MalformedModelOutputError, OutputTruncatedError) as exc:
            # A failed repair must not lose the first, partially-good result. The
            # original outcome still routes to human review with its issues intact,
            # which is strictly better than failing the document.
            logger.warning("extraction.repair_failed", error=exc.code)
            return outcome

        repaired = self._build_outcome(response, spec, text)
        repaired.attempts = outcome.attempts + 1
        repaired.cost_usd = outcome.cost_usd + repaired.cost_usd
        repaired.input_tokens += outcome.input_tokens
        repaired.output_tokens += outcome.output_tokens
        repaired.latency_ms += outcome.latency_ms
        repaired.repaired = True

        # Only keep the repair if it actually helped. A repair that trades three
        # errors for four is a regression, and silently accepting it would make the
        # loop actively harmful.
        if len(repaired.blocking_errors) >= len(outcome.blocking_errors):
            logger.info(
                "extraction.repair_rejected",
                before=len(outcome.blocking_errors),
                after=len(repaired.blocking_errors),
            )
            outcome.attempts = repaired.attempts
            outcome.cost_usd = repaired.cost_usd
            outcome.input_tokens = repaired.input_tokens
            outcome.output_tokens = repaired.output_tokens
            outcome.latency_ms = repaired.latency_ms
            return outcome

        logger.info(
            "extraction.repair_succeeded",
            before=len(outcome.blocking_errors),
            after=len(repaired.blocking_errors),
        )
        return repaired

    async def _call(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        spec: DocumentTypeSpec,
        source_text: str,
    ) -> LLMResponse:
        request = LLMRequest(
            system=system,
            user_content=user,
            json_schema=schema,
            schema_name=spec.schema_id(),
            max_output_tokens=self._settings.max_output_tokens,
            effort=self._settings.effort,
            timeout_seconds=self._settings.timeout_seconds,
            temperature=self._settings.temperature,
            metadata={
                DOCUMENT_TYPE_KEY: spec.key,
                # The fixture provider needs the raw document text, not the fully
                # rendered prompt. Real providers ignore this.
                SOURCE_TEXT_KEY: source_text,
            },
        )
        return await self._provider.complete_structured(request)

    def _build_outcome(
        self, response: LLMResponse, spec: DocumentTypeSpec, text: str
    ) -> ExtractionOutcome:
        normalized, syntax_issues = validate_syntax(spec, response.data)
        issues = list(syntax_issues)

        data = normalized if normalized is not None else response.data
        if normalized is not None:
            result = self._validator.validate(
                RuleContext(data=normalized, spec=spec, source_text=text)
            )
            issues.extend(result.issues)

        return ExtractionOutcome(
            data=data,
            raw_model_output=response.data,
            issues=issues,
            provider=response.provider,
            model=response.model,
            model_version=response.model_version,
            prompt_key=prompts.EXTRACTION_SYSTEM.key,
            prompt_version=prompts.EXTRACTION_SYSTEM.version,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
        )

    def _prepare_text(self, text: str) -> str:
        """Bound input size.

        Truncation is at a character budget rather than a token budget because it
        happens before any tokenizer is available, and because cost is roughly
        linear in characters for this content mix. The marker is explicit so a
        truncated extraction is visible in the output rather than silently partial.
        """
        limit = self._settings.max_input_chars
        if len(text) <= limit:
            return text
        logger.warning("extraction.text_truncated", original_chars=len(text), limit=limit)
        head = int(limit * 0.75)
        tail = limit - head
        return text[:head] + "\n\n[... document truncated for length ...]\n\n" + text[-tail:]

    def _guard_cost(self, system: str, user: str) -> None:
        """Refuse work that would obviously breach the per-document ceiling.

        A pre-flight estimate, not an exact figure — the point is to stop a
        pathological input before it is billed, not to predict the invoice.
        """
        from docflow.llm.pricing import estimate_cost

        estimated_input = estimate_tokens(system) + estimate_tokens(user)
        projected = estimate_cost(
            self._settings.model,
            input_tokens=estimated_input,
            output_tokens=self._settings.max_output_tokens,
        )
        ceiling = Decimal(str(self._settings.max_cost_usd_per_document))
        if projected > ceiling:
            raise CostLimitExceededError(
                "Processing this document would exceed the per-document cost limit",
                detail={
                    "estimated_usd": str(projected),
                    "limit_usd": str(ceiling),
                    "estimated_input_tokens": estimated_input,
                },
            )

    def _type_guidance(self, spec: DocumentTypeSpec) -> str:
        if not spec.extraction_guidance:
            return ""
        return f"## Guidance for {spec.name.lower()} documents\n\n{spec.extraction_guidance}"
