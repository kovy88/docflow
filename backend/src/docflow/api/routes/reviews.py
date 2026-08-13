"""Human review routes: edit, approve, reject, review queue."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from docflow.api.deps import CurrentPrincipal, SessionDep
from docflow.api.schemas import (
    ApproveRequest,
    ExtractionResponse,
    RejectRequest,
    ReviewOutcomeResponse,
    UpdateExtractionRequest,
)
from docflow.api.serializers import serialize_extraction
from docflow.db.repositories import ExtractionRepository
from docflow.services.review_service import FieldEdit, ReviewService

router = APIRouter(tags=["review"])


@router.patch(
    "/documents/{document_id}/extraction",
    response_model=ReviewOutcomeResponse,
    summary="Correct extracted fields",
    description=(
        "Applies field-level corrections and re-runs all validation layers over the "
        "result. Every change is recorded in `field_corrections` with the model and "
        "prompt version that produced the original value — this is the feedback "
        "signal the evaluation harness consumes."
    ),
)
async def update_extraction(
    document_id: uuid.UUID,
    payload: UpdateExtractionRequest,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> ReviewOutcomeResponse:
    service = ReviewService(session, principal=principal)
    outcome = await service.apply_edits(
        document_id,
        [FieldEdit(field_path=e.field_path, value=e.value) for e in payload.edits],
        note=payload.note,
    )
    return ReviewOutcomeResponse(
        extraction_id=outcome.extraction.id,
        status=outcome.status.value,
        corrections_applied=outcome.corrections_applied,
        remaining_errors=outcome.remaining_errors,
        needs_review=outcome.extraction.needs_review,
    )


@router.post(
    "/documents/{document_id}/approve",
    response_model=ReviewOutcomeResponse,
    summary="Approve an extraction",
    description=(
        "Marks the extraction approved and the document completed. Refused while "
        "validation errors remain, unless `force` is set — a forced approval is "
        "recorded in the audit log with the errors it overrode."
    ),
)
async def approve(
    document_id: uuid.UUID,
    payload: ApproveRequest,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> ReviewOutcomeResponse:
    service = ReviewService(session, principal=principal)
    outcome = await service.approve(
        document_id,
        note=payload.note,
        duration_seconds=payload.duration_seconds,
        force=payload.force,
    )
    return ReviewOutcomeResponse(
        extraction_id=outcome.extraction.id,
        status=outcome.status.value,
        corrections_applied=0,
        remaining_errors=outcome.remaining_errors,
        needs_review=False,
    )


@router.post(
    "/documents/{document_id}/reject",
    response_model=ReviewOutcomeResponse,
    summary="Reject an extraction",
)
async def reject(
    document_id: uuid.UUID,
    payload: RejectRequest,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> ReviewOutcomeResponse:
    service = ReviewService(session, principal=principal)
    outcome = await service.reject(
        document_id, reason=payload.reason, duration_seconds=payload.duration_seconds
    )
    return ReviewOutcomeResponse(
        extraction_id=outcome.extraction.id,
        status=outcome.status.value,
        corrections_applied=0,
        remaining_errors=0,
        needs_review=False,
    )


@router.get(
    "/reviews/queue",
    response_model=list[ExtractionResponse],
    summary="Documents awaiting review",
    description="Lowest confidence first — the queue is ordered by where a human adds most value.",
)
async def review_queue(
    session: SessionDep,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ExtractionResponse]:
    extractions = await ExtractionRepository(session, principal.organization_id).review_queue(
        limit=limit, offset=offset
    )
    return [serialize_extraction(e) for e in extractions]
