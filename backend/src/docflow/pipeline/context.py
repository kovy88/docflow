"""Pipeline context — the single mutable object threaded through the stages.

Each stage reads what earlier stages produced and writes its own output. The
alternative — passing a growing tuple of values between stage functions — makes
every stage signature change when any stage learns a new output, and makes partial
results unavailable when something fails midway.

Partial results matter here: a document that fails at extraction should still show
the user its page count, detected type and extracted text in the UI. The context
carries whatever was completed before the failure.

**Nothing in this module performs I/O.** Stages hold their own dependencies
(storage, provider, session); the context is data.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from docflow.documents.classification import ClassificationResult
from docflow.documents.text_extraction import ExtractedText
from docflow.domain.confidence import FieldConfidence
from docflow.domain.enums import ProcessingStage, StepStatus
from docflow.extraction.baseline import BaselineResult
from docflow.extraction.extractor import ExtractionOutcome
from docflow.schemas.base import DocumentTypeSpec
from docflow.validation.engine import Issue


@dataclass(slots=True)
class StepRecord:
    """One stage execution, persisted to `processing_steps`."""

    stage: ProcessingStage
    status: StepStatus
    sequence: int
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineContext:
    # --- identity (set at construction) --------------------------------------
    document_id: uuid.UUID
    organization_id: uuid.UUID
    job_id: uuid.UUID
    attempt: int = 1
    request_id: str | None = None

    # --- input ---------------------------------------------------------------
    filename: str = ""
    content_type: str = ""
    storage_key: str = ""
    file_bytes: bytes | None = None
    checksum_sha256: str = ""
    # Caller-supplied type, when the API client already knows. Skips classification
    # entirely — an integration posting to `/documents?document_type=invoice` should
    # not pay for a guess it does not need.
    requested_type_key: str | None = None

    # --- stage outputs -------------------------------------------------------
    extracted: ExtractedText | None = None
    classification: ClassificationResult | None = None
    spec: DocumentTypeSpec | None = None
    extraction: ExtractionOutcome | None = None
    baseline: BaselineResult | None = None
    issues: list[Issue] = field(default_factory=list)
    field_confidences: list[FieldConfidence] = field(default_factory=list)
    overall_confidence: float | None = None
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

    # --- accounting ----------------------------------------------------------
    steps: list[StepRecord] = field(default_factory=list)
    total_cost_usd: Decimal = Decimal("0")
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    llm_calls: int = 0

    # --- results -------------------------------------------------------------
    extraction_id: uuid.UUID | None = None
    text_storage_key: str | None = None
    failed: bool = False
    error_code: str | None = None
    error_message: str | None = None

    @property
    def document_text(self) -> str:
        return self.extracted.text if self.extracted else ""

    @property
    def document_type_key(self) -> str:
        if self.spec is not None:
            return self.spec.key
        if self.classification is not None:
            return self.classification.document_type_key
        return self.requested_type_key or "generic"

    @property
    def page_count(self) -> int | None:
        return self.extracted.page_count if self.extracted else None

    @property
    def used_ocr(self) -> bool:
        return bool(self.extracted and self.extracted.used_ocr)

    def add_review_reason(self, reason: str) -> None:
        if reason not in self.review_reasons:
            self.review_reasons.append(reason)

    def log_fields(self) -> dict[str, Any]:
        """Correlation fields attached to every log line for this document.

        Never includes document content or extracted values — see
        `docs/SECURITY.md` on safe logging.
        """
        return {
            "document_id": str(self.document_id),
            "organization_id": str(self.organization_id),
            "job_id": str(self.job_id),
            "attempt": self.attempt,
            "request_id": self.request_id,
        }
