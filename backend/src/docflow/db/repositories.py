"""Repositories — every query, org-scoped by construction.

## The tenant-isolation argument

This is the module where a multi-tenant SaaS leaks data, so the design is
deliberately restrictive.

`OrgScopedRepository` takes `organization_id` in its **constructor** and injects it
into every query it builds. There is no method that accepts an arbitrary filter and
no method that returns a row without the org predicate applied. A caller cannot
forget to scope a query, because there is no unscoped query to call.

The specific vulnerability this closes is IDOR: `GET /documents/{id}` where `id`
belongs to another organization. The route does not compare ids and decide — it
asks the repository, whose `WHERE` clause contains both the id *and* the caller's
organization. A miss returns `None`, and the service raises 404.

**404, not 403.** Returning "forbidden" for a document that exists elsewhere
confirms its existence, turning the authorization check into an enumeration oracle.
Every cross-tenant access looks exactly like a document that was never there.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from docflow.db.models import (
    ApiKey,
    AuditLog,
    Document,
    DocumentType,
    Extraction,
    ExtractionField,
    FieldCorrection,
    Membership,
    Organization,
    ProcessingJob,
    ProcessingStep,
    Review,
    UsageRecord,
    User,
    ValidationIssue,
    WebhookDelivery,
    WebhookEndpoint,
)
from docflow.domain.enums import DocumentStatus, ExtractionStatus, JobStatus

ModelT = TypeVar("ModelT")


class OrgScopedRepository(Generic[ModelT]):
    """Base class that makes the organization predicate non-optional."""

    model: type[Any]

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        self.session = session
        self.organization_id = organization_id

    def _scoped(self) -> Select[Any]:
        return select(self.model).where(self.model.organization_id == self.organization_id)


# =============================================================================
# Identity & tenancy (not org-scoped — these establish the scope)
# =============================================================================


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )
        return result.scalar_one_or_none()

    async def create(self, *, email: str, hashed_password: str, full_name: str | None) -> User:
        user = User(
            email=email.strip().lower(), hashed_password=hashed_password, full_name=full_name
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def touch_login(self, user: User) -> None:
        user.last_login_at = dt.datetime.now(dt.UTC)

    async def memberships(self, user_id: uuid.UUID) -> Sequence[Membership]:
        result = await self.session.execute(
            select(Membership)
            .where(Membership.user_id == user_id)
            .options(selectinload(Membership.organization))
            .order_by(Membership.created_at)
        )
        return result.scalars().all()

    async def membership_in(
        self, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Membership | None:
        result = await self.session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: uuid.UUID) -> Organization | None:
        return await self.session.get(Organization, organization_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self.session.execute(
            select(Organization).where(Organization.slug == slug)
        )
        return result.scalar_one_or_none()

    async def create(
        self, *, name: str, slug: str, plan: str = "free", quota: int = 50
    ) -> Organization:
        org = Organization(name=name, slug=slug, plan=plan, monthly_document_quota=quota)
        self.session.add(org)
        await self.session.flush()
        return org

    async def add_member(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: str
    ) -> Membership:
        membership = Membership(
            organization_id=organization_id, user_id=user_id, role=role
        )
        self.session.add(membership)
        await self.session.flush()
        return membership


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_by_hash(self, hashed: str) -> ApiKey | None:
        """Look a key up by digest, rejecting revoked and expired keys in SQL.

        Filtering in the query rather than in Python means a revoked key cannot be
        authenticated by a code path that forgets to check — there is only one path.
        """
        now = dt.datetime.now(dt.UTC)
        result = await self.session.execute(
            select(ApiKey).where(
                ApiKey.hashed_key == hashed,
                ApiKey.revoked_at.is_(None),
                (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_org(self, organization_id: uuid.UUID) -> Sequence[ApiKey]:
        result = await self.session.execute(
            select(ApiKey)
            .where(ApiKey.organization_id == organization_id)
            .order_by(ApiKey.created_at.desc())
        )
        return result.scalars().all()

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        name: str,
        prefix: str,
        hashed_key: str,
        created_by_id: uuid.UUID | None,
        scopes: list[str] | None = None,
        expires_at: dt.datetime | None = None,
    ) -> ApiKey:
        key = ApiKey(
            organization_id=organization_id,
            name=name,
            prefix=prefix,
            hashed_key=hashed_key,
            created_by_id=created_by_id,
            scopes=scopes or [],
            expires_at=expires_at,
        )
        self.session.add(key)
        await self.session.flush()
        return key

    async def revoke(self, key_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(ApiKey)
            .where(
                ApiKey.id == key_id,
                ApiKey.organization_id == organization_id,
                ApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=dt.datetime.now(dt.UTC))
        )
        return bool(result.rowcount)

    async def touch(self, key: ApiKey) -> None:
        key.last_used_at = dt.datetime.now(dt.UTC)


# =============================================================================
# Documents
# =============================================================================


class DocumentRepository(OrgScopedRepository[Document]):
    model = Document

    async def get(self, document_id: uuid.UUID) -> Document | None:
        result = await self.session.execute(self._scoped().where(Document.id == document_id))
        return result.scalar_one_or_none()

    async def get_with_extraction(self, document_id: uuid.UUID) -> Document | None:
        result = await self.session.execute(
            self._scoped()
            .where(Document.id == document_id)
            .options(
                selectinload(Document.extractions).selectinload(Extraction.fields),
                selectinload(Document.extractions).selectinload(Extraction.issues),
            )
        )
        return result.scalar_one_or_none()

    async def find_by_checksum(self, checksum: str) -> Document | None:
        result = await self.session.execute(
            self._scoped().where(Document.checksum_sha256 == checksum)
        )
        return result.scalar_one_or_none()

    async def create(self, **values: Any) -> Document:
        document = Document(organization_id=self.organization_id, **values)
        self.session.add(document)
        await self.session.flush()
        return document

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: DocumentStatus | None = None,
        document_type: str | None = None,
        search: str | None = None,
        needs_review: bool | None = None,
    ) -> tuple[Sequence[Document], int]:
        query = self._scoped()
        if status is not None:
            query = query.where(Document.status == status.value)
        if document_type:
            query = query.where(Document.document_type_key == document_type)
        if needs_review is True:
            query = query.where(Document.status == DocumentStatus.NEEDS_REVIEW.value)
        if search:
            # `ilike` with a leading wildcard cannot use a B-tree index. Acceptable
            # at the volumes a single SMB tenant produces; a trigram index or a
            # tsvector column is the fix if this becomes hot. Noted in DATABASE.md.
            query = query.where(Document.filename.ilike(f"%{search}%"))

        total = await self.session.scalar(
            select(func.count()).select_from(query.subquery())
        )
        result = await self.session.execute(
            query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
        )
        return result.scalars().all(), int(total or 0)

    async def set_status(
        self,
        document_id: uuid.UUID,
        status: DocumentStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status.value}
        if error_code is not None:
            values["error_code"] = error_code
        if error_message is not None:
            values["error_message"] = error_message
        await self.session.execute(
            update(Document)
            .where(Document.id == document_id, Document.organization_id == self.organization_id)
            .values(**values)
        )

    async def status_counts(self) -> dict[str, int]:
        result = await self.session.execute(
            select(Document.status, func.count())
            .where(Document.organization_id == self.organization_id)
            .group_by(Document.status)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def count_in_period(self, *, since: dt.datetime) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.organization_id == self.organization_id,
                Document.created_at >= since,
            )
        )
        return int(total or 0)

    async def delete(self, document_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(Document).where(
                Document.id == document_id,
                Document.organization_id == self.organization_id,
            )
        )
        return bool(result.rowcount)


class JobRepository(OrgScopedRepository[ProcessingJob]):
    model = ProcessingJob

    async def get(self, job_id: uuid.UUID) -> ProcessingJob | None:
        result = await self.session.execute(self._scoped().where(ProcessingJob.id == job_id))
        return result.scalar_one_or_none()

    async def find_by_idempotency_key(self, key: str) -> ProcessingJob | None:
        result = await self.session.execute(
            self._scoped().where(ProcessingJob.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def latest_for_document(self, document_id: uuid.UUID) -> ProcessingJob | None:
        result = await self.session.execute(
            self._scoped()
            .where(ProcessingJob.document_id == document_id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create(
        self, *, document_id: uuid.UUID, idempotency_key: str, max_attempts: int
    ) -> ProcessingJob:
        job = ProcessingJob(
            organization_id=self.organization_id,
            document_id=document_id,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def mark_running(self, job: ProcessingJob, attempt: int) -> None:
        job.status = JobStatus.RUNNING.value
        job.attempt = attempt
        job.started_at = dt.datetime.now(dt.UTC)

    async def finish(
        self,
        job: ProcessingJob,
        *,
        status: JobStatus,
        error_code: str | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        job.status = status.value
        job.finished_at = dt.datetime.now(dt.UTC)
        if job.started_at:
            job.duration_ms = int(
                (job.finished_at - job.started_at).total_seconds() * 1000
            )
        job.error_code = error_code
        job.error_category = error_category
        job.error_message = error_message

    async def steps_for_document(self, document_id: uuid.UUID) -> Sequence[ProcessingStep]:
        result = await self.session.execute(
            select(ProcessingStep)
            .join(ProcessingJob, ProcessingStep.job_id == ProcessingJob.id)
            .where(
                ProcessingStep.document_id == document_id,
                ProcessingJob.organization_id == self.organization_id,
            )
            .order_by(ProcessingStep.started_at, ProcessingStep.sequence)
        )
        return result.scalars().all()


class ExtractionRepository(OrgScopedRepository[Extraction]):
    model = Extraction

    async def get(self, extraction_id: uuid.UUID) -> Extraction | None:
        result = await self.session.execute(
            self._scoped()
            .where(Extraction.id == extraction_id)
            .options(selectinload(Extraction.fields), selectinload(Extraction.issues))
        )
        return result.scalar_one_or_none()

    async def current_for_document(self, document_id: uuid.UUID) -> Extraction | None:
        result = await self.session.execute(
            self._scoped()
            .where(Extraction.document_id == document_id, Extraction.is_current.is_(True))
            .options(selectinload(Extraction.fields), selectinload(Extraction.issues))
        )
        return result.scalar_one_or_none()

    async def supersede_current(self, document_id: uuid.UUID) -> int:
        """Mark existing extractions superseded before inserting a new revision.

        Append-only history is what makes "which model produced this?" answerable
        after a reprocess. The unique index on `(document_id, revision)` is the
        backstop if this is ever forgotten.
        """
        result = await self.session.execute(
            update(Extraction)
            .where(
                Extraction.document_id == document_id,
                Extraction.organization_id == self.organization_id,
                Extraction.is_current.is_(True),
            )
            .values(is_current=False, status=ExtractionStatus.SUPERSEDED.value)
        )
        return int(result.rowcount or 0)

    async def next_revision(self, document_id: uuid.UUID) -> int:
        highest = await self.session.scalar(
            select(func.max(Extraction.revision)).where(
                Extraction.document_id == document_id,
                Extraction.organization_id == self.organization_id,
            )
        )
        return int(highest or 0) + 1

    async def create(self, **values: Any) -> Extraction:
        extraction = Extraction(organization_id=self.organization_id, **values)
        self.session.add(extraction)
        await self.session.flush()
        return extraction

    def add_field(self, **values: Any) -> ExtractionField:
        row = ExtractionField(organization_id=self.organization_id, **values)
        self.session.add(row)
        return row

    def add_issue(self, **values: Any) -> ValidationIssue:
        row = ValidationIssue(organization_id=self.organization_id, **values)
        self.session.add(row)
        return row

    async def replace_issues(self, extraction_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(ValidationIssue).where(
                ValidationIssue.extraction_id == extraction_id,
                ValidationIssue.organization_id == self.organization_id,
            )
        )

    async def review_queue(self, *, limit: int = 50, offset: int = 0) -> Sequence[Extraction]:
        result = await self.session.execute(
            self._scoped()
            .where(
                Extraction.is_current.is_(True),
                Extraction.needs_review.is_(True),
                Extraction.status == ExtractionStatus.NEEDS_REVIEW.value,
            )
            .order_by(Extraction.overall_confidence.asc().nullsfirst())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()


class ReviewRepository(OrgScopedRepository[Review]):
    model = Review

    async def create(self, **values: Any) -> Review:
        review = Review(organization_id=self.organization_id, **values)
        self.session.add(review)
        await self.session.flush()
        return review

    def add_correction(self, **values: Any) -> FieldCorrection:
        correction = FieldCorrection(
            organization_id=self.organization_id,
            created_at=dt.datetime.now(dt.UTC),
            **values,
        )
        self.session.add(correction)
        return correction

    async def list_for_document(self, document_id: uuid.UUID) -> Sequence[Review]:
        result = await self.session.execute(
            self._scoped()
            .where(Review.document_id == document_id)
            .options(selectinload(Review.corrections))
            .order_by(Review.created_at.desc())
        )
        return result.scalars().all()

    async def correction_stats(
        self, *, since: dt.datetime | None = None
    ) -> list[tuple[str, str, int]]:
        """`(document_type, field_path, correction_count)`, most corrected first.

        This is the product's improvement backlog, derived from what humans
        actually fix rather than from what we imagine is hard.
        """
        query = (
            select(
                FieldCorrection.document_type_key,
                FieldCorrection.field_path,
                func.count().label("corrections"),
            )
            .where(FieldCorrection.organization_id == self.organization_id)
            .group_by(FieldCorrection.document_type_key, FieldCorrection.field_path)
            .order_by(func.count().desc())
        )
        if since is not None:
            query = query.where(FieldCorrection.created_at >= since)
        result = await self.session.execute(query.limit(50))
        return [(row[0], row[1], int(row[2])) for row in result.all()]


class UsageRepository(OrgScopedRepository[UsageRecord]):
    model = UsageRecord

    def record(self, **values: Any) -> UsageRecord:
        row = UsageRecord(
            organization_id=self.organization_id,
            created_at=dt.datetime.now(dt.UTC),
            **values,
        )
        self.session.add(row)
        return row

    async def period_totals(self, billing_period: str) -> dict[str, Any]:
        result = await self.session.execute(
            select(
                func.count().label("events"),
                func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0),
            ).where(
                UsageRecord.organization_id == self.organization_id,
                UsageRecord.billing_period == billing_period,
            )
        )
        row = result.one()
        return {
            "events": int(row[0]),
            "input_tokens": int(row[1]),
            "output_tokens": int(row[2]),
            "cost_usd": float(row[3]),
        }

    async def daily_series(self, *, days: int = 30) -> list[dict[str, Any]]:
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
        result = await self.session.execute(
            select(
                func.date_trunc("day", UsageRecord.created_at).label("day"),
                func.count(),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0),
            )
            .where(
                UsageRecord.organization_id == self.organization_id,
                UsageRecord.created_at >= since,
            )
            .group_by("day")
            .order_by("day")
        )
        return [
            {"day": row[0].date().isoformat(), "events": int(row[1]), "cost_usd": float(row[2])}
            for row in result.all()
        ]


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def record(self, **values: Any) -> AuditLog:
        entry = AuditLog(created_at=dt.datetime.now(dt.UTC), **values)
        self.session.add(entry)
        return entry

    async def list_for_org(
        self, organization_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[AuditLog]:
        result = await self.session.execute(
            select(AuditLog)
            .where(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


class DocumentTypeRepository(OrgScopedRepository[DocumentType]):
    model = DocumentType

    async def list_for_org(self) -> Sequence[DocumentType]:
        result = await self.session.execute(
            select(DocumentType)
            .where(
                (DocumentType.organization_id == self.organization_id)
                | (DocumentType.organization_id.is_(None)),
                DocumentType.is_active.is_(True),
            )
            .order_by(DocumentType.name)
        )
        return result.scalars().all()

    async def get_by_key(self, key: str) -> DocumentType | None:
        result = await self.session.execute(
            select(DocumentType)
            .where(
                DocumentType.key == key,
                DocumentType.organization_id == self.organization_id,
                DocumentType.is_active.is_(True),
            )
            .order_by(DocumentType.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create(self, **values: Any) -> DocumentType:
        row = DocumentType(organization_id=self.organization_id, **values)
        self.session.add(row)
        await self.session.flush()
        return row


class WebhookRepository(OrgScopedRepository[WebhookEndpoint]):
    model = WebhookEndpoint

    async def list_active(self, event: str) -> Sequence[WebhookEndpoint]:
        result = await self.session.execute(
            self._scoped().where(WebhookEndpoint.is_active.is_(True))
        )
        return [e for e in result.scalars().all() if not e.events or event in e.events]

    async def list_all(self) -> Sequence[WebhookEndpoint]:
        result = await self.session.execute(
            self._scoped().order_by(WebhookEndpoint.created_at.desc())
        )
        return result.scalars().all()

    async def create(self, **values: Any) -> WebhookEndpoint:
        row = WebhookEndpoint(organization_id=self.organization_id, **values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete(self, endpoint_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(WebhookEndpoint).where(
                WebhookEndpoint.id == endpoint_id,
                WebhookEndpoint.organization_id == self.organization_id,
            )
        )
        return bool(result.rowcount)

    def queue_delivery(self, **values: Any) -> WebhookDelivery:
        delivery = WebhookDelivery(
            organization_id=self.organization_id,
            created_at=dt.datetime.now(dt.UTC),
            **values,
        )
        self.session.add(delivery)
        return delivery
