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

## Dead-lettering

A job that exhausts its attempts is marked `dead_lettered`, its document `failed`
with a user-readable reason. It is not silently dropped and it is not retried
forever — both of which are ways to lose a customer's document without telling them.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from docflow.config import get_settings
from docflow.db.repositories import DocumentRepository, JobRepository
from docflow.db.session import session_scope
from docflow.domain.enums import DocumentStatus, JobStatus
from docflow.domain.errors import DocflowError, ResourceNotFoundError, is_retryable
from docflow.observability.logging import bind_context, clear_context
from docflow.pipeline import build_pipeline
from docflow.services.processing_service import ProcessingService

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
                # Raising signals arq to redeliver. The document stays FAILED in
                # the meantime, which is honest — it *has* failed, and the next
                # attempt will flip it back to PROCESSING.
                logger.info(
                    "job.retrying",
                    error_code=result.error_code,
                    attempt=attempt,
                    max_attempts=settings.processing.max_attempts,
                )
                raise RetryableJobError(result.error_code or "unknown")
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

    except RetryableJobError:
        raise
    except ResourceNotFoundError:
        # The document or job was deleted while queued. Nothing to do and nothing
        # to retry — swallow so the job does not churn through its attempts.
        logger.warning("job.target_missing", job_id=job_id)
        return {"status": "skipped", "reason": "target_missing"}
    except DocflowError as exc:
        if is_retryable(exc) and attempt < settings.processing.max_attempts:
            logger.warning("job.retrying", error_code=exc.code, attempt=attempt)
            raise
        await _fail_job(job_uuid, org_uuid, exc.code, exc.message)
        return {"status": "failed", "error_code": exc.code}
    except Exception:
        logger.exception("job.crashed", job_id=job_id)
        if attempt >= settings.processing.max_attempts:
            await _fail_job(job_uuid, org_uuid, "internal_error", "Processing failed")
            return {"status": "failed", "error_code": "internal_error"}
        raise
    finally:
        clear_context()


class RetryableJobError(Exception):
    """Signals arq to redeliver. Carries the underlying error code for logging."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(f"retryable: {error_code}")


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
    logger.error("job.dead_lettered", job_id=str(job_id), error_code=error_code)


async def _fail_job(
    job_id: uuid.UUID, org_id: uuid.UUID, error_code: str, message: str
) -> None:
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
    except Exception:  # noqa: BLE001
        # A webhook problem must never fail a document that processed correctly.
        logger.exception("webhook.dispatch_failed", document_id=str(result.document_id))


async def deliver_webhook(
    ctx: dict[str, Any], delivery_id: str, organization_id: str
) -> dict[str, Any]:
    from docflow.services.webhook_service import WebhookService

    attempt = int(ctx.get("job_try", 1))
    async with session_scope() as session:
        service = WebhookService(session, organization_id=uuid.UUID(organization_id))
        outcome = await service.deliver(uuid.UUID(delivery_id), attempt=attempt)
    return outcome
