"""Dashboard, usage and export.

The numbers here are the ones a buyer asks about: how much did this cost, how much
human time did it need, is it getting better. All derived from recorded facts —
nothing is estimated except the LLM cost, which is labelled as an estimate with the
date of the price table it used.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from docflow.api.deps import CurrentPrincipal, SessionDep, SettingsDep
from docflow.api.schemas import (
    CorrectionStat,
    DashboardResponse,
    DocumentTypeResponse,
    UsageResponse,
)
from docflow.db.models import Document, Extraction, Review
from docflow.db.repositories import (
    DocumentRepository,
    OrganizationRepository,
    ReviewRepository,
    UsageRepository,
)
from docflow.domain.enums import DocumentStatus
from docflow.domain.errors import ResourceNotFoundError
from docflow.llm.pricing import PRICING_AS_OF
from docflow.schemas.registry import get_registry
from docflow.services.document_service import current_billing_period

router = APIRouter(tags=["analytics"])


@router.get("/dashboard", response_model=DashboardResponse, summary="Dashboard metrics")
async def dashboard(
    session: SessionDep,
    principal: CurrentPrincipal,
) -> DashboardResponse:
    org_id = principal.organization_id
    documents = DocumentRepository(session, org_id)
    usage = UsageRepository(session, org_id)

    counts = await documents.status_counts()
    total = sum(counts.values())
    period = current_billing_period()
    period_start = dt.datetime.now(dt.UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    organization = await OrganizationRepository(session).get(org_id)
    if organization is None:
        raise ResourceNotFoundError("Organization not found")

    processed_this_period = await documents.count_in_period(since=period_start)
    totals = await usage.period_totals(period)

    # Success rate counts only documents that reached a terminal state. Including
    # in-flight documents would make the number sag every time a batch is uploaded.
    terminal = sum(
        counts.get(s.value, 0)
        for s in (
            DocumentStatus.COMPLETED,
            DocumentStatus.NEEDS_REVIEW,
            DocumentStatus.REJECTED,
            DocumentStatus.FAILED,
        )
    )
    succeeded = counts.get(DocumentStatus.COMPLETED.value, 0) + counts.get(
        DocumentStatus.NEEDS_REVIEW.value, 0
    )
    success_rate = round(succeeded / terminal, 4) if terminal else None

    avg_ms = await session.scalar(
        select(func.avg(Document.processing_ms)).where(
            Document.organization_id == org_id, Document.processing_ms.is_not(None)
        )
    )

    reviewable = await session.scalar(
        select(func.count()).select_from(Extraction).where(
            Extraction.organization_id == org_id, Extraction.is_current.is_(True)
        )
    )
    needing_review = await session.scalar(
        select(func.count()).select_from(Extraction).where(
            Extraction.organization_id == org_id,
            Extraction.is_current.is_(True),
            Extraction.needs_review.is_(True),
        )
    )
    review_rate = (
        round(int(needing_review or 0) / int(reviewable), 4) if reviewable else None
    )

    cost = float(totals["cost_usd"])
    return DashboardResponse(
        total_documents=total,
        by_status=counts,
        needs_review=counts.get(DocumentStatus.NEEDS_REVIEW.value, 0),
        processed_this_period=processed_this_period,
        quota=organization.monthly_document_quota,
        quota_used=processed_this_period,
        success_rate=success_rate,
        avg_processing_ms=float(avg_ms) if avg_ms is not None else None,
        review_rate=review_rate,
        cost_usd_this_period=cost,
        cost_per_document=(
            round(cost / processed_this_period, 6) if processed_this_period else None
        ),
        pricing_as_of=PRICING_AS_OF,
        daily=await usage.daily_series(days=30),
    )


@router.get("/usage", response_model=UsageResponse, summary="Usage for a billing period")
async def usage_summary(
    session: SessionDep,
    principal: CurrentPrincipal,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
) -> UsageResponse:
    org_id = principal.organization_id
    billing_period = period or current_billing_period()
    totals = await UsageRepository(session, org_id).period_totals(billing_period)

    organization = await OrganizationRepository(session).get(org_id)
    if organization is None:
        raise ResourceNotFoundError("Organization not found")

    start = dt.datetime.strptime(billing_period, "%Y-%m").replace(tzinfo=dt.UTC)
    documents = await DocumentRepository(session, org_id).count_in_period(since=start)

    return UsageResponse(
        billing_period=billing_period,
        documents=documents,
        events=totals["events"],
        input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"],
        cost_usd=float(totals["cost_usd"]),
        quota=organization.monthly_document_quota,
        plan=organization.plan,
    )


@router.get(
    "/analytics/corrections",
    response_model=list[CorrectionStat],
    summary="Most-corrected fields",
    description=(
        "Which fields humans actually fix, ranked. This is the improvement backlog: "
        "it reflects this organization's documents rather than the evaluation corpus."
    ),
)
async def correction_stats(
    session: SessionDep,
    principal: CurrentPrincipal,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> list[CorrectionStat]:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    rows = await ReviewRepository(session, principal.organization_id).correction_stats(
        since=since
    )
    return [
        CorrectionStat(document_type_key=r[0], field_path=r[1], corrections=r[2])
        for r in rows
    ]


@router.get(
    "/document-types",
    response_model=list[DocumentTypeResponse],
    summary="Configured document types",
)
async def document_types(principal: CurrentPrincipal) -> list[DocumentTypeResponse]:
    specs = get_registry().list_specs(str(principal.organization_id))
    return [
        DocumentTypeResponse(
            key=s.key,
            name=s.name,
            description=s.description,
            version=s.version,
            is_builtin=True,
            field_count=len(s.fields),
            required_fields=sorted(s.required_paths),
            critical_fields=sorted(s.critical_paths),
            review_threshold=s.review_threshold,
            rules=list(s.rule_ids),
        )
        for s in specs
    ]


@router.get(
    "/export",
    summary="Export extractions as CSV or JSON",
    description=(
        "Bulk export of approved/completed extractions. The integration escape "
        "hatch for tools that do not speak webhooks — most accounting software "
        "imports CSV."
    ),
)
async def export(
    session: SessionDep,
    settings: SettingsDep,
    principal: CurrentPrincipal,
    fmt: Annotated[str, Query(alias="format", pattern="^(csv|json)$")] = "csv",
    document_type: Annotated[str | None, Query()] = None,
    since: Annotated[dt.datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> Response:
    query = (
        select(Extraction)
        .where(
            Extraction.organization_id == principal.organization_id,
            Extraction.is_current.is_(True),
        )
        .order_by(Extraction.created_at.desc())
        .limit(limit)
    )
    if document_type:
        query = query.where(Extraction.document_type_key == document_type)
    if since:
        query = query.where(Extraction.created_at >= since)

    rows = (await session.execute(query)).scalars().all()

    records: list[dict[str, Any]] = []
    for extraction in rows:
        record = {
            "document_id": str(extraction.document_id),
            "document_type": extraction.document_type_key,
            "status": extraction.status,
            "confidence": extraction.overall_confidence,
            "extracted_at": extraction.created_at.isoformat(),
            "model": extraction.model,
            "prompt_version": extraction.prompt_version,
        }
        record.update(_flatten_for_export(extraction.data or {}))
        records.append(record)

    if fmt == "json":
        return Response(
            content=json.dumps(records, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="docflow-export.json"'},
        )

    buffer = io.StringIO()
    if records:
        # Union of keys across rows: documents of the same type can have different
        # optional fields present, and a header taken from row one would silently
        # drop columns.
        fieldnames: list[str] = []
        for record in records:
            for key in record:
                if key not in fieldnames:
                    fieldnames.append(key)
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="docflow-export.csv"'},
    )


def _flatten_for_export(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested data into CSV-friendly columns.

    Lists are JSON-encoded rather than exploded into columns: an invoice with three
    line items and one with twenty cannot share a column layout, and exploding
    would produce a ragged file most importers reject.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten_for_export(value, f"{path}."))
        elif isinstance(value, list):
            out[path] = json.dumps(value, default=str) if value else ""
        else:
            out[path] = value
    return out
