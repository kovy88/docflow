"""Worker-side orchestration: run the pipeline, persist everything it produced.

Separate from `DocumentService` because the callers are different processes with
different constraints. The API is latency-bound and never touches an LLM; the
worker is throughput-bound and does nothing else.

## Transaction boundary

The pipeline runs **outside** the write transaction, and results are persisted in a
single transaction afterwards. Holding a database transaction open across a
30-second LLM call would pin a connection from a small pool for the duration and
turn provider latency into database exhaustion.

The cost is that a crash between pipeline completion and commit loses the work.
That is acceptable and recoverable: the job is retried, and the money already spent
on the LLM call is the price of not building a distributed transaction. The
alternative — writing partial results as each stage completes — costs a round trip
per stage on every document to protect against a rare crash.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.config import Settings
from docflow.db.models import Document, Extraction, ProcessingJob, ProcessingStep
from docflow.db.repositories import (
    DocumentRepository,
    ExtractionRepository,
    JobRepository,
    UsageRepository,
)
from docflow.domain.confidence import FieldConfidence
from docflow.domain.enums import (
    DocumentStatus,
    ExtractionStatus,
    ExtractorKind,
    FieldSource,
    JobStatus,
)
from docflow.domain.errors import DocflowError, ResourceNotFoundError, is_retryable
from docflow.pipeline import PipelineContext, PipelineRunner
from docflow.services.document_service import current_billing_period
from docflow.validation.paths import to_template

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class ProcessingResult:
    document_id: uuid.UUID
    status: DocumentStatus
    extraction_id: uuid.UUID | None
    needs_review: bool
    confidence: float | None
    cost_usd: Decimal
    duration_ms: int
    error_code: str | None = None
    retryable: bool = False


class ProcessingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        pipeline: PipelineRunner,
        settings: Settings,
    ) -> None:
        self._session = session
        self._pipeline = pipeline
        self._settings = settings

    async def process(
        self, *, job_id: uuid.UUID, organization_id: uuid.UUID, attempt: int = 1,
        request_id: str | None = None,
    ) -> ProcessingResult:
        jobs = JobRepository(self._session, organization_id)
        documents = DocumentRepository(self._session, organization_id)

        job = await jobs.get(job_id)
        if job is None:
            raise ResourceNotFoundError(f"Job {job_id} not found")

        # A job that already succeeded must not run again. This is the guard that
        # makes an at-least-once queue safe: arq can redeliver after a worker dies
        # between finishing the work and acknowledging it.
        if job.status == JobStatus.SUCCEEDED.value:
            logger.info("job.already_succeeded", job_id=str(job_id))
            document = await documents.get(job.document_id)
            return ProcessingResult(
                document_id=job.document_id,
                status=DocumentStatus(document.status) if document else DocumentStatus.COMPLETED,
                extraction_id=None,
                needs_review=False,
                confidence=None,
                cost_usd=Decimal("0"),
                duration_ms=0,
            )

        document = await documents.get(job.document_id)
        if document is None:
            raise ResourceNotFoundError(f"Document {job.document_id} not found")

        await jobs.mark_running(job, attempt)
        document.status = DocumentStatus.PROCESSING.value
        document.processing_started_at = dt.datetime.now(dt.UTC)
        await self._session.commit()

        ctx = PipelineContext(
            document_id=document.id,
            organization_id=organization_id,
            job_id=job.id,
            attempt=attempt,
            request_id=request_id,
            filename=document.filename,
            content_type=document.content_type,
            storage_key=document.storage_key,
            checksum_sha256=document.checksum_sha256,
            requested_type_key=document.document_type_key,
        )

        started = dt.datetime.now(dt.UTC)
        ctx = await self._pipeline.run(ctx)
        duration_ms = int((dt.datetime.now(dt.UTC) - started).total_seconds() * 1000)

        return await self._persist(
            job=job, document=document, ctx=ctx, duration_ms=duration_ms,
            organization_id=organization_id,
        )

    # ----------------------------------------------------------------- persist

    async def _persist(
        self,
        *,
        job: ProcessingJob,
        document: Document,
        ctx: PipelineContext,
        duration_ms: int,
        organization_id: uuid.UUID,
    ) -> ProcessingResult:
        jobs = JobRepository(self._session, organization_id)
        extractions = ExtractionRepository(self._session, organization_id)
        usage = UsageRepository(self._session, organization_id)

        self._write_steps(ctx)

        document.page_count = ctx.page_count
        document.char_count = ctx.extracted.char_count if ctx.extracted else None
        document.used_ocr = ctx.used_ocr
        document.language = ctx.extracted.language if ctx.extracted else None
        document.text_storage_key = ctx.text_storage_key
        document.processing_finished_at = dt.datetime.now(dt.UTC)
        document.processing_ms = duration_ms
        if ctx.classification:
            document.document_type_key = ctx.classification.document_type_key
            document.classification_confidence = ctx.classification.confidence

        # Usage is recorded even on failure. Tokens spent before a stage failed are
        # still tokens the provider billed; omitting them would make the cost
        # dashboard quietly understate real spend.
        if ctx.llm_calls:
            usage.record(
                document_id=document.id,
                kind="extraction",
                provider=ctx.extraction.provider if ctx.extraction else None,
                model=ctx.extraction.model if ctx.extraction else None,
                input_tokens=ctx.total_input_tokens,
                output_tokens=ctx.total_output_tokens,
                cost_usd=ctx.total_cost_usd,
                latency_ms=duration_ms,
                billing_period=current_billing_period(),
            )

        if ctx.failed or ctx.extraction is None:
            document.status = DocumentStatus.FAILED.value
            document.error_code = ctx.error_code
            document.error_message = ctx.error_message
            retryable = _error_is_retryable(ctx.error_code)
            await jobs.finish(
                job,
                status=JobStatus.FAILED,
                error_code=ctx.error_code,
                error_message=ctx.error_message,
            )
            await self._session.commit()
            return ProcessingResult(
                document_id=document.id,
                status=DocumentStatus.FAILED,
                extraction_id=None,
                needs_review=False,
                confidence=None,
                cost_usd=ctx.total_cost_usd,
                duration_ms=duration_ms,
                error_code=ctx.error_code,
                retryable=retryable,
            )

        extraction = await self._write_extraction(extractions, ctx, document, job)
        ctx.extraction_id = extraction.id

        status = (
            DocumentStatus.NEEDS_REVIEW if ctx.needs_review else DocumentStatus.COMPLETED
        )
        document.status = status.value
        document.error_code = None
        document.error_message = None

        await jobs.finish(job, status=JobStatus.SUCCEEDED)
        await self._session.commit()

        logger.info(
            "document.processed",
            **ctx.log_fields(),
            status=status.value,
            confidence=ctx.overall_confidence,
            needs_review=ctx.needs_review,
            cost_usd=str(ctx.total_cost_usd),
            duration_ms=duration_ms,
            document_type=ctx.document_type_key,
        )

        return ProcessingResult(
            document_id=document.id,
            status=status,
            extraction_id=extraction.id,
            needs_review=ctx.needs_review,
            confidence=ctx.overall_confidence,
            cost_usd=ctx.total_cost_usd,
            duration_ms=duration_ms,
        )

    def _write_steps(self, ctx: PipelineContext) -> None:
        for record in ctx.steps:
            self._session.add(
                ProcessingStep(
                    job_id=ctx.job_id,
                    document_id=ctx.document_id,
                    sequence=record.sequence,
                    stage=record.stage.value,
                    status=record.status.value,
                    attempt=ctx.attempt,
                    started_at=record.started_at,
                    finished_at=record.finished_at,
                    duration_ms=record.duration_ms,
                    error_code=record.error_code,
                    error_message=record.error_message,
                    detail=_jsonable(record.detail),
                )
            )

    async def _write_extraction(
        self,
        extractions: ExtractionRepository,
        ctx: PipelineContext,
        document: Document,
        job: ProcessingJob,
    ) -> Extraction:
        assert ctx.extraction is not None and ctx.spec is not None

        await extractions.supersede_current(document.id)
        revision = await extractions.next_revision(document.id)

        status = (
            ExtractionStatus.NEEDS_REVIEW if ctx.needs_review else ExtractionStatus.DRAFT
        )
        extraction = await extractions.create(
            document_id=document.id,
            job_id=job.id,
            status=status.value,
            is_current=True,
            revision=revision,
            extractor=ExtractorKind.LLM.value,
            provider=ctx.extraction.provider,
            model=ctx.extraction.model,
            model_version=ctx.extraction.model_version,
            prompt_key=ctx.extraction.prompt_key,
            prompt_version=ctx.extraction.prompt_version,
            document_type_key=ctx.spec.key,
            schema_version=ctx.spec.version,
            data=_jsonable(ctx.extraction.data),
            raw_model_output=_jsonable(ctx.extraction.raw_model_output),
            overall_confidence=ctx.overall_confidence,
            needs_review=ctx.needs_review,
            review_reasons=list(ctx.review_reasons),
            input_tokens=ctx.total_input_tokens,
            output_tokens=ctx.total_output_tokens,
            cost_usd=ctx.total_cost_usd,
            latency_ms=ctx.extraction.latency_ms,
            retry_count=max(0, ctx.extraction.attempts - 1),
        )

        by_path = {c.field_path: c for c in ctx.field_confidences}
        evidence = ctx.baseline.evidence if ctx.baseline else {}
        required = ctx.spec.required_paths

        for path, value in _iter_leaves(ctx.extraction.data):
            spec_field = ctx.spec.field_by_path(to_template(path))
            if spec_field is None:
                continue
            confidence: FieldConfidence | None = by_path.get(path)
            extractions.add_field(
                extraction_id=extraction.id,
                field_path=path,
                label=spec_field.label,
                value={"value": value},
                confidence=confidence.score if confidence else None,
                confidence_band=confidence.band.value if confidence else None,
                confidence_signals=(
                    {
                        "reasons": confidence.reasons,
                        "grounding": confidence.signals.grounding,
                        "format": confidence.signals.format_cleanliness,
                        "validation": confidence.signals.validation,
                        "context": confidence.signals.context,
                    }
                    if confidence
                    else {}
                ),
                source=FieldSource.LLM.value,
                is_required=to_template(path) in required,
                needs_review=bool(confidence and confidence.needs_review),
                evidence_text=evidence.get(path),
            )

        for issue in ctx.issues:
            extractions.add_issue(
                extraction_id=extraction.id,
                rule_id=issue.rule_id,
                field_path=issue.field_path,
                severity=issue.severity.value,
                code=issue.code,
                message=issue.message,
                context=_jsonable(issue.context),
            )

        return extraction


def _error_is_retryable(error_code: str | None) -> bool:
    if not error_code:
        return False
    for subclass in _all_error_classes():
        if subclass.code == error_code:
            return subclass.retryable
    return False


def _all_error_classes() -> list[type[DocflowError]]:
    seen: list[type[DocflowError]] = []
    stack: list[type[DocflowError]] = [DocflowError]
    while stack:
        current = stack.pop()
        seen.append(current)
        stack.extend(current.__subclasses__())
    return seen


def _iter_leaves(data: Any, prefix: str = ""):
    """Yield `(path, value)` for every leaf, including explicit nulls.

    Nulls are persisted deliberately: the review UI must render "Due date — not
    found" as a row a human can fill in. Omitting null fields would make a missing
    value invisible, which is the opposite of what review is for.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict | list):
                yield from _iter_leaves(value, path)
            else:
                yield path, value
    elif isinstance(data, list):
        for index, item in enumerate(data):
            path = f"{prefix}.{index}" if prefix else str(index)
            if isinstance(item, dict | list):
                yield from _iter_leaves(item, path)
            else:
                yield path, item


def _jsonable(value: Any) -> Any:
    """Coerce Decimals, dates and UUIDs into JSON-safe primitives."""
    import datetime
    from decimal import Decimal as D

    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, D):
        return str(value)
    if isinstance(value, datetime.date | datetime.datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


__all__ = ["ProcessingResult", "ProcessingService", "is_retryable"]
