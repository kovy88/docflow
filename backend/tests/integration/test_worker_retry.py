"""Worker retry/dead-letter orchestration (`docflow.worker.tasks.process_document`).

This is the decision logic described in the module docstring of
`worker/tasks.py` — retry only what's retryable, dead-letter what exhausts its
attempts, never lose a document silently. It has no coverage anywhere else:
`test_pipeline.py` exercises the pipeline directly (bypassing the job/retry
machinery entirely), and nothing else calls `process_document`.

`ProcessingService` is stubbed — what's under test is the retry/fail/dead-letter
*decision*, not the pipeline itself, which is already covered elsewhere. The
database is real: a job and document are created for real, `process_document`
is called for real, and assertions check the real resulting row state.

## Why a dedicated session fixture instead of the shared `session`/`client` ones

`process_document` calls `docflow.db.session.session_scope()` internally, which
opens its own session from the module-level engine/sessionmaker singleton — a
*different* connection than the one the shared `session` fixture binds to. Two
different connections are two different Postgres transactions: one can't see
the other's uncommitted rows. `worker_session` below points that module-level
singleton at the same connection this test uses, the same way the shared
`session` fixture binds itself to it, so a document/job created through this
fixture is visible to `process_document`'s own `session_scope()` calls.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from arq import Retry
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from docflow.db import session as session_module
from docflow.db.models import Organization
from docflow.db.repositories import DocumentRepository, JobRepository
from docflow.domain.enums import DocumentStatus, JobStatus
from docflow.domain.errors import (
    DocflowError,
    ProviderAuthError,
    ProviderTimeoutError,
    ResourceNotFoundError,
)
from docflow.services.processing_service import ProcessingResult
from docflow.worker import tasks as worker_tasks

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def worker_session(engine) -> AsyncIterator[AsyncSession]:
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
    db_session = factory()
    await connection.begin_nested()

    @event.listens_for(db_session.sync_session, "after_transaction_end")
    def _restart_savepoint(sess, trans):  # type: ignore[no-untyped-def]
        if trans.nested and not trans._parent.nested:
            connection.sync_connection.begin_nested()

    prev_engine, prev_sessionmaker = session_module._engine, session_module._sessionmaker
    session_module._engine = connection  # type: ignore[assignment]
    session_module._sessionmaker = factory

    try:
        yield db_session
    finally:
        session_module._engine = prev_engine
        session_module._sessionmaker = prev_sessionmaker
        await db_session.close()
        await transaction.rollback()
        await connection.close()


class StubProcessingService:
    """Replaces the real pipeline with a controlled outcome per test.

    `outcome` is either a `ProcessingResult` to return, or an exception
    instance to raise — covers both ways `ProcessingService.process` can
    signal failure (a returned FAILED result, or a raised `DocflowError`).
    """

    def __init__(self, outcome: ProcessingResult | Exception) -> None:
        self.outcome = outcome

    def __call__(self, session, *, pipeline, settings):  # matches ProcessingService's constructor
        return self

    async def process(self, *, job_id, organization_id, attempt, request_id=None):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest_asyncio.fixture
async def worker_org(worker_session: AsyncSession) -> Organization:
    org = Organization(
        name="Worker Retry Test Org", slug=f"worker-{uuid.uuid4().hex[:8]}", plan="business"
    )
    worker_session.add(org)
    await worker_session.flush()
    return org


async def _make_job(
    worker_session: AsyncSession,
    org: Organization,
    *,
    max_attempts: int = 3,
    processing_started_at: object = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    documents = DocumentRepository(worker_session, org.id)
    document = await documents.create(
        filename="test.txt",
        content_type="text/plain",
        size_bytes=10,
        checksum_sha256=uuid.uuid4().hex,
        storage_key="orig/test.txt",
        status=DocumentStatus.PROCESSING.value,
    )
    if processing_started_at is not None:
        document.processing_started_at = processing_started_at
    jobs = JobRepository(worker_session, org.id)
    job = await jobs.create(
        document_id=document.id, idempotency_key=uuid.uuid4().hex, max_attempts=max_attempts
    )
    await worker_session.commit()
    return job.id, document.id


def _result(*, retryable: bool, error_code: str = "provider_timeout") -> ProcessingResult:
    return ProcessingResult(
        document_id=uuid.uuid4(),
        status=DocumentStatus.FAILED,
        extraction_id=None,
        needs_review=False,
        confidence=None,
        cost_usd=0,  # type: ignore[arg-type]
        duration_ms=0,
        error_code=error_code,
        retryable=retryable,
    )


def _patch_service(monkeypatch, outcome: ProcessingResult | Exception) -> None:
    monkeypatch.setattr(worker_tasks, "ProcessingService", StubProcessingService(outcome))
    monkeypatch.setattr(worker_tasks, "build_pipeline", lambda **kwargs: None)


def _metric_value(metric, *, suffix: str = "_total", **labels: str) -> float:
    """Read a Prometheus metric's current value via the public `.collect()` API.

    Deltas (before/after around the call under test) rather than an absolute
    value, since `REGISTRY` is a module-level singleton shared across the whole
    test session — other tests may have already incremented the same series.
    """
    for sample in metric.collect()[0].samples:
        if sample.name.endswith(suffix) and sample.labels == labels:
            return sample.value
    return 0.0


class TestRaisedDocflowErrors:
    """`ProcessingService.process` raises rather than returning a FAILED result."""

    async def test_retryable_error_below_max_attempts_is_reraised(
        self, worker_session, worker_org, monkeypatch
    ):
        job_id, _ = await _make_job(worker_session, worker_org, max_attempts=3)
        _patch_service(monkeypatch, ProviderTimeoutError("provider timed out"))

        # arq redelivers on `arq.Retry` and only `arq.Retry` — a bare re-raise of
        # the original error (what this asserted before) is invisible to arq's
        # dispatcher and the job dies after one attempt regardless of
        # `max_attempts`. See the "Redelivery is arq.Retry" note in
        # worker/tasks.py's module docstring.
        with pytest.raises(Retry) as exc_info:
            await worker_tasks.process_document({"job_try": 1}, str(job_id), str(worker_org.id))
        assert isinstance(exc_info.value.__cause__, ProviderTimeoutError)
        assert exc_info.value.defer_score is not None

        # Not failed yet — arq is expected to redeliver.
        job = await JobRepository(worker_session, worker_org.id).get(job_id)
        assert job.status == JobStatus.PENDING.value

    async def test_retryable_error_exhausted_fails_the_job(
        self, worker_session, worker_org, monkeypatch
    ):
        job_id, document_id = await _make_job(worker_session, worker_org, max_attempts=3)
        _patch_service(monkeypatch, ProviderTimeoutError("provider timed out"))

        outcome = await worker_tasks.process_document(
            {"job_try": 3}, str(job_id), str(worker_org.id)
        )

        assert outcome == {"status": "failed", "error_code": "provider_timeout"}
        job = await JobRepository(worker_session, worker_org.id).get(job_id)
        assert job.status == JobStatus.FAILED.value
        assert job.error_code == "provider_timeout"
        document = await DocumentRepository(worker_session, worker_org.id).get(document_id)
        assert document.status == DocumentStatus.FAILED.value

    async def test_non_retryable_error_fails_immediately_on_first_attempt(
        self, worker_session, worker_org, monkeypatch
    ):
        job_id, document_id = await _make_job(worker_session, worker_org, max_attempts=3)
        _patch_service(monkeypatch, ProviderAuthError("bad credentials"))

        outcome = await worker_tasks.process_document(
            {"job_try": 1}, str(job_id), str(worker_org.id)
        )

        assert outcome == {"status": "failed", "error_code": "provider_auth_failed"}
        job = await JobRepository(worker_session, worker_org.id).get(job_id)
        assert job.status == JobStatus.FAILED.value
        document = await DocumentRepository(worker_session, worker_org.id).get(document_id)
        assert document.status == DocumentStatus.FAILED.value

    async def test_resource_not_found_is_skipped_not_failed(
        self, worker_session, worker_org, monkeypatch
    ):
        job_id, _ = await _make_job(worker_session, worker_org)
        _patch_service(monkeypatch, ResourceNotFoundError("document deleted"))

        outcome = await worker_tasks.process_document(
            {"job_try": 1}, str(job_id), str(worker_org.id)
        )

        assert outcome == {"status": "skipped", "reason": "target_missing"}
        # Untouched, not marked failed — there is nothing to mark.
        job = await JobRepository(worker_session, worker_org.id).get(job_id)
        assert job.status == JobStatus.PENDING.value


class TestReturnedFailedResult:
    """`ProcessingService.process` returns rather than raises — the pipeline
    completed but decided the document failed (e.g. every extraction attempt
    was rejected)."""

    async def test_retryable_result_below_max_attempts_raises_arq_retry(
        self, worker_session, worker_org, monkeypatch
    ):
        from docflow.observability.metrics import job_retries

        job_id, _ = await _make_job(worker_session, worker_org, max_attempts=3)
        _patch_service(monkeypatch, _result(retryable=True, error_code="provider_timeout"))
        before = _metric_value(job_retries, error_code="provider_timeout")

        with pytest.raises(Retry) as exc_info:
            await worker_tasks.process_document({"job_try": 1}, str(job_id), str(worker_org.id))
        assert exc_info.value.defer_score is not None

        job = await JobRepository(worker_session, worker_org.id).get(job_id)
        assert job.status == JobStatus.PENDING.value
        # A retry is precisely the case an operator needs paged on if it spikes —
        # confirms the metric that was previously defined but never incremented.
        assert _metric_value(job_retries, error_code="provider_timeout") == before + 1

    async def test_retryable_result_exhausted_is_dead_lettered(
        self, worker_session, worker_org, monkeypatch
    ):
        from docflow.observability.metrics import jobs_dead_lettered

        job_id, document_id = await _make_job(worker_session, worker_org, max_attempts=3)
        _patch_service(monkeypatch, _result(retryable=True, error_code="malformed_model_output"))
        before = _metric_value(jobs_dead_lettered, error_code="malformed_model_output")

        await worker_tasks.process_document({"job_try": 3}, str(job_id), str(worker_org.id))

        job = await JobRepository(worker_session, worker_org.id).get(job_id)
        assert job.status == JobStatus.DEAD_LETTERED.value
        document = await DocumentRepository(worker_session, worker_org.id).get(document_id)
        assert document.status == DocumentStatus.FAILED.value
        assert document.error_code == "malformed_model_output"
        assert _metric_value(jobs_dead_lettered, error_code="malformed_model_output") == before + 1


class TestUnexpectedExceptions:
    """A bug or an error class nobody anticipated — not a `DocflowError` at all."""

    async def test_below_max_attempts_is_reraised(self, worker_session, worker_org, monkeypatch):
        job_id, _ = await _make_job(worker_session, worker_org, max_attempts=3)
        _patch_service(monkeypatch, RuntimeError("something nobody anticipated"))

        with pytest.raises(Retry) as exc_info:
            await worker_tasks.process_document({"job_try": 1}, str(job_id), str(worker_org.id))
        assert isinstance(exc_info.value.__cause__, RuntimeError)

    async def test_exhausted_fails_as_internal_error(self, worker_session, worker_org, monkeypatch):
        job_id, document_id = await _make_job(worker_session, worker_org, max_attempts=3)
        _patch_service(monkeypatch, RuntimeError("something nobody anticipated"))

        outcome = await worker_tasks.process_document(
            {"job_try": 3}, str(job_id), str(worker_org.id)
        )

        assert outcome == {"status": "failed", "error_code": "internal_error"}
        job = await JobRepository(worker_session, worker_org.id).get(job_id)
        assert job.status == JobStatus.FAILED.value
        document = await DocumentRepository(worker_session, worker_org.id).get(document_id)
        assert document.status == DocumentStatus.FAILED.value


class TestStaleSweep:
    """`sweep_stale_jobs` — the backstop for a worker that died mid-job and left
    a document at `processing` with nothing else that will ever revisit it."""

    async def test_recovers_a_document_stuck_past_the_threshold(self, worker_session, worker_org):
        long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
        job_id, document_id = await _make_job(
            worker_session, worker_org, processing_started_at=long_ago
        )

        outcome = await worker_tasks.sweep_stale_jobs({})

        assert outcome == {"stale_documents_recovered": 1}
        document = await DocumentRepository(worker_session, worker_org.id).get(document_id)
        assert document.status == DocumentStatus.FAILED.value
        assert document.error_code == "processing_timeout"
        job = await JobRepository(worker_session, worker_org.id).get(job_id)
        assert job.status == JobStatus.FAILED.value

    async def test_leaves_a_recently_started_document_alone(self, worker_session, worker_org):
        just_now = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=5)
        _, document_id = await _make_job(worker_session, worker_org, processing_started_at=just_now)

        outcome = await worker_tasks.sweep_stale_jobs({})

        assert outcome == {"stale_documents_recovered": 0}
        document = await DocumentRepository(worker_session, worker_org.id).get(document_id)
        assert document.status == DocumentStatus.PROCESSING.value

    async def test_ignores_documents_that_already_finished(self, worker_session, worker_org):
        long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
        _, document_id = await _make_job(worker_session, worker_org, processing_started_at=long_ago)
        await DocumentRepository(worker_session, worker_org.id).set_status(
            document_id, DocumentStatus.COMPLETED
        )
        await worker_session.commit()

        outcome = await worker_tasks.sweep_stale_jobs({})

        assert outcome == {"stale_documents_recovered": 0}


class TestMetricsEmission:
    """`record_document`/`record_stage`/`record_llm_call`/`record_validation_issue`
    were defined in `observability/metrics.py` but never called from anywhere —
    dead code that `docflow_documents_processed_total` etc. would report zero
    forever. Every other test either stubs `ProcessingService` entirely (the
    classes above) or calls `PipelineRunner` directly, bypassing
    `ProcessingService._persist` (`test_pipeline.py`) — so nothing exercises the
    real, unstubbed `process_document` end to end. This does, once, to prove the
    wiring actually reaches Prometheus and not just "doesn't crash".
    """

    async def test_a_real_run_populates_the_previously_dead_metrics(
        self, worker_session, worker_org, storage, provider, sample_invoice, monkeypatch
    ):
        from docflow.observability import metrics
        from docflow.pipeline import build_pipeline as real_build_pipeline
        from docflow.storage.base import build_key

        monkeypatch.setattr(
            worker_tasks,
            "build_pipeline",
            lambda **kwargs: real_build_pipeline(
                settings=kwargs["settings"], storage=storage, provider=provider
            ),
        )

        doc_id = uuid.uuid4()
        key = build_key(worker_org.id, doc_id, extension="txt")
        await storage.put(key, sample_invoice, content_type="text/plain")

        documents = DocumentRepository(worker_session, worker_org.id)
        document = await documents.create(
            id=doc_id,
            filename="invoice.txt",
            content_type="text/plain",
            size_bytes=len(sample_invoice),
            checksum_sha256=hashlib.sha256(sample_invoice).hexdigest(),
            storage_key=key,
            status=DocumentStatus.UPLOADED.value,
        )
        jobs = JobRepository(worker_session, worker_org.id)
        job = await jobs.create(
            document_id=document.id, idempotency_key=uuid.uuid4().hex, max_attempts=3
        )
        await worker_session.commit()

        stage_before = _metric_value(
            metrics.stage_duration, suffix="_count", stage="llm_extraction"
        )

        result = await worker_tasks.process_document(
            {"job_try": 1}, str(job.id), str(worker_org.id)
        )

        assert result["status"] in ("completed", "needs_review")

        assert (
            _metric_value(
                metrics.documents_processed,
                document_type="invoice",
                status=result["status"],
            )
            >= 1
        )
        assert (
            _metric_value(
                metrics.llm_calls,
                provider="fixture",
                model="fixture-heuristic",
                purpose="extraction",
            )
            >= 1
        )
        stage_after = _metric_value(metrics.stage_duration, suffix="_count", stage="llm_extraction")
        assert stage_after == stage_before + 1


# Sanity check on the fixture itself: DocflowError must actually be raisable
# with just a message, matching every concrete error class used above.
def test_docflow_error_constructs_with_message_only():
    err = DocflowError("test")
    assert err.message == "test"
