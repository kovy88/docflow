"""Relational schema.

Design notes that are easy to miss when skimming:

* **Every business table carries `organization_id`.** Not because the parent chain
  couldn't be walked, but because tenant isolation should be enforceable by a single
  predicate on the table being queried. A join-derived tenant check is one forgotten
  join away from a data leak; a column is not. It also lets every hot query use a
  composite `(organization_id, ...)` index.

* **Extractions are append-only and versioned.** Re-processing a document creates a
  new extraction row and marks the previous one `superseded` rather than mutating it.
  That is what makes "which model and prompt produced this result?" answerable
  months later, and what lets the evaluation harness replay history.

* **`documents.checksum_sha256` is unique per organization.** This is the content-
  addressed half of idempotency: uploading the same bytes twice cannot create two
  billable extraction jobs. The request-scoped half is `idempotency_key`.

See `docs/DATABASE.md` for the ER diagram and the index rationale.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from docflow.db.base import Base, TimestampMixin, new_id
from docflow.domain.enums import (
    ActorType,
    ConfidenceBand,
    DeliveryStatus,
    DocumentStatus,
    ExtractionStatus,
    ExtractorKind,
    FieldSource,
    JobStatus,
    OrgRole,
    PlanTier,
    ProcessingStage,
    ReviewAction,
    StepStatus,
    ValidationSeverity,
)

# JSONB everywhere: we need to index into extracted payloads (`data->>'total'`) for
# analytics and search, which plain JSON cannot do.
JSON_TYPE = JSONB


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)


def _enum_col(length: int = 32) -> String:
    """Enums are stored as VARCHAR with a CHECK constraint rather than PG ENUM types.

    Postgres enum types require `ALTER TYPE ... ADD VALUE` to extend, which cannot run
    inside a transaction block before PG12 and still cannot be reversed. VARCHAR +
    CHECK gives the same integrity with migrations that are ordinary DDL.
    """
    return String(length)


# =============================================================================
# Tenancy & identity
# =============================================================================


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)

    plan: Mapped[str] = mapped_column(_enum_col(), nullable=False, default=PlanTier.FREE.value)
    # Quota is stored on the org rather than derived from `plan` so that a single
    # customer can be granted a bespoke limit without inventing a new plan tier.
    monthly_document_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Per-org processing overrides; null keys fall back to global settings.
    settings: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("plan IN ('free','starter','business','enterprise')", name="plan_valid"),
        CheckConstraint("monthly_document_quota >= 0", name="quota_non_negative"),
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    # Argon2id hash. Never a plaintext or reversible value.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Membership(Base, TimestampMixin):
    """Join table between users and organizations, carrying the role.

    Modelled explicitly (rather than a `user.organization_id` column) because a
    consultant or accountant legitimately works across several client organizations,
    and because the role belongs to the *relationship*, not to the user.
    """

    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(_enum_col(), nullable=False, default=OrgRole.MEMBER.value)

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_memberships_user_org"),
        Index("ix_memberships_org_role", "organization_id", "role"),
        CheckConstraint("role IN ('owner','admin','member','viewer')", name="role_valid"),
    )


class ApiKey(Base, TimestampMixin):
    """Machine credentials for the public API and for n8n / webhook consumers.

    The full key is shown exactly once at creation. We persist only a SHA-256 digest
    (fast, and the key is already 256 bits of entropy so a slow KDF buys nothing) plus
    a short non-secret `prefix` used to display and look up the key without a table
    scan over every hash.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    hashed_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    scopes: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class RevokedToken(Base):
    """Refresh-token blocklist: a jti explicitly revoked before its own expiry.

    Deliberately **not** a session table recording every issued token — that would
    turn every login into a write and every authenticated request into a lookup.
    Access tokens are never revoked here; at a 30-minute TTL the remedy for a leaked
    one is to wait it out. Only refresh tokens (14-day TTL) are worth an explicit
    kill switch, and only on logout / "sign out everywhere" is a row written.

    No `organization_id`: `jti` is 24 bytes of `token_urlsafe` randomness embedded in
    the token itself, so a lookup can never be guided or enumerated cross-tenant —
    the isolation argument that motivates `organization_id` on business tables
    (see the module docstring) doesn't apply to a table keyed by an unguessable value.

    Rows past `expires_at` are dead weight — the JWT's own `exp` claim already
    rejects the token by then — so `RevokedTokenRepository.purge_expired()` exists
    for an operator to reclaim the space. Nothing calls it on a schedule yet; see
    docs/LIMITATIONS.md.
    """

    __tablename__ = "revoked_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The token's own `exp`, copied here so an expired blocklist entry can be told
    # apart from one still worth checking, without decoding a JWT to find out.
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )

    __table_args__ = (Index("ix_revoked_tokens_expires_at", "expires_at"),)


# =============================================================================
# Schema registry
# =============================================================================


