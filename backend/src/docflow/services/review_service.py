"""Human review: edit, approve, reject.

Review is not a UI feature bolted onto an AI pipeline — it is the mechanism that
makes the product sellable. A business will not let unverified model output into
its accounting system, and no honest accuracy number is high enough to change that
for the fields where being wrong is expensive.

## What happens on an edit

1. The value is written into `extractions.data`.
2. A `field_corrections` row records old value, new value, and the model, prompt
   and schema version that produced the wrong one.
3. The field is re-stamped `source=human`, `confidence=1.0`. A human-entered value
   is not a prediction and must never be shown as uncertain.
4. **Validation re-runs over the corrected data.** This matters: fixing a subtotal
   should clear the "totals do not add up" error, and fixing one field should not
   silently leave a stale error on the record.

Step 2 is the compounding asset. Corrections are ground truth, generated as a side
effect of work the customer was doing anyway, and they say exactly which fields the
system gets wrong on *this customer's* documents — which is not the same as which
fields it gets wrong on the evaluation set.

## Approval is gated on validation

`approve` refuses while blocking errors remain, unless the reviewer passes
`force=True` (recorded in the audit log). The default protects against approving
data that is definitely wrong; the override exists because reality occasionally
disagrees with the rules, and a system that cannot be overridden gets worked
around.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.models import Extraction, ExtractionField
from docflow.db.repositories import (
    AuditRepository,
    DocumentRepository,
    ExtractionRepository,
    ReviewRepository,
)
from docflow.domain.enums import (
    ConfidenceBand,
    DocumentStatus,
    ExtractionStatus,
    FieldSource,
    ReviewAction,
    ValidationSeverity,
)
from docflow.domain.errors import (
    AuthorizationError,
    ConflictError,
    ResourceNotFoundError,
    ValidationRequestError,
)
from docflow.schemas.registry import SchemaRegistry, get_registry
from docflow.security.tokens import AuthPrincipal
from docflow.domain.enums import OrgRole
from docflow.validation.engine import RuleContext, ValidationEngine, validate_syntax
from docflow.validation.paths import set_path, to_template

logger = structlog.get_logger(__name__)

MAX_EDITS_PER_REQUEST = 200


@dataclass(frozen=True, slots=True)
class FieldEdit:
    field_path: str
    value: Any


@dataclass(slots=True)
class ReviewOutcome:
    extraction: Extraction
    corrections_applied: int
    remaining_errors: int
    status: ExtractionStatus


class ReviewService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        principal: AuthPrincipal,
        registry: SchemaRegistry | None = None,
        engine: ValidationEngine | None = None,
    ) -> None:
        self._session = session
        self._principal = principal
        self._org_id = principal.organization_id
        self._registry = registry or get_registry()
        self._engine = engine or ValidationEngine()
        self.extractions = ExtractionRepository(session, self._org_id)
        self.documents = DocumentRepository(session, self._org_id)
        self.reviews = ReviewRepository(session, self._org_id)
        self._audit = AuditRepository(session)

    # -------------------------------------------------------------------- edit

    async def apply_edits(
        self, document_id: uuid.UUID, edits: list[FieldEdit], *, note: str | None = None
    ) -> ReviewOutcome:
        self._require_role(OrgRole.MEMBER)
        if not edits:
            raise ValidationRequestError("No field edits were supplied")
        if len(edits) > MAX_EDITS_PER_REQUEST:
            raise ValidationRequestError(
                f"At most {MAX_EDITS_PER_REQUEST} fields can be edited in one request"
            )

        extraction = await self._load_editable(document_id)
        spec = self._registry.resolve_or_fallback(
            extraction.document_type_key, str(self._org_id)
        )

        data = dict(extraction.data or {})
        by_path = {f.field_path: f for f in extraction.fields}

        review = await self.reviews.create(
            document_id=document_id,
            extraction_id=extraction.id,
            reviewer_id=self._principal.user_id,
            action=ReviewAction.EDIT.value,
            note=note,
        )

        applied = 0
        for edit in edits:
            spec_field = spec.field_by_path(to_template(edit.field_path))
            if spec_field is None:
                raise ValidationRequestError(
                    f"{edit.field_path!r} is not a field of this document type",
                    detail={"field_path": edit.field_path},
                )

            current = by_path.get(edit.field_path)
            old_value = (current.value or {}).get("value") if current else None
            if _same(old_value, edit.value):
                continue

            try:
                set_path(data, edit.field_path, edit.value)
            except KeyError as exc:
                raise ValidationRequestError(
                    f"{edit.field_path!r} could not be updated", detail={"reason": str(exc)}
                ) from exc

            self.reviews.add_correction(
                review_id=review.id,
                extraction_id=extraction.id,
                document_id=document_id,
                field_path=edit.field_path,
                old_value={"value": old_value},
                new_value={"value": edit.value},
                old_confidence=current.confidence if current else None,
                old_confidence_band=current.confidence_band if current else None,
                document_type_key=extraction.document_type_key,
                model=extraction.model,
                prompt_version=extraction.prompt_version,
                schema_version=extraction.schema_version,
            )

            if current is not None:
                _mark_human(current, edit.value)
            else:
                self.extractions.add_field(
                    extraction_id=extraction.id,
                    field_path=edit.field_path,
                    label=spec_field.label,
                    value={"value": edit.value},
                    confidence=1.0,
                    confidence_band=ConfidenceBand.HIGH.value,
                    source=FieldSource.HUMAN.value,
                    is_required=to_template(edit.field_path) in spec.required_paths,
                    needs_review=False,
                    was_corrected=True,
                )
            applied += 1

        review.fields_corrected = applied
        extraction.data = data

        remaining = await self._revalidate(extraction, spec)
        extraction.needs_review = remaining > 0
        if extraction.status == ExtractionStatus.DRAFT.value and remaining:
            extraction.status = ExtractionStatus.NEEDS_REVIEW.value

        self._audit.record(
            organization_id=self._org_id,
            actor_type=self._principal.actor_type.value,
            actor_id=self._principal.actor_id,
            actor_label=self._principal.label,
            action="extraction.edited",
            resource_type="extraction",
            resource_id=extraction.id,
            meta={"fields_corrected": applied, "remaining_errors": remaining},
        )
        logger.info(
            "review.edits_applied",
            document_id=str(document_id),
            organization_id=str(self._org_id),
            fields_corrected=applied,
            remaining_errors=remaining,
        )
        return ReviewOutcome(
            extraction=extraction,
            corrections_applied=applied,
            remaining_errors=remaining,
            status=ExtractionStatus(extraction.status),
        )

    # ----------------------------------------------------------------- approve

    async def approve(
        self,
        document_id: uuid.UUID,
        *,
        note: str | None = None,
        duration_seconds: int | None = None,
        force: bool = False,
    ) -> ReviewOutcome:
        self._require_role(OrgRole.MEMBER)
        extraction = await self._load_editable(document_id)

        blocking = [
            i for i in extraction.issues
            if i.severity == ValidationSeverity.ERROR.value and not i.resolved
        ]
        if blocking and not force:
            raise ConflictError(
                "This extraction still has validation errors. Fix them, or approve with "
                "`force` to override.",
                detail={
                    "errors": [
                        {"field": i.field_path, "message": i.message} for i in blocking[:10]
                    ]
                },
            )

        await self.reviews.create(
            document_id=document_id,
            extraction_id=extraction.id,
            reviewer_id=self._principal.user_id,
            action=ReviewAction.APPROVE.value,
            note=note,
            duration_seconds=duration_seconds,
        )

        extraction.status = ExtractionStatus.APPROVED.value
        extraction.needs_review = False
        await self.documents.set_status(document_id, DocumentStatus.COMPLETED)

        self._audit.record(
            organization_id=self._org_id,
            actor_type=self._principal.actor_type.value,
            actor_id=self._principal.actor_id,
            actor_label=self._principal.label,
            action="extraction.approved",
            resource_type="extraction",
            resource_id=extraction.id,
            meta={"forced": force, "override_errors": len(blocking) if force else 0},
        )
        return ReviewOutcome(
            extraction=extraction,
            corrections_applied=0,
            remaining_errors=0 if not force else len(blocking),
            status=ExtractionStatus.APPROVED,
        )

    async def reject(
        self,
        document_id: uuid.UUID,
        *,
        reason: str,
        duration_seconds: int | None = None,
    ) -> ReviewOutcome:
        self._require_role(OrgRole.MEMBER)
        if not reason.strip():
            raise ValidationRequestError("A reason is required when rejecting an extraction")

        extraction = await self._load_editable(document_id)
        await self.reviews.create(
            document_id=document_id,
            extraction_id=extraction.id,
            reviewer_id=self._principal.user_id,
            action=ReviewAction.REJECT.value,
            note=reason,
            duration_seconds=duration_seconds,
        )

        extraction.status = ExtractionStatus.REJECTED.value
        extraction.needs_review = False
        await self.documents.set_status(document_id, DocumentStatus.REJECTED)

        self._audit.record(
            organization_id=self._org_id,
            actor_type=self._principal.actor_type.value,
            actor_id=self._principal.actor_id,
            actor_label=self._principal.label,
            action="extraction.rejected",
            resource_type="extraction",
            resource_id=extraction.id,
            meta={"reason": reason[:500]},
        )
        return ReviewOutcome(
            extraction=extraction,
            corrections_applied=0,
            remaining_errors=0,
            status=ExtractionStatus.REJECTED,
        )

    # --------------------------------------------------------------- internals

    async def _load_editable(self, document_id: uuid.UUID) -> Extraction:
        extraction = await self.extractions.current_for_document(document_id)
        if extraction is None:
            # Covers both "no such document" and "document belongs to another
            # organization" — the repository's org predicate made them identical.
            raise ResourceNotFoundError("No extraction is available for this document")
        if extraction.status == ExtractionStatus.SUPERSEDED.value:
            raise ConflictError("This extraction has been superseded by a newer one")
        return extraction

    async def _revalidate(self, extraction: Extraction, spec: Any) -> int:
        """Re-run all three validation layers over the edited data.

        The same code path the pipeline used, so a human edit can never produce a
        record that validation would have rejected on the way in.
        """
        normalized, syntax_issues = validate_syntax(spec, extraction.data)
        issues = list(syntax_issues)
        if normalized is not None:
            extraction.data = normalized
            result = self._engine.validate(RuleContext(data=normalized, spec=spec))
            issues.extend(result.issues)

        await self.extractions.replace_issues(extraction.id)
        for issue in issues:
            self.extractions.add_issue(
                extraction_id=extraction.id,
                rule_id=issue.rule_id,
                field_path=issue.field_path,
                severity=issue.severity.value,
                code=issue.code,
                message=issue.message,
                context=issue.context,
            )
        # Refresh the in-memory relationship so the caller sees current issues
        # rather than the pre-edit set.
        await self._session.flush()
        await self._session.refresh(extraction, ["issues"])

        return sum(1 for i in issues if i.severity is ValidationSeverity.ERROR)

    def _require_role(self, role: OrgRole) -> None:
        if not self._principal.can(role):
            raise AuthorizationError(
                f"This action requires the {role.value} role or higher"
            )


def _mark_human(field: ExtractionField, value: Any) -> None:
    field.value = {"value": value}
    field.source = FieldSource.HUMAN.value
    # A value a person typed is not a prediction. Showing it at 62% confidence
    # because the model was unsure would be actively misleading.
    field.confidence = 1.0
    field.confidence_band = ConfidenceBand.HIGH.value
    field.needs_review = False
    field.was_corrected = True
    field.confidence_signals = {"reasons": ["Corrected by a reviewer"]}


def _same(left: Any, right: Any) -> bool:
    """Treat `None`, `""` and absent as equivalent for change detection.

    Without this, clearing an already-empty field records a spurious correction and
    pollutes the correction-rate metric — which is meant to measure model error.
    """
    if left in (None, "") and right in (None, ""):
        return True
    return str(left) == str(right)
