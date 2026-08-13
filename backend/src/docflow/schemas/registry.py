"""Document type registry.

Two sources of document types:

1. **Built-in specs** declared in `docflow.schemas.types`. Python-native, fully
   typed, unit tested, shipped with the product.
2. **Organization-defined types** stored in the `document_types` table and compiled
   into Pydantic models at load time by `build_dynamic_spec`.

Both produce a `DocumentTypeSpec`, and the pipeline cannot tell them apart. That is
the whole point: a customer adding a "delivery note" type gets the same validation,
confidence and review machinery as the built-in invoice type, with no code change.

Custom types are defined with a **restricted field DSL** rather than raw JSON Schema.
Accepting arbitrary JSON Schema from a tenant would mean handing user-controlled
input to a schema compiler and then to a model's structured-output layer — a large
attack surface (unbounded nesting, pathological regexes, `$ref` cycles) for a
capability nobody asked for. A closed set of field kinds covers the real use cases and
can be validated exhaustively.
"""

from __future__ import annotations

import threading
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from docflow.domain.errors import UnknownDocumentTypeError, ValidationRequestError
from docflow.schemas.base import (
    ClassificationHints,
    DocumentTypeSpec,
    FieldKind,
    FieldSpec,
    derive_field_specs,
    spec_field,
)
from docflow.schemas.fields import CleanStr, CurrencyCode, FlexibleDate, Money
from docflow.schemas.types.contract import CONTRACT_SPEC
from docflow.schemas.types.invoice import INVOICE_SPEC
from docflow.schemas.types.purchase_order import PURCHASE_ORDER_SPEC
from docflow.schemas.types.receipt import RECEIPT_SPEC

# Fallback for documents we cannot confidently classify. Extracting *something*
# useful beats failing: the user still gets a summary and a review queue entry, and
# the misclassification is visible rather than silent.


class GenericDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Title", FieldKind.STRING)
    )
    document_kind: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Detected kind",
            FieldKind.STRING,
            groundable=False,
            hint="Your best short label for what this document is",
        ),
    )
    issuer: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Issuer", FieldKind.STRING)
    )
    recipient: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Recipient", FieldKind.STRING)
    )
    document_date: FlexibleDate | None = Field(
        default=None, json_schema_extra=spec_field("Date", FieldKind.DATE)
    )
    reference_number: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Reference", FieldKind.IDENTIFIER)
    )
    total_amount: Money | None = Field(
        default=None, json_schema_extra=spec_field("Amount", FieldKind.MONEY)
    )
    currency: CurrencyCode | None = Field(
        default=None, json_schema_extra=spec_field("Currency", FieldKind.CURRENCY)
    )
    summary: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Summary", FieldKind.TEXT, groundable=False,
            hint="Two or three sentences describing the document",
        ),
    )


GENERIC_SPEC = DocumentTypeSpec(
    key="generic",
    name="Generic document",
    description="Fallback for documents that do not match a configured type.",
    model=GenericDocument,
    version=1,
    fields=derive_field_specs(GenericDocument),
    classification=ClassificationHints(),
    rule_ids=("currency_supported", "date_sanity"),
    # Everything unclassified goes to a human. We do not know what this is, so we
    # have no basis for claiming the extraction is right.
    review_threshold=1.0,
    extraction_guidance=(
        "This document did not match a configured type. Extract only what is "
        "clearly present and leave the rest null."
    ),
)


BUILTIN_SPECS: tuple[DocumentTypeSpec, ...] = (
    INVOICE_SPEC,
    CONTRACT_SPEC,
    PURCHASE_ORDER_SPEC,
    RECEIPT_SPEC,
    GENERIC_SPEC,
)

FALLBACK_KEY = "generic"


class SchemaRegistry:
    """Thread-safe registry with an organization-scoped overlay.

    Lookup order for `resolve(key, organization_id)`:
        1. a type registered by that organization (allows shadowing built-ins)
        2. a built-in type
        3. `UnknownDocumentTypeError`

    Custom types are cached in-process after being compiled from the database.
    Invalidation is explicit (`invalidate`) and is triggered by the write path in
    `services.schema_service` — see `docs/DECISIONS.md` on why this is not TTL-based.
    """

    def __init__(self, specs: tuple[DocumentTypeSpec, ...] = BUILTIN_SPECS) -> None:
        self._builtin: dict[str, DocumentTypeSpec] = {s.key: s for s in specs}
        self._custom: dict[tuple[str, str], DocumentTypeSpec] = {}
        self._lock = threading.RLock()

    # ----------------------------------------------------------------- read paths

    def resolve(self, key: str, organization_id: str | None = None) -> DocumentTypeSpec:
        with self._lock:
            if organization_id and (spec := self._custom.get((organization_id, key))):
                return spec
            if spec := self._builtin.get(key):
                return spec
        raise UnknownDocumentTypeError(
            f"No schema registered for document type {key!r}",
            detail={"document_type": key},
        )

    def resolve_or_fallback(self, key: str | None, organization_id: str | None = None) -> DocumentTypeSpec:
        if not key:
            return self._builtin[FALLBACK_KEY]
        try:
            return self.resolve(key, organization_id)
        except UnknownDocumentTypeError:
            return self._builtin[FALLBACK_KEY]

    def list_specs(self, organization_id: str | None = None) -> list[DocumentTypeSpec]:
        with self._lock:
            by_key: dict[str, DocumentTypeSpec] = dict(self._builtin)
            if organization_id:
                for (org, key), spec in self._custom.items():
                    if org == organization_id:
                        by_key[key] = spec
            return sorted(by_key.values(), key=lambda s: s.name)

    def classifiable_specs(self, organization_id: str | None = None) -> list[DocumentTypeSpec]:
        """Types that participate in classification (everything but the fallback)."""
        return [s for s in self.list_specs(organization_id) if s.key != FALLBACK_KEY]

    # ---------------------------------------------------------------- write paths

    def register_custom(self, organization_id: str, spec: DocumentTypeSpec) -> None:
        with self._lock:
            self._custom[(organization_id, spec.key)] = spec

    def invalidate(self, organization_id: str, key: str | None = None) -> None:
        with self._lock:
            if key is not None:
                self._custom.pop((organization_id, key), None)
                return
            for cache_key in [k for k in self._custom if k[0] == organization_id]:
                self._custom.pop(cache_key, None)

    def clear_custom(self) -> None:
        with self._lock:
            self._custom.clear()


