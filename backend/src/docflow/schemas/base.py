"""Document-type specification: the contract between a document type and the pipeline.

A `DocumentTypeSpec` bundles everything the generic pipeline needs to handle a new
kind of document:

    * a Pydantic model      — structure, types, JSON Schema for the model's tool call
    * field metadata        — which fields are required, critical, or groundable
    * classification hints  — cheap keyword signals so we don't pay an LLM to tell us
                              an invoice is an invoice
    * business rules        — the semantic checks that make output trustworthy

Adding a document type means adding one module here. No pipeline code changes, no
`if document_type == "invoice"` branches anywhere. That property is the difference
between a document platform and an invoice parser with extra steps.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, get_args, get_origin

from pydantic import BaseModel


class FieldKind(StrEnum):
    """Semantic kind, used for confidence signals, UI rendering and normalisation.

    Distinct from the Python type: `total` and `vat_rate` are both Decimal, but only
    one of them is money and only one should be rendered with a currency symbol.
    """

    STRING = "string"
    TEXT = "text"
    MONEY = "money"
    NUMBER = "number"
    DATE = "date"
    CURRENCY = "currency"
    IDENTIFIER = "identifier"
    BANK_ACCOUNT = "bank_account"
    ENUM = "enum"
    BOOLEAN = "boolean"
    OBJECT = "object"
    LIST = "list"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    path: str
    label: str
    kind: FieldKind
    required: bool = False
    # Should evidence grounding be applied? False for computed or inferred values
    # (a boolean `auto_renews` will never appear verbatim in the text, so scoring it
    # against the source would penalise a correct answer).
    groundable: bool = True
    # A wrong value here is expensive to the customer — bank account, total, due
    # date. Critical fields get a stricter review threshold and always appear at the
    # top of the review UI.
    critical: bool = False
    hint: str | None = None
    enum_values: tuple[str, ...] = ()

    @property
    def is_nested(self) -> bool:
        return "." in self.path or "[]" in self.path


@dataclass(frozen=True, slots=True)
class ClassificationHints:
    """Cheap deterministic classification signal.

    Keyword weights are summed over the document text (case-insensitive, word
    boundaries) and normalised. Running this first means a clearly-labelled invoice
    never costs an LLM call to classify — roughly a third of total token spend at
    typical document mixes, removed for free.
    """

    keywords: dict[str, float] = field(default_factory=dict)
    # Regexes worth more than a bare keyword because they are structurally specific,
    # e.g. a VAT-rate table or an "IBAN:" label.
    patterns: dict[str, float] = field(default_factory=dict)
    # Terms that argue *against* this type. "purchase order" appearing in a document
    # is decent evidence it is not an invoice.
    negative_keywords: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentTypeSpec:
    key: str
    name: str
    description: str
    model: type[BaseModel]
    version: int = 1
    fields: tuple[FieldSpec, ...] = ()
    classification: ClassificationHints = field(default_factory=ClassificationHints)
    # Rule ids from `docflow.validation.rules`, applied in order.
    rule_ids: tuple[str, ...] = ()
    # European day-first date convention. Per type because a US-sourced PO and a
    # Czech invoice can legitimately coexist in one organization.
    day_first_dates: bool = True
    # Overall-confidence threshold below which the document is routed to review.
    review_threshold: float = 0.85
    # Stricter threshold applied to fields marked `critical`.
    critical_field_threshold: float = 0.92
    # Extra guidance appended to the extraction prompt for this type. Kept in the
    # spec rather than the prompt template so that prompts stay type-agnostic.
    extraction_guidance: str = ""

    @property
    def required_paths(self) -> set[str]:
        return {f.path for f in self.fields if f.required}

    @property
    def critical_paths(self) -> set[str]:
        return {f.path for f in self.fields if f.critical}

    def field_by_path(self, path: str) -> FieldSpec | None:
        return _index(self).get(path)

    def json_schema(self) -> dict[str, Any]:
        """JSON Schema handed to the provider as a structured-output definition."""
        return self.model.model_json_schema()

    def schema_id(self) -> str:
        return f"{self.key}_v{self.version}"


_INDEX_CACHE: dict[str, dict[str, FieldSpec]] = {}


def _index(spec: DocumentTypeSpec) -> dict[str, FieldSpec]:
    cached = _INDEX_CACHE.get(spec.schema_id())
    if cached is None:
        cached = {f.path: f for f in spec.fields}
        _INDEX_CACHE[spec.schema_id()] = cached
    return cached


# --------------------------------------------------------- declarative field helper


def spec_field(
    label: str,
    kind: FieldKind,
    *,
    required: bool = False,
    groundable: bool = True,
    critical: bool = False,
    hint: str | None = None,
) -> dict[str, Any]:
    """Attach Docflow metadata to a Pydantic field via `json_schema_extra`.

    Keeping the metadata on the Pydantic field (rather than in a parallel list) means
    the structure and its metadata cannot drift apart — there is exactly one place
    where a field is declared.
    """
    return {
        "docflow": {
            "label": label,
            "kind": kind.value,
            "required": required,
            "groundable": groundable,
            "critical": critical,
            "hint": hint,
        }
    }


def derive_field_specs(model: type[BaseModel], prefix: str = "") -> tuple[FieldSpec, ...]:
    """Walk a Pydantic model and build the `FieldSpec` tuple.

    Nested models are flattened with dotted paths; list-of-model fields use a `[]`
    segment (`line_items[].description`) so that a spec can describe a repeated
    group without knowing how many rows a given document has.
    """
    specs: list[FieldSpec] = []
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        extra = (info.json_schema_extra or {}) if isinstance(info.json_schema_extra, dict) else {}
        meta: dict[str, Any] = dict(extra.get("docflow") or {})  # type: ignore[arg-type]

        annotation = info.annotation
        inner_model = _model_of(annotation)
        list_model = _list_model_of(annotation)

        if list_model is not None:
            specs.append(
                FieldSpec(
                    path=path,
                    label=meta.get("label") or _humanise(name),
                    kind=FieldKind.LIST,
                    required=bool(meta.get("required", info.is_required())),
                    groundable=False,
                    critical=bool(meta.get("critical", False)),
                    hint=meta.get("hint"),
                )
            )
            specs.extend(derive_field_specs(list_model, prefix=f"{path}[]."))
            continue

        if inner_model is not None:
            specs.append(
                FieldSpec(
                    path=path,
                    label=meta.get("label") or _humanise(name),
                    kind=FieldKind.OBJECT,
                    required=bool(meta.get("required", info.is_required())),
                    groundable=False,
                    hint=meta.get("hint"),
                )
            )
            specs.extend(derive_field_specs(inner_model, prefix=f"{path}."))
            continue

        specs.append(
            FieldSpec(
                path=path,
                label=meta.get("label") or _humanise(name),
                kind=FieldKind(meta.get("kind", FieldKind.STRING.value)),
                required=bool(meta.get("required", info.is_required())),
                groundable=bool(meta.get("groundable", True)),
                critical=bool(meta.get("critical", False)),
                hint=meta.get("hint") or info.description,
                enum_values=tuple(meta.get("enum_values", ())),
            )
        )
    return tuple(specs)


def _model_of(annotation: Any) -> type[BaseModel] | None:
    for candidate in _unwrap(annotation):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _list_model_of(annotation: Any) -> type[BaseModel] | None:
    for candidate in _unwrap(annotation):
        if get_origin(candidate) in (list, Sequence):
            args = get_args(candidate)
            if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                return args[0]
    return None


def _unwrap(annotation: Any) -> list[Any]:
    """Flatten `Optional[X]`, `X | None`, `Annotated[X, ...]` down to candidates."""
    origin = get_origin(annotation)
    if origin is None:
        return [annotation]
    args = get_args(annotation)
    out: list[Any] = [annotation]
    for arg in args:
        if arg is type(None):
            continue
        out.extend(_unwrap(arg))
    return out


_WORD_SPLIT = re.compile(r"_+")


def _humanise(name: str) -> str:
    return " ".join(w.capitalize() for w in _WORD_SPLIT.split(name) if w)
