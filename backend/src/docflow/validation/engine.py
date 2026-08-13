"""Three-layer validation.

    Layer 1 — syntax     Pydantic. Does the payload have the right shape and types?
    Layer 2 — semantic   Is the data internally coherent? (dates ordered, totals add
                         up, IBAN checksum passes, currency is one we support)
    Layer 3 — business   Document-type-specific policy from the spec's `rule_ids`.

Layers 2 and 3 share one mechanism — they differ in *which* rules a document type
opts into, not in how rules are executed. Splitting them into separate engines would
have duplicated the runner for no benefit.

Two properties matter more than the individual rules:

* **A failing rule never raises.** Every rule returns issues; an exception inside a
  rule is caught and reported as an `internal` issue against that rule. One badly
  behaved rule must not be able to fail a document that eleven other rules approve.

* **Validation is pure.** `validate()` takes data and returns issues. No database, no
  network, no clock other than the one passed in. That is what makes the entire rule
  catalogue testable in milliseconds and what lets the evaluation harness replay
  historical extractions through today's rules.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog

from docflow.domain.enums import ValidationSeverity
from docflow.schemas.base import DocumentTypeSpec

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Issue:
    rule_id: str
    code: str
    severity: ValidationSeverity
    message: str
    field_path: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks_approval(self) -> bool:
        return self.severity is ValidationSeverity.ERROR


@dataclass(slots=True)
class RuleContext:
    """Everything a rule may read. Deliberately small and side-effect free."""

    data: dict[str, Any]
    spec: DocumentTypeSpec
    # Source text, when available. Rules that need to check a value against the
    # document (rather than against other values) use this.
    source_text: str = ""
    today: dt.date = field(default_factory=lambda: dt.datetime.now(dt.UTC).date())
    # Per-organization overrides merged over the spec defaults.
    options: dict[str, Any] = field(default_factory=dict)

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


RuleFn = Callable[[RuleContext], Iterable[Issue]]


@dataclass(slots=True)
class ValidationResult:
    issues: list[Issue]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is ValidationSeverity.WARNING]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def affected_paths(self) -> set[str]:
        return {i.field_path for i in self.issues if i.field_path}

    def paths_with_severity(self, severity: ValidationSeverity) -> set[str]:
        return {i.field_path for i in self.issues if i.field_path and i.severity is severity}

    def by_rule(self) -> dict[str, list[Issue]]:
        out: dict[str, list[Issue]] = {}
        for issue in self.issues:
            out.setdefault(issue.rule_id, []).append(issue)
        return out


class RuleRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, RuleFn] = {}

    def register(self, rule_id: str) -> Callable[[RuleFn], RuleFn]:
        def decorator(fn: RuleFn) -> RuleFn:
            if rule_id in self._rules:
                raise ValueError(f"Duplicate validation rule id: {rule_id}")
            self._rules[rule_id] = fn
            return fn

        return decorator

    def get(self, rule_id: str) -> RuleFn | None:
        return self._rules.get(rule_id)

    def ids(self) -> list[str]:
        return sorted(self._rules)


registry = RuleRegistry()


class ValidationEngine:
    def __init__(self, rules: RuleRegistry | None = None) -> None:
        self._registry = rules or registry

    def validate(self, ctx: RuleContext) -> ValidationResult:
        issues: list[Issue] = []
        for rule_id in ctx.spec.rule_ids:
            rule = self._registry.get(rule_id)
            if rule is None:
                # A spec referencing a rule that does not exist is a deployment bug,
                # not a document problem. Surface it loudly but keep validating.
                logger.error("validation.rule_missing", rule_id=rule_id, spec=ctx.spec.key)
                issues.append(
                    Issue(
                        rule_id=rule_id,
                        code="rule_not_found",
                        severity=ValidationSeverity.INFO,
                        message=f"Validation rule {rule_id!r} is not registered",
                    )
                )
                continue
            try:
                issues.extend(rule(ctx))
            except Exception as exc:
                logger.exception("validation.rule_failed", rule_id=rule_id, spec=ctx.spec.key)
                issues.append(
                    Issue(
                        rule_id=rule_id,
                        code="rule_execution_failed",
                        severity=ValidationSeverity.WARNING,
                        message=f"Validation rule {rule_id!r} could not be evaluated",
                        context={"error": type(exc).__name__},
                    )
                )
        return ValidationResult(issues=issues)


def validate_syntax(
    spec: DocumentTypeSpec, payload: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[Issue]]:
    """Layer 1. Returns `(normalised_payload, issues)`.

    A `None` payload means the data was unusable and later layers should be skipped —
    running arithmetic rules over a payload whose `total` is the string "see attached"
    produces noise, not information.

    Pydantic errors are mapped onto field paths so the review UI can highlight the
    offending input rather than showing a stack trace.
    """
    from pydantic import ValidationError

    try:
        model = spec.model.model_validate(payload)
    except ValidationError as exc:
        issues = [
            Issue(
                rule_id="schema",
                code=str(err.get("type", "invalid")),
                severity=ValidationSeverity.ERROR,
                message=_readable_pydantic_error(err),
                field_path=".".join(str(p) for p in err.get("loc", ())) or None,
                context={"input": _truncate(err.get("input"))},
            )
            for err in exc.errors()
        ]
        return None, issues

    return model.model_dump(mode="json"), []


def _readable_pydantic_error(err: Mapping[str, Any]) -> str:
    loc = ".".join(str(p) for p in err.get("loc", ())) or "payload"
    msg = err.get("msg", "is invalid")
    return f"{loc}: {msg}"


def _truncate(value: Any, limit: int = 120) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def iter_issues(*groups: Iterable[Issue]) -> Iterator[Issue]:
    for group in groups:
        yield from group
