"""ORM → API translation.

One place that knows how a database row becomes a response body, so the mapping
cannot drift between the two routes that return the same shape.
"""

from __future__ import annotations

from docflow.api.schemas import (
    ExtractionFieldResponse,
    ExtractionResponse,
    ValidationIssueResponse,
)
from docflow.db.models import Extraction


def serialize_extraction(extraction: Extraction) -> ExtractionResponse:
    fields = [
        ExtractionFieldResponse(
            field_path=f.field_path,
            label=f.label,
            value=(f.value or {}).get("value"),
            confidence=f.confidence,
            confidence_band=f.confidence_band,
            source=f.source,
            is_required=f.is_required,
            needs_review=f.needs_review,
            was_corrected=f.was_corrected,
            evidence_text=f.evidence_text,
            reasons=list((f.confidence_signals or {}).get("reasons", [])),
        )
        for f in sorted(extraction.fields, key=_field_order)
    ]

    return ExtractionResponse(
        id=extraction.id,
        document_id=extraction.document_id,
        status=extraction.status,
        revision=extraction.revision,
        document_type_key=extraction.document_type_key,
        schema_version=extraction.schema_version,
        data=extraction.data or {},
        fields=fields,
        issues=[ValidationIssueResponse.model_validate(i) for i in extraction.issues],
        overall_confidence=extraction.overall_confidence,
        needs_review=extraction.needs_review,
        review_reasons=list(extraction.review_reasons or []),
        created_at=extraction.created_at,
        provider=extraction.provider,
        model=extraction.model,
        model_version=extraction.model_version,
        prompt_key=extraction.prompt_key,
        prompt_version=extraction.prompt_version,
        extractor=extraction.extractor,
        input_tokens=extraction.input_tokens,
        output_tokens=extraction.output_tokens,
        cost_usd=float(extraction.cost_usd or 0),
        latency_ms=extraction.latency_ms,
    )


def _field_order(field: object) -> tuple[int, int, str]:
    """Order fields for the review UI: work first, then everything else.

    Reviewers open a document to fix problems. Putting the fields that need
    attention at the top — required-and-flagged, then flagged, then the rest —
    means the work is visible without scrolling, and a document with one bad field
    out of thirty takes one glance rather than a scan.
    """
    needs_review = bool(getattr(field, "needs_review", False))
    required = bool(getattr(field, "is_required", False))
    path = str(getattr(field, "field_path", ""))
    if needs_review and required:
        bucket = 0
    elif needs_review:
        bucket = 1
    elif required:
        bucket = 2
    else:
        bucket = 3
    # Nested paths sort after their parents within a bucket.
    return bucket, path.count("."), path
