"""API request/response models.

Deliberately separate from the SQLAlchemy models. Serialising ORM objects directly
couples the public contract to the database schema, so a column rename becomes a
breaking API change, and it makes accidental over-exposure the default — the first
time someone adds `internal_notes` to a table it appears in the API.

These models are the contract. They also generate the OpenAPI document, so the
`description` and `examples` here are the API documentation.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


# ------------------------------------------------------------------------ auth


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)
    organization_name: str = Field(min_length=2, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str


class OrganizationSummary(ApiModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    monthly_document_quota: int
    role: str | None = None


class UserSummary(ApiModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth scheme name, not a secret
    expires_in: int
    user: UserSummary
    organization: OrganizationSummary


class SessionResponse(BaseModel):
    user: UserSummary
    organization: OrganizationSummary
    organizations: list[OrganizationSummary]
    role: str


# ------------------------------------------------------------------- documents


class DocumentSummary(ApiModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: str
    document_type_key: str | None
    page_count: int | None
    used_ocr: bool
    processing_ms: int | None
    created_at: dt.datetime
    error_code: str | None = None


class DocumentDetail(DocumentSummary):
    checksum_sha256: str
    char_count: int | None
    language: str | None
    classification_confidence: float | None
    error_message: str | None = None
    source: str
    processing_started_at: dt.datetime | None = None
    processing_finished_at: dt.datetime | None = None


class UploadResponse(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    duplicate: bool = Field(
        default=False,
        description="True when identical content was already uploaded; no new job was created.",
    )
    message: str | None = None


class JobStatusResponse(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID | None
    status: str
    job_status: str | None
    attempt: int = 0
    max_attempts: int = 0
    error_code: str | None = None
    error_message: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    duration_ms: int | None = None


class ProcessingStepResponse(ApiModel):
    stage: str
    status: str
    sequence: int
    duration_ms: int | None
    started_at: dt.datetime
    finished_at: dt.datetime | None
    error_code: str | None
    error_message: str | None
    detail: dict[str, Any]


# ------------------------------------------------------------------ extraction


class ExtractionFieldResponse(BaseModel):
    field_path: str
    label: str | None
    value: Any
    confidence: float | None
    confidence_band: str | None
    source: str
    is_required: bool
    needs_review: bool
    was_corrected: bool
    evidence_text: str | None = None
    reasons: list[str] = Field(default_factory=list)


class ValidationIssueResponse(ApiModel):
    rule_id: str
    field_path: str | None
    severity: str
    code: str
    message: str


class ExtractionResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    revision: int
    document_type_key: str
    schema_version: int
    data: dict[str, Any]
    fields: list[ExtractionFieldResponse]
    issues: list[ValidationIssueResponse]
    overall_confidence: float | None
    needs_review: bool
    review_reasons: list[str]
    created_at: dt.datetime

    # Reproducibility block — every value needed to explain or replay this result.
    provider: str | None
    model: str | None
    model_version: str | None
    prompt_key: str | None
    prompt_version: str | None
    extractor: str

    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int | None


class FieldEditRequest(BaseModel):
    field_path: str = Field(
        max_length=200, examples=["total", "supplier.name", "line_items.0.unit_price"]
    )
    value: Any


class UpdateExtractionRequest(BaseModel):
    edits: list[FieldEditRequest] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class ApproveRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    force: bool = Field(
        default=False,
        description="Approve despite unresolved validation errors. Recorded in the audit log.",
    )


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)


class ReviewOutcomeResponse(BaseModel):
    extraction_id: uuid.UUID
    status: str
    corrections_applied: int
    remaining_errors: int
    needs_review: bool


# ------------------------------------------------------------------- analytics


class DashboardResponse(BaseModel):
    total_documents: int
    by_status: dict[str, int]
    needs_review: int
    processed_this_period: int
    quota: int
    quota_used: int
    success_rate: float | None
    avg_processing_ms: float | None
    review_rate: float | None
    cost_usd_this_period: float
    cost_per_document: float | None
    pricing_as_of: str
    daily: list[dict[str, Any]]


class UsageResponse(BaseModel):
    billing_period: str
    documents: int
    events: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    quota: int
    plan: str


class CorrectionStat(BaseModel):
    document_type_key: str
    field_path: str
    corrections: int


# --------------------------------------------------------------- document types


class DocumentTypeResponse(BaseModel):
    key: str
    name: str
    description: str
    version: int
    is_builtin: bool
    field_count: int
    required_fields: list[str]
    critical_fields: list[str]
    review_threshold: float
    rules: list[str]


class CustomFieldDefinition(BaseModel):
    name: str = Field(max_length=64, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    label: str | None = Field(default=None, max_length=120)
    type: str = Field(default="string")
    required: bool = False
    critical: bool = False
    hint: str | None = Field(default=None, max_length=300)


class CreateDocumentTypeRequest(BaseModel):
    key: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=500)
    fields: list[CustomFieldDefinition] = Field(min_length=1, max_length=60)
    keywords: dict[str, float] = Field(default_factory=dict)
    review_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    extraction_guidance: str = Field(default="", max_length=4000)


# ------------------------------------------------------------------- api keys


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyResponse(ApiModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    expires_at: dt.datetime | None
    revoked_at: dt.datetime | None


class CreatedApiKeyResponse(ApiKeyResponse):
    api_key: str = Field(description="The full key. Shown once and never retrievable again.")


# ------------------------------------------------------------------- webhooks


class CreateWebhookRequest(BaseModel):
    url: str = Field(max_length=1000)
    description: str | None = Field(default=None, max_length=300)
    events: list[str] = Field(default_factory=list)


class WebhookResponse(ApiModel):
    id: uuid.UUID
    url: str
    description: str | None
    events: list[str]
    is_active: bool
    last_success_at: dt.datetime | None
    last_failure_at: dt.datetime | None
    consecutive_failures: int


class WebhookSecretResponse(WebhookResponse):
    secret: str = Field(description="HMAC signing secret. Shown once.")


# ------------------------------------------------------------------------ misc


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, bool]
    detail: dict[str, str] = Field(default_factory=dict)
