"""Document ingestion and job orchestration.

This is where the idempotency story lives, and it is the part of the system most
worth being careful about: every duplicate job is a duplicate LLM bill.

## Three layers of duplicate protection

1. **Content addressing.** `documents` has a unique constraint on
   `(organization_id, checksum_sha256)`. The same bytes cannot become two
   documents in one organization, whatever the client does. This catches the
   common real case — a user clicking upload twice, or a mail integration
   re-delivering the same attachment.

2. **Explicit idempotency keys.** A client may send `Idempotency-Key`. Jobs are
   unique on `(organization_id, idempotency_key)`, so a network retry of a request
   whose response was lost returns the original job instead of starting a second.

3. **Deterministic queue job ids.** The arq job id is derived from the idempotency
   key, so even a double-enqueue inside one request is a no-op at the queue level.

Each layer alone has a gap. Content addressing does not help a client that retries
before the first insert commits; idempotency keys are optional; queue-level dedupe
expires with the arq keep-result window. Together they close the realistic cases.

## Status transitions are guarded

`reprocess` refuses a document that is already in flight. Without that check, an
impatient user clicking "reprocess" three times gets three concurrent pipelines
writing to the same document, and the last writer wins nondeterministically.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from typing import BinaryIO

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.config import Settings
from docflow.db.models import Document, ProcessingJob
from docflow.db.repositories import (
    AuditRepository,
    DocumentRepository,
    JobRepository,
    OrganizationRepository,
)
from docflow.documents.validation import validate_upload
from docflow.domain.enums import DocumentStatus, JobStatus
from docflow.domain.errors import (
    ConflictError,
    DuplicateDocumentError,
    QuotaExceededError,
    ResourceNotFoundError,
)
from docflow.security.tokens import AuthPrincipal
from docflow.storage.base import StorageBackend, build_key

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    job: ProcessingJob
    duplicate_of: uuid.UUID | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        principal: AuthPrincipal,
        storage: StorageBackend,
        settings: Settings,
    ) -> None:
        self._session = session
        self._principal = principal
        self._storage = storage
        self._settings = settings
        self._org_id = principal.organization_id
        self.documents = DocumentRepository(session, self._org_id)
        self.jobs = JobRepository(session, self._org_id)
        self._orgs = OrganizationRepository(session)
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ upload

    async def upload(
        self,
        stream: BinaryIO,
        *,
        filename: str,
        declared_content_type: str | None,
        idempotency_key: str | None = None,
        document_type: str | None = None,
        source: str = "web",
    ) -> UploadResult:
        upload, data = validate_upload(
            stream,
            filename=filename,
            declared_content_type=declared_content_type,
            settings=self._settings.upload,
        )

        await self._assert_within_quota()

        existing = await self.documents.find_by_checksum(upload.checksum_sha256)
        if existing is not None:
            job = await self.jobs.latest_for_document(existing.id)
            if job is None:
                job = await self._create_and_enqueue(existing, idempotency_key)
            logger.info(
                "document.duplicate_upload",
                document_id=str(existing.id),
                organization_id=str(self._org_id),
            )
            return UploadResult(document=existing, job=job, duplicate_of=existing.id)

        document_id = uuid.uuid4()
        storage_key = build_key(
            self._org_id,
            document_id,
            kind="original",
            extension=_extension_for(upload.content_type, upload.filename),
        )

        # Storage write precedes the database insert. If the insert fails we leak an
        # orphaned object, which a lifecycle rule reclaims. The other order risks a
        # committed row pointing at an object that was never written — a document
        # that exists in the UI and fails forever in the worker.
        await self._storage.put(storage_key, data, content_type=upload.content_type)

        try:
            document = await self.documents.create(
                id=document_id,
                uploaded_by_id=self._principal.user_id,
                filename=upload.filename,
                content_type=upload.content_type,
                size_bytes=upload.size_bytes,
                checksum_sha256=upload.checksum_sha256,
                storage_key=storage_key,
                status=DocumentStatus.UPLOADED.value,
                document_type_key=document_type,
                source=source,
                doc_metadata={
                    "declared_content_type": declared_content_type,
                    "type_mismatch": upload.type_mismatch,
                },
            )
        except IntegrityError:
            # Lost a race with a concurrent identical upload. The other request's
            # document is the canonical one.
            await self._session.rollback()
            duplicate = await self.documents.find_by_checksum(upload.checksum_sha256)
            if duplicate is None:
                raise DuplicateDocumentError("This document has already been uploaded") from None
            job = await self.jobs.latest_for_document(duplicate.id)
            if job is None:
                job = await self._create_and_enqueue(duplicate, idempotency_key)
            return UploadResult(document=duplicate, job=job, duplicate_of=duplicate.id)

        job = await self._create_and_enqueue(document, idempotency_key)

        self._audit.record(
            organization_id=self._org_id,
            actor_type=self._principal.actor_type.value,
            actor_id=self._principal.actor_id,
            actor_label=self._principal.label,
            action="document.uploaded",
            resource_type="document",
            resource_id=document.id,
            meta={"filename": upload.filename, "size_bytes": upload.size_bytes, "source": source},
        )
        return UploadResult(document=document, job=job)

    async def _create_and_enqueue(
        self, document: Document, idempotency_key: str | None
    ) -> ProcessingJob:
        key = idempotency_key or _default_idempotency_key(document)

        existing = await self.jobs.find_by_idempotency_key(key)
        if existing is not None:
            logger.info("job.idempotent_hit", job_id=str(existing.id), document_id=str(document.id))
            return existing

        job = await self.jobs.create(
            document_id=document.id,
            idempotency_key=key,
            max_attempts=self._settings.processing.max_attempts,
        )
        document.status = DocumentStatus.QUEUED.value
        return job

    # --------------------------------------------------------------- lifecycle

    async def reprocess(self, document_id: uuid.UUID, *, reason: str = "manual") -> ProcessingJob:
        document = await self.documents.get(document_id)
        if document is None:
            raise ResourceNotFoundError("Document not found")

        status = DocumentStatus(document.status)
        if status.is_in_flight:
            raise ConflictError(
                "This document is already being processed. Wait for it to finish before reprocessing."
            )

        await self._assert_within_quota()

        # A fresh key per reprocess request, otherwise the idempotency lookup would
        # return the original job and reprocessing would silently do nothing.
        key = f"reprocess:{document_id}:{uuid.uuid4().hex[:12]}"
        job = await self.jobs.create(
            document_id=document.id,
            idempotency_key=key,
            max_attempts=self._settings.processing.max_attempts,
        )
        document.status = DocumentStatus.QUEUED.value
        document.error_code = None
        document.error_message = None

        self._audit.record(
            organization_id=self._org_id,
            actor_type=self._principal.actor_type.value,
            actor_id=self._principal.actor_id,
            actor_label=self._principal.label,
            action="document.reprocess",
            resource_type="document",
            resource_id=document.id,
            meta={"reason": reason, "job_id": str(job.id)},
        )
        return job

    async def delete(self, document_id: uuid.UUID) -> None:
        document = await self.documents.get(document_id)
        if document is None:
            raise ResourceNotFoundError("Document not found")

        keys = [k for k in (document.storage_key, document.text_storage_key) if k]
        await self.documents.delete(document_id)

        # Storage cleanup after the row is gone. A failure here leaks an object;
        # deleting the object first and then failing the row delete would leave a
        # document in the UI whose file 404s.
        for key in keys:
            try:
                await self._storage.delete(key)
            except Exception:
                logger.warning("document.storage_cleanup_failed", key=key)

        self._audit.record(
            organization_id=self._org_id,
            actor_type=self._principal.actor_type.value,
            actor_id=self._principal.actor_id,
            actor_label=self._principal.label,
            action="document.deleted",
            resource_type="document",
            resource_id=document_id,
        )

    async def download_url(self, document_id: uuid.UUID) -> str:
        document = await self.documents.get(document_id)
        if document is None:
            raise ResourceNotFoundError("Document not found")
        return await self._storage.presigned_url(
            document.storage_key,
            expires_in=self._settings.storage.presign_ttl_seconds,
            filename=document.filename,
        )

    # ------------------------------------------------------------------ quota

    async def _assert_within_quota(self) -> None:
        org = await self._orgs.get(self._org_id)
        if org is None:
            raise ResourceNotFoundError("Organization not found")
        if org.monthly_document_quota <= 0:
            return  # 0 means unmetered

        used = await self.documents.count_in_period(since=_billing_period_start())
        if used >= org.monthly_document_quota:
            raise QuotaExceededError(
                f"Your plan allows {org.monthly_document_quota} documents per month "
                f"and you have used {used}. Upgrade to continue.",
                detail={
                    "used": used,
                    "quota": org.monthly_document_quota,
                    "plan": org.plan,
                    "resets_at": _next_billing_period_start().isoformat(),
                },
            )


def _default_idempotency_key(document: Document) -> str:
    """Derived from the content hash when the client supplies no key.

    Means an unkeyed re-upload of identical bytes reuses the original job rather
    than creating a second one — the common case for an email integration that
    re-delivers, or a user who refreshes the upload page.
    """
    return f"auto:{document.checksum_sha256[:32]}"


def queue_job_id(idempotency_key: str) -> str:
    """Deterministic arq job id.

    arq drops an enqueue whose job id already exists, so this makes double-enqueue
    a no-op inside the queue itself, not just in our table.
    """
    return "docflow:" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]


def _extension_for(content_type: str, filename: str) -> str:
    mapping = {
        "application/pdf": "pdf",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/tiff": "tiff",
        "image/webp": "webp",
        "text/plain": "txt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }
    if extension := mapping.get(content_type):
        return extension
    _, _, suffix = filename.rpartition(".")
    return suffix.lower()[:8] if suffix and suffix != filename else "bin"


def _billing_period_start(now: dt.datetime | None = None) -> dt.datetime:
    moment = now or dt.datetime.now(dt.UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_billing_period_start(now: dt.datetime | None = None) -> dt.datetime:
    start = _billing_period_start(now)
    return (start.replace(day=28) + dt.timedelta(days=8)).replace(day=1)


def current_billing_period(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.UTC)).strftime("%Y-%m")


__all__ = [
    "DocumentService",
    "JobStatus",
    "UploadResult",
    "current_billing_period",
    "queue_job_id",
]
