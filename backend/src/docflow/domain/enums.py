"""Domain enumerations.

These are the vocabulary of the system and are referenced by the database, the API
contract and the frontend. They live in `domain/` because they are pure business
concepts with no I/O dependency.

String-valued enums (not integers) so that database rows and log lines are readable
without a lookup table, and so that adding a value never renumbers existing data.
"""

from __future__ import annotations

from enum import StrEnum


class DocumentStatus(StrEnum):
    """Lifecycle of a document, as shown to the user.

    This is deliberately coarse. Fine-grained progress lives in `ProcessingStage`,
    which is recorded per step; conflating the two produced a status field that
    changed too often to be useful for filtering.

    Terminal states: COMPLETED, REJECTED, FAILED.
    """

    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_DOCUMENT_STATUSES

    @property
    def is_in_flight(self) -> bool:
        return self in (DocumentStatus.QUEUED, DocumentStatus.PROCESSING)


_TERMINAL_DOCUMENT_STATUSES = frozenset(
    {DocumentStatus.COMPLETED, DocumentStatus.REJECTED, DocumentStatus.FAILED}
)


class ProcessingStage(StrEnum):
    """Explicit pipeline stages.

    Each stage is an independently testable unit with a typed input/output contract
    (see `docflow.pipeline.stage.Stage`). Persisting one row per stage is what makes
    the UI timeline and the per-stage latency metrics possible.
    """

    FILE_VALIDATION = "file_validation"
    METADATA_EXTRACTION = "metadata_extraction"
    TEXT_EXTRACTION = "text_extraction"
    OCR = "ocr"
    CLASSIFICATION = "classification"
    SCHEMA_SELECTION = "schema_selection"
    LLM_EXTRACTION = "llm_extraction"
    BASELINE_CROSSCHECK = "baseline_crosscheck"
    SCHEMA_VALIDATION = "schema_validation"
    BUSINESS_VALIDATION = "business_validation"
    CONFIDENCE_SCORING = "confidence_scoring"
    PERSISTENCE = "persistence"
    REVIEW_ROUTING = "review_routing"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTERED = "dead_lettered"


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExtractionStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ConfidenceBand(StrEnum):
    """Coarse confidence buckets shown in the UI.

    Users cannot act on `0.7314`; they can act on "check this one". Thresholds are
    defined in `docflow.domain.confidence` and are calibrated against the evaluation
    set rather than guessed.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FieldSource(StrEnum):
    """Where a field's current value came from — needed for the feedback loop."""

    LLM = "llm"
    BASELINE = "baseline"
    DERIVED = "derived"
    HUMAN = "human"


class ValidationSeverity(StrEnum):
    ERROR = "error"      # blocks approval
    WARNING = "warning"  # routes to review, does not block
    INFO = "info"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    REPROCESS = "reprocess"


class OrgRole(StrEnum):
    """Roles are ordered; `at_least` implements the hierarchy."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

    def at_least(self, required: OrgRole) -> bool:
        return _ROLE_RANK[self] >= _ROLE_RANK[required]


_ROLE_RANK: dict[OrgRole, int] = {
    OrgRole.VIEWER: 0,
    OrgRole.MEMBER: 1,
    OrgRole.ADMIN: 2,
    OrgRole.OWNER: 3,
}


class PlanTier(StrEnum):
    FREE = "free"
    STARTER = "starter"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class ExtractorKind(StrEnum):
    """Which extraction engine produced a result — LLM or the rule-based baseline.

    Recording this lets the evaluation harness compare engines over the same corpus
    and lets support answer "why did this document come out differently?".
    """

    LLM = "llm"
    BASELINE = "baseline"


class ActorType(StrEnum):
    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"


class WebhookEvent(StrEnum):
    DOCUMENT_PROCESSED = "document.processed"
    DOCUMENT_NEEDS_REVIEW = "document.needs_review"
    DOCUMENT_FAILED = "document.failed"
    DOCUMENT_APPROVED = "document.approved"
    DOCUMENT_REJECTED = "document.rejected"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
