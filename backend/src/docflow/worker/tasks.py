"""Worker tasks.

## Retry policy

The rule is: **retry only what a retry can fix.**

* `ProviderRateLimitError`, `ProviderTimeoutError`, `StorageError` — transient.
  Retry with exponential backoff and jitter.
* `SchemaValidationError`, `ModelRefusalError`, `CorruptDocumentError` — deterministic.
  The same input produces the same failure, so retrying burns money and delays the
  user's error message. Fail immediately.
* `ProviderAuthError` — a bad key stays bad. Fail immediately and alert.

The decision is not made here by inspecting exception types; it is read off
`DocflowError.retryable`, declared next to each error class. That keeps the policy
in one place and makes "is this retryable?" a property of the error rather than a
judgement scattered across handlers.

## Redelivery is `arq.Retry`, not "raise and hope"

arq only redelivers a job when the task raises its own `arq.worker.Retry` —
*every* other exception, including a plain re-raise of the original error, is
treated as terminal: arq deletes the job from the queue and records it failed
after exactly one attempt. This was a real bug here (a plain `RetryableJobError`
raised for this exact purpose, silently never redelivered — caught by reading
`arq==0.28.0`'s dispatch logic directly, not by inference). Every retry path
below raises `arq.Retry(defer=...)` for that reason; nothing else redelivers.

## Dead-lettering

A job that exhausts its attempts (this module's own `max_attempts`, checked
*before* deciding whether to raise `Retry` — arq's own `max_tries` is set one
higher purely as a backstop, see `worker/main.py`) is marked `dead_lettered`,
its document `failed` with a user-readable reason. It is not silently dropped
and it is not retried forever — both are ways to lose a customer's document
without telling them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import structlog
from arq import Retry
from sqlalchemy import select

from docflow.config import get_settings
from docflow.db.models import Document
from docflow.db.repositories import DocumentRepository, JobRepository
from docflow.db.session import session_scope
from docflow.domain.enums import DocumentStatus, JobStatus
from docflow.domain.errors import DocflowError, ResourceNotFoundError, is_retryable
from docflow.observability.logging import bind_context, clear_context
from docflow.observability.metrics import job_retries, jobs_dead_lettered
from docflow.pipeline import build_pipeline
from docflow.services.processing_service import ProcessingService
from docflow.worker.queue import retry_delay_seconds

logger = structlog.get_logger(__name__)


async def process_document(
    ctx: dict[str, Any],
    job_id: str,
    organization_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Process one document. Called by arq."""
    job_uuid = uuid.UUID(job_id)
    org_uuid = uuid.UUID(organization_id)
    # arq's `job_try` is 1-based and increments on each redelivery.
    attempt = int(ctx.get("job_try", 1))

    bind_context(
        job_id=job_id,
        organization_id=organization_id,
        request_id=request_id,
        attempt=attempt,
    )
    settings = get_settings()

    try:
        async with session_scope() as session:
            service = ProcessingService(
                session, pipeline=build_pipeline(settings=settings), settings=settings
            )
            result = await service.process(
                job_id=job_uuid,
                organization_id=org_uuid,
                attempt=attempt,
                request_id=request_id,
            )

        if result.status is DocumentStatus.FAILED and result.retryable:
            if attempt < settings.processing.max_attempts:
                # arq.Retry is the only exception arq redelivers on. The document
                # stays FAILED in the meantime, which is honest — it *has* failed,
                # and the next attempt will flip it back to PROCESSING.
                defer = retry_delay_seconds(
                    attempt,
                    base=settings.processing.retry_base_delay_seconds,
                    cap=settings.processing.retry_max_delay_seconds,
                )
                logger.info(
                    "job.retrying",
                    error_code=result.error_code,
                    attempt=attempt,
                    max_attempts=settings.processing.max_attempts,
                    defer_seconds=round(defer, 1),
                )
                job_retries.labels(error_code=result.error_code or "unknown").inc()
                raise Retry(defer=defer)
            await _dead_letter(job_uuid, org_uuid, result.error_code)

        if result.extraction_id is not None:
            await _notify(result, org_uuid)

        return {
            "document_id": str(result.document_id),
            "status": result.status.value,
            "needs_review": result.needs_review,
            "confidence": result.confidence,
            "cost_usd": str(result.cost_usd),
            "duration_ms": result.duration_ms,
        }

    except Retry:
        raise
    except ResourceNotFoundError:
        # The document or job was deleted while queued. Nothing to do and nothing
        # to retry — swallow so the job does not churn through its attempts.
        logger.warning("job.target_missing", job_id=job_id)
        return {"status": "skipped", "reason": "target_missing"}
    except DocflowError as exc:
        if is_retryable(exc) and attempt < settings.processing.max_attempts:
            defer = retry_delay_seconds(
                attempt,
                base=settings.processing.retry_base_delay_seconds,
                cap=settings.processing.retry_max_delay_seconds,
            )
            logger.warning(
                "job.retrying", error_code=exc.code, attempt=attempt, defer_seconds=round(defer, 1)
            )
            job_retries.labels(error_code=exc.code).inc()
            raise Retry(defer=defer) from exc
        await _fail_job(job_uuid, org_uuid, exc.code, exc.message)
        return {"status": "failed", "error_code": exc.code}
    except Exception as exc:
        logger.exception("job.crashed", job_id=job_id)
        if attempt >= settings.processing.max_attempts:
            await _fail_job(job_uuid, org_uuid, "internal_error", "Processing failed")
            return {"status": "failed", "error_code": "internal_error"}
        defer = retry_delay_seconds(
            attempt,
            base=settings.processing.retry_base_delay_seconds,
            cap=settings.processing.retry_max_delay_seconds,
        )
        job_retries.labels(error_code="internal_error").inc()
        raise Retry(defer=defer) from exc
    finally:
        clear_context()


