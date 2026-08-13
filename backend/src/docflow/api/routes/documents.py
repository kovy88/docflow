"""Document routes.

Handlers stay thin: validate input, call a service, serialise the result. Business
logic lives in `docflow.services`, which is what makes it testable without HTTP and
reusable from the worker and the evaluation harness.

Note the tenant-isolation property throughout: no handler compares
`document.organization_id` to the caller's. The repository's `WHERE` clause already
did, and a miss is a 404 — see `docflow.db.repositories`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile, status

from docflow.api.deps import CurrentPrincipal, SessionDep, SettingsDep, StorageDep
from docflow.api.schemas import (
    DocumentDetail,
    DocumentSummary,
    ExtractionResponse,
    JobStatusResponse,
    Page,
    ProcessingStepResponse,
    UploadResponse,
)
from docflow.api.serializers import serialize_extraction
from docflow.db.repositories import DocumentRepository, ExtractionRepository, JobRepository
from docflow.domain.enums import DocumentStatus, OrgRole
from docflow.domain.errors import AuthorizationError, ResourceNotFoundError
from docflow.services.document_service import DocumentService, queue_job_id
from docflow.worker.queue import enqueue_processing

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document for processing",
    description=(
        "Accepts the file, stores it, and queues processing. Returns immediately with "
        "`202 Accepted` — extraction happens asynchronously. Poll "
        "`GET /documents/{id}/status` or subscribe to a webhook for completion.\n\n"
        "Send an `Idempotency-Key` header to make retries safe. Uploading identical "
        "content twice returns the original document with `duplicate: true` and does "
        "not create a second billable job."
    ),
)
async def upload_document(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    principal: CurrentPrincipal,
    file: Annotated[UploadFile, File(description="PDF, image, DOCX or plain text")],
    document_type: Annotated[
        str | None, Form(description="Skip classification by naming the type")
    ] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> UploadResponse:
    if not principal.can(OrgRole.MEMBER):
        raise AuthorizationError("Uploading documents requires the member role or higher")

    service = DocumentService(
        session, principal=principal, storage=storage, settings=settings
    )
    result = await service.upload(
        file.file,
        filename=file.filename or "document",
        declared_content_type=file.content_type,
        idempotency_key=idempotency_key,
        document_type=document_type,
        source="api" if principal.actor_type.value == "api_key" else "web",
    )

    # Commit before enqueueing. The worker will look the job up by id, and a job
    # queued against an uncommitted row is a guaranteed "job not found" race.
    await session.commit()

    if not result.is_duplicate or result.job.status == "pending":
        await enqueue_processing(
            job_id=result.job.id,
            organization_id=principal.organization_id,
            job_key=queue_job_id(result.job.idempotency_key),
            request_id=getattr(request.state, "request_id", None),
        )

    return UploadResponse(
        document_id=result.document.id,
        job_id=result.job.id,
        status=result.document.status,
        duplicate=result.is_duplicate,
        message=(
            "This document was already uploaded; returning the existing record."
            if result.is_duplicate
            else None
        ),
    )


@router.get("", response_model=Page[DocumentSummary], summary="List documents")
async def list_documents(
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    document_status: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    document_type: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[DocumentSummary]:
    service = DocumentService(
        session, principal=principal, storage=storage, settings=settings
    )
    documents, total = await service.documents.list(
        limit=limit,
        offset=offset,
        status=document_status,
        document_type=document_type,
        search=search,
    )
    return Page(
        items=[DocumentSummary.model_validate(d) for d in documents],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentDetail, summary="Get a document")
async def get_document(
    document_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    principal: CurrentPrincipal,
) -> DocumentDetail:
    service = DocumentService(
        session, principal=principal, storage=storage, settings=settings
    )
    document = await service.documents.get(document_id)
    if document is None:
        raise ResourceNotFoundError("Document not found")
    return DocumentDetail.model_validate(document)


@router.get(
    "/{document_id}/status",
    response_model=JobStatusResponse,
    summary="Poll processing status",
)
async def get_status(
    document_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    principal: CurrentPrincipal,
) -> JobStatusResponse:
    service = DocumentService(
        session, principal=principal, storage=storage, settings=settings
    )
    document = await service.documents.get(document_id)
    if document is None:
        raise ResourceNotFoundError("Document not found")

    job = await service.jobs.latest_for_document(document_id)
    return JobStatusResponse(
        document_id=document.id,
        job_id=job.id if job else None,
        status=document.status,
        job_status=job.status if job else None,
        attempt=job.attempt if job else 0,
        max_attempts=job.max_attempts if job else 0,
        error_code=document.error_code,
        error_message=document.error_message,
        started_at=job.started_at if job else None,
        finished_at=job.finished_at if job else None,
        duration_ms=document.processing_ms,
    )


@router.get(
    "/{document_id}/timeline",
    response_model=list[ProcessingStepResponse],
    summary="Per-stage processing timeline",
)
async def get_timeline(
    document_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> list[ProcessingStepResponse]:
    # Confirm the document exists *in this organization* before returning steps.
    # `steps_for_document` is already org-scoped through its join, so this is not
    # what stops a leak — it makes the response consistent with every other
    # document endpoint (404 rather than an empty 200), and means a future refactor
    # of that join cannot silently turn this into a leak.
    documents = DocumentRepository(session, principal.organization_id)
    if await documents.get(document_id) is None:
        raise ResourceNotFoundError("Document not found")

    steps = await JobRepository(session, principal.organization_id).steps_for_document(
        document_id
    )
    return [ProcessingStepResponse.model_validate(s) for s in steps]


@router.get(
    "/{document_id}/extraction",
    response_model=ExtractionResponse,
    summary="Get the current extraction",
)
async def get_extraction(
    document_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> ExtractionResponse:
    extraction = await ExtractionRepository(
        session, principal.organization_id
    ).current_for_document(document_id)
    if extraction is None:
        raise ResourceNotFoundError("No extraction is available for this document")
    return serialize_extraction(extraction)


@router.post(
    "/{document_id}/reprocess",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reprocess a document",
    description=(
        "Runs the pipeline again with the current model, prompt and schema versions. "
        "The previous extraction is kept and marked superseded, so history is never "
        "lost. Refused while the document is already being processed."
    ),
)
async def reprocess_document(
    document_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    principal: CurrentPrincipal,
) -> UploadResponse:
    if not principal.can(OrgRole.MEMBER):
        raise AuthorizationError("Reprocessing requires the member role or higher")

    service = DocumentService(
        session, principal=principal, storage=storage, settings=settings
    )
    job = await service.reprocess(document_id)
    await session.commit()

    await enqueue_processing(
        job_id=job.id,
        organization_id=principal.organization_id,
        job_key=queue_job_id(job.idempotency_key),
        request_id=getattr(request.state, "request_id", None),
    )
    return UploadResponse(
        document_id=document_id, job_id=job.id, status=DocumentStatus.QUEUED.value
    )


@router.get("/{document_id}/download", summary="Get a time-limited download URL")
async def download_document(
    document_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    principal: CurrentPrincipal,
) -> dict[str, str | int]:
    service = DocumentService(
        session, principal=principal, storage=storage, settings=settings
    )
    url = await service.download_url(document_id)
    return {"url": url, "expires_in": settings.storage.presign_ttl_seconds}


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its stored files",
)
async def delete_document(
    document_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    principal: CurrentPrincipal,
) -> None:
    if not principal.can(OrgRole.ADMIN):
        raise AuthorizationError("Deleting documents requires the admin role")
    service = DocumentService(
        session, principal=principal, storage=storage, settings=settings
    )
    await service.delete(document_id)