class DocumentType(Base, TimestampMixin):
    """A configurable document type: what to extract and how to check it.

    `organization_id IS NULL` marks a built-in type shipped with the product. An
    organization can create its own types, or shadow a built-in one by registering a
    type with the same `key` — resolution prefers the org-scoped row. This is the
    mechanism that keeps the platform from being an invoice-only product.
    """

    __tablename__ = "document_types"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # JSON Schema describing the fields. The canonical definition for built-in types
    # is the Pydantic model in `docflow.schemas.types`; this column stores the
    # generated JSON Schema so that custom, DB-only types work through the same path.
    json_schema: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    # Keyword hints, per-field review thresholds, business-rule toggles.
    config: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "key", "version", name="uq_document_types_org_key_ver"),
        Index("ix_document_types_key_active", "key", "is_active"),
    )


class PromptVersion(Base, TimestampMixin):
    """Immutable snapshot of a prompt template.

    Prompts live in source control (`docflow.prompts`) — this table is the *audit*
    copy, written on first use, so that an extraction row can point at the exact text
    that produced it even after the source file has moved on.
    """

    __tablename__ = "prompt_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("key", "version", name="uq_prompt_versions_key_version"),)


# =============================================================================
# Documents & processing
# =============================================================================


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # Opaque key into object storage. Never exposed to clients — downloads go through
    # an authorised endpoint that mints a short-lived URL, so that knowing the key is
    # not sufficient to read the file.
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    text_storage_key: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[str] = mapped_column(
        _enum_col(), nullable=False, default=DocumentStatus.UPLOADED.value, index=True
    )
    document_type_key: Mapped[str | None] = mapped_column(String(64), index=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float)

    page_count: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int | None] = mapped_column(Integer)
    used_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    language: Mapped[str | None] = mapped_column(String(8))

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    processing_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    processing_finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    processing_ms: Mapped[int | None] = mapped_column(Integer)

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="web")
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)

    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[ProcessingJob]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Content-addressed dedupe. The dominant list query is
        # "documents for my org, newest first, optionally filtered by status",
        # which the two composite indexes below serve without a sort.
        UniqueConstraint("organization_id", "checksum_sha256", name="uq_documents_org_checksum"),
        Index("ix_documents_org_created", "organization_id", "created_at"),
        Index("ix_documents_org_status_created", "organization_id", "status", "created_at"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint(
            "status IN ('uploaded','queued','processing','needs_review',"
            "'completed','rejected','failed')",
            name="status_valid",
        ),
    )


class ProcessingJob(Base, TimestampMixin):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        _enum_col(), nullable=False, default=JobStatus.PENDING.value, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # arq job id. Deterministic (derived from the idempotency key) so that a
    # duplicated enqueue is a no-op inside arq itself, not just in our own table.
    queue_job_id: Mapped[str | None] = mapped_column(String(120), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    document: Mapped[Document] = relationship(back_populates="jobs")
    steps: Mapped[list[ProcessingStep]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ProcessingStep.sequence"
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_jobs_org_idempotency"),
        Index("ix_jobs_status_created", "status", "created_at"),
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','cancelled','dead_lettered')",
            name="job_status_valid",
        ),
    )


class ProcessingStep(Base):
    """One pipeline stage execution. Drives the UI timeline and per-stage latency."""

    __tablename__ = "processing_steps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(_enum_col(40), nullable=False)
    status: Mapped[str] = mapped_column(_enum_col(), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    # Non-sensitive stage output: counts, flags, model names. Never document text.
    detail: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)

    job: Mapped[ProcessingJob] = relationship(back_populates="steps")

    __table_args__ = (
        Index("ix_steps_stage_status", "stage", "status"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','skipped')", name="step_status_valid"
        ),
    )


# =============================================================================
# Extraction results
# =============================================================================