async def _dead_letter(job_id: uuid.UUID, org_id: uuid.UUID, error_code: str | None) -> None:
    async with session_scope() as session:
        jobs = JobRepository(session, org_id)
        job = await jobs.get(job_id)
        if job is None:
            return
        job.status = JobStatus.DEAD_LETTERED.value
        documents = DocumentRepository(session, org_id)
        await documents.set_status(
            job.document_id,
            DocumentStatus.FAILED,
            error_code=error_code or "max_attempts_exceeded",
            error_message=(
                "Processing failed after several attempts. The document is unchanged — "
                "you can retry it, or contact support with the document id."
            ),
        )
    jobs_dead_lettered.labels(error_code=error_code or "max_attempts_exceeded").inc()
    logger.error("job.dead_lettered", job_id=str(job_id), error_code=error_code)


async def _fail_job(job_id: uuid.UUID, org_id: uuid.UUID, error_code: str, message: str) -> None:
    async with session_scope() as session:
        jobs = JobRepository(session, org_id)
        job = await jobs.get(job_id)
        if job is None:
            return
        await jobs.finish(
            job, status=JobStatus.FAILED, error_code=error_code, error_message=message
        )
        await DocumentRepository(session, org_id).set_status(
            job.document_id,
            DocumentStatus.FAILED,
            error_code=error_code,
            error_message=message,
        )


async def sweep_stale_jobs(ctx: dict[str, Any]) -> dict[str, Any]:
    """Recover documents stuck at `processing` with nothing left to revisit them.

    The gap this closes: a worker that dies outright (OOM, SIGKILL, host
    eviction) mid-job leaves its document at `processing` and its job at
    `running` forever. arq's own per-job lease can reclaim the job in Redis up
    to `max_tries`, but once that's exhausted arq marks it failed **without
    calling `process_document`** — so `_dead_letter`/`_fail_job`, the only
    code that ever moves a document off `processing`, never runs. Before this
    existed, nothing in the system — application or arq — would ever touch
    that document again. Registered as an arq cron job in `WorkerSettings`
    (`worker/main.py`), not triggered by request traffic.

    The discovery query is deliberately **not** organization-scoped — this is
    a system maintenance sweep across every tenant, the one legitimate
    exception to "no unscoped query to call" (see `db/repositories.py`'s
    module docstring). Every write after discovery still goes through the
    normal org-scoped repositories, keyed off that row's own
    `organization_id`.
    """
    settings = get_settings()
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(
        seconds=settings.processing.stale_processing_threshold_seconds
    )

    swept = 0
    async with session_scope() as session:
        result = await session.execute(
            select(Document.id, Document.organization_id).where(
                Document.status == DocumentStatus.PROCESSING.value,
                Document.processing_started_at < cutoff,
            )
        )
        stale = result.all()

    for document_id, organization_id in stale:
        async with session_scope() as session:
            jobs = JobRepository(session, organization_id)
            job = await jobs.latest_for_document(document_id)
            documents = DocumentRepository(session, organization_id)

            message = (
                "Processing did not complete within the expected time and was reset. "
                "This usually means the worker restarted mid-job — you can retry it."
            )
            if job is not None and job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
                await jobs.finish(
                    job,
                    status=JobStatus.FAILED,
                    error_code="processing_timeout",
                    error_message=message,
                )
            await documents.set_status(
                document_id,
                DocumentStatus.FAILED,
                error_code="processing_timeout",
                error_message=message,
            )
        swept += 1
        logger.warning(
            "job.swept_stale",
            document_id=str(document_id),
            organization_id=str(organization_id),
        )

    if swept:
        logger.info("sweep.completed", stale_documents_recovered=swept)
    return {"stale_documents_recovered": swept}


async def _notify(result: Any, organization_id: uuid.UUID) -> None:
    """Queue webhook deliveries for whoever subscribed to this outcome."""
    from docflow.domain.enums import WebhookEvent
    from docflow.services.webhook_service import WebhookService

    event = (
        WebhookEvent.DOCUMENT_NEEDS_REVIEW
        if result.needs_review
        else WebhookEvent.DOCUMENT_PROCESSED
    )
    try:
        async with session_scope() as session:
            await WebhookService(session, organization_id=organization_id).dispatch(
                event,
                {
                    "document_id": str(result.document_id),
                    "extraction_id": str(result.extraction_id),
                    "status": result.status.value,
                    "needs_review": result.needs_review,
                    "confidence": result.confidence,
                },
            )
    except Exception:
        # A webhook problem must never fail a document that processed correctly.
        logger.exception("webhook.dispatch_failed", document_id=str(result.document_id))


async def deliver_webhook(
    ctx: dict[str, Any], delivery_id: str, organization_id: str
) -> dict[str, Any]:
    from docflow.services.webhook_service import WebhookService

    attempt = int(ctx.get("job_try", 1))
    async with session_scope() as session:
        service = WebhookService(session, organization_id=uuid.UUID(organization_id))
        return await service.deliver(uuid.UUID(delivery_id), attempt=attempt)