_registry = SchemaRegistry()


def get_registry() -> SchemaRegistry:
    return _registry


# ---------------------------------------------------- custom type compilation (DSL)

# Closed set of field kinds a tenant may declare. Each maps to a vetted annotation.
_DSL_TYPES: dict[str, Any] = {
    "string": CleanStr,
    "text": CleanStr,
    "money": Money,
    "number": Money,
    "date": FlexibleDate,
    "currency": CurrencyCode,
    "identifier": CleanStr,
    "boolean": bool,
}

_DSL_KIND: dict[str, FieldKind] = {
    "string": FieldKind.STRING,
    "text": FieldKind.TEXT,
    "money": FieldKind.MONEY,
    "number": FieldKind.NUMBER,
    "date": FieldKind.DATE,
    "currency": FieldKind.CURRENCY,
    "identifier": FieldKind.IDENTIFIER,
    "boolean": FieldKind.BOOLEAN,
}

MAX_CUSTOM_FIELDS = 60
MAX_NAME_LENGTH = 64


def validate_custom_definition(definition: dict[str, Any]) -> None:
    """Validate a tenant-supplied type definition before it reaches the compiler."""
    fields = definition.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValidationRequestError("A document type must define at least one field")
    if len(fields) > MAX_CUSTOM_FIELDS:
        raise ValidationRequestError(
            f"A document type may define at most {MAX_CUSTOM_FIELDS} fields"
        )

    seen: set[str] = set()
    for raw in fields:
        if not isinstance(raw, dict):
            raise ValidationRequestError("Each field must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise ValidationRequestError(
                f"Field name {name!r} must be a valid identifier not starting with '_'"
            )
        if len(name) > MAX_NAME_LENGTH:
            raise ValidationRequestError(f"Field name {name!r} is too long")
        if name in seen:
            raise ValidationRequestError(f"Duplicate field name {name!r}")
        seen.add(name)

        kind = raw.get("type", "string")
        if kind not in _DSL_TYPES:
            raise ValidationRequestError(
                f"Unsupported field type {kind!r}. Supported: {sorted(_DSL_TYPES)}"
            )


def build_dynamic_spec(
    *,
    key: str,
    name: str,
    description: str,
    version: int,
    definition: dict[str, Any],
) -> DocumentTypeSpec:
    """Compile a validated tenant definition into a real `DocumentTypeSpec`."""
    validate_custom_definition(definition)

    annotations: dict[str, Any] = {}
    field_specs: list[FieldSpec] = []

    for raw in definition["fields"]:
        fname = raw["name"]
        kind_key = raw.get("type", "string")
        annotation = _DSL_TYPES[kind_key]
        label = raw.get("label") or fname.replace("_", " ").capitalize()
        required = bool(raw.get("required", False))
        critical = bool(raw.get("critical", False))
        hint = raw.get("hint")

        annotations[fname] = (
            annotation | None,
            Field(
                default=None,
                description=hint,
                json_schema_extra=spec_field(
                    label,
                    _DSL_KIND[kind_key],
                    required=required,
                    critical=critical,
                    hint=hint,
                    groundable=kind_key != "boolean",
                ),
            ),
        )
        field_specs.append(
            FieldSpec(
                path=fname,
                label=label,
                kind=_DSL_KIND[kind_key],
                required=required,
                critical=critical,
                groundable=kind_key != "boolean",
                hint=hint,
            )
        )

    model = create_model(  # type: ignore[call-overload]
        f"Custom_{key.title().replace('_', '')}_V{version}",
        __config__=ConfigDict(extra="forbid"),
        **annotations,
    )

    rule_ids = ["required_fields", "currency_supported", "date_sanity", "positive_amounts"]
    return DocumentTypeSpec(
        key=key,
        name=name,
        description=description,
        model=model,
        version=version,
        fields=tuple(field_specs),
        classification=ClassificationHints(
            keywords={k: float(v) for k, v in (definition.get("keywords") or {}).items()}
        ),
        rule_ids=tuple(rule_ids),
        review_threshold=float(definition.get("review_threshold", 0.85)),
        extraction_guidance=str(definition.get("extraction_guidance", "")),
    )