class Extraction(Base, TimestampMixin):
    """One attempt at turning a document into structured data.

    Append-only: reprocessing supersedes rather than overwrites. `data` holds the
    current values (including human edits); `extraction_fields` holds the per-field
    metadata that the review UI needs.
    """

    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(
        _enum_col(), nullable=False, default=ExtractionStatus.DRAFT.value, index=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # --- reproducibility block -------------------------------------------------
    # Together these answer "what exactly produced this row?". Every one of them is
    # required to reproduce a result; omitting any makes a regression un-debuggable.
    extractor: Mapped[str] = mapped_column(
        _enum_col(), nullable=False, default=ExtractorKind.LLM.value
    )
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    model_version: Mapped[str | None] = mapped_column(String(120))
    prompt_key: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    document_type_key: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    pipeline_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    # ---------------------------------------------------------------------------

    data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    raw_model_output: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)

    overall_confidence: Mapped[float | None] = mapped_column(Float, index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    review_reasons: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NUMERIC, not float: costs are money and get summed over millions of rows.
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document: Mapped[Document] = relationship(back_populates="extractions")
    fields: Mapped[list[ExtractionField]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )
    issues: Mapped[list[ValidationIssue]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_extractions_doc_current", "document_id", "is_current"),
        Index("ix_extractions_org_created", "organization_id", "created_at"),
        Index("ix_extractions_org_review", "organization_id", "needs_review", "status"),
        UniqueConstraint("document_id", "revision", name="uq_extractions_document_revision"),
        CheckConstraint(
            "status IN ('draft','needs_review','approved','rejected','superseded')",
            name="extraction_status_valid",
        ),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
    )


class ExtractionField(Base):
    """Per-field metadata: confidence, provenance, evidence, review state.

    Kept in its own table rather than nested inside `extractions.data` because the
    review UI, the confidence calibration job and the correction-rate metric all
    query across fields (`WHERE confidence_band = 'low'`), which a JSON blob cannot
    index efficiently.
    """

    __tablename__ = "extraction_fields"

    id: Mapped[uuid.UUID] = _uuid_pk()
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Dotted path, e.g. `total` or `line_items.0.unit_price`.
    field_path: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    # Wrapped as {"value": ...} so that JSON null and SQL NULL stay distinguishable.
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)

    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_band: Mapped[str | None] = mapped_column(_enum_col(16), index=True)
    confidence_signals: Mapped[dict[str, Any]] = mapped_column(
        JSON_TYPE, nullable=False, default=dict
    )
    source: Mapped[str] = mapped_column(_enum_col(), nullable=False, default=FieldSource.LLM.value)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    was_corrected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Verbatim supporting snippet from the source, for the "show me where" affordance.
    evidence_text: Mapped[str | None] = mapped_column(Text)
    evidence_page: Mapped[int | None] = mapped_column(Integer)

    extraction: Mapped[Extraction] = relationship(back_populates="fields")

    __table_args__ = (
        UniqueConstraint("extraction_id", "field_path", name="uq_fields_extraction_path"),
        Index("ix_fields_org_band", "organization_id", "confidence_band"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
    )


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[uuid.UUID] = _uuid_pk()
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    rule_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    field_path: Mapped[str | None] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(_enum_col(16), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    extraction: Mapped[Extraction] = relationship(back_populates="issues")

    __table_args__ = (
        CheckConstraint("severity IN ('error','warning','info')", name="severity_valid"),
        Index("ix_issues_rule_severity", "rule_id", "severity"),
    )


# =============================================================================
# Human review & feedback
# =============================================================================


class Review(Base, TimestampMixin):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    action: Mapped[str] = mapped_column(_enum_col(), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    # Wall-clock time the reviewer spent. This is the number that turns into the ROI
    # claim, so it is measured rather than assumed.
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    fields_corrected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    corrections: Mapped[list[FieldCorrection]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "action IN ('approve','reject','edit','reprocess')", name="review_action_valid"
        ),
    )


class FieldCorrection(Base):
    """A human changing a value. The training signal of the whole product.

    Every correction records the model/prompt/schema that produced the wrong value,
    so the corpus can be sliced by prompt version to answer "did v8 actually help?"
    without a join back through a possibly-superseded extraction row.
    """

    __tablename__ = "field_corrections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    field_path: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    old_confidence: Mapped[float | None] = mapped_column(Float)
    old_confidence_band: Mapped[str | None] = mapped_column(_enum_col(16))

    document_type_key: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    review: Mapped[Review] = relationship(back_populates="corrections")

    __table_args__ = (
        Index("ix_corrections_type_field", "document_type_key", "field_path"),
        Index("ix_corrections_prompt", "prompt_version", "field_path"),
    )


# =============================================================================
# Usage, audit, integrations
# =============================================================================


class UsageRecord(Base):
    """Append-only ledger of billable/costed events.

    Separate from `extractions` because usage must survive document deletion (you
    still owe for work already done) and because billing periods aggregate over a
    time index that `extractions` does not need.
    """

    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extractions.id", ondelete="SET NULL"), index=True
    )

    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Denormalised YYYY-MM so the billing rollup is an index scan, not a date_trunc
    # over the whole table.
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_usage_org_period", "organization_id", "billing_period"),
        Index("ix_usage_org_created", "organization_id", "created_at"),
    )


class AuditLog(Base):
    """Who did what to which resource. Security-relevant and append-only."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    actor_type: Mapped[str] = mapped_column(
        _enum_col(), nullable=False, default=ActorType.USER.value
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_label: Mapped[str | None] = mapped_column(String(320))

    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)

    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    request_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (Index("ix_audit_org_created", "organization_id", "created_at"),)


class WebhookEndpoint(Base, TimestampMixin):
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    # HMAC-SHA256 signing secret; receivers verify the `X-Docflow-Signature` header.
    secret: Mapped[str] = mapped_column(String(80), nullable=False)
    events: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(
        _enum_col(), nullable=False, default=DeliveryStatus.PENDING.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "ApiKey",
    "AuditLog",
    "Document",
    "DocumentType",
    "Extraction",
    "ExtractionField",
    "FieldCorrection",
    "Membership",
    "Organization",
    "ProcessingJob",
    "ProcessingStep",
    "PromptVersion",
    "Review",
    "UsageRecord",
    "User",
    "ValidationIssue",
    "WebhookDelivery",
    "WebhookEndpoint",
]

# Referenced for their enum values in CHECK constraints and defaults above; listed
# here so linters see the import as used and future edits keep them in sync.
_ENUMS_IN_USE = (
    ConfidenceBand,
    DocumentStatus,
    ExtractionStatus,
    ProcessingStage,
    ReviewAction,
    StepStatus,
    ValidationSeverity,
)
