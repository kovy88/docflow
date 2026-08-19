"""Evaluation metrics.

## Match definitions, and why there are three

Comparing extracted values to ground truth sounds trivial and is not. `"39930.00"`
and `"39 930,00 Kč"` are the same amount; `"2024-03-14"` and `"14.03.2024"` are the
same date; `"ACME Solutions s.r.o."` and `"ACME Solutions, s.r.o."` are the same
company. A strict string comparison would report all three as errors and produce a
number that is precise, reproducible, and wrong.

So three levels are reported side by side:

* **exact** — identical strings. Honest and pessimistic; the floor.
* **normalised** — equal after type-aware normalisation (dates parsed, money as
  decimals, whitespace and punctuation folded). **This is the headline metric**,
  because it is the one that predicts whether a human has to intervene.
* **fuzzy** — normalised, plus near-string-match for free text (names, addresses),
  where a trailing comma is not a defect a user would care about.

Reporting all three is the point. A gap between exact and normalised says the model
is right but formats differently; a gap between normalised and fuzzy says it is
approximately right on free text. Both are actionable; a single blended number is
not.

## The metric that actually matters commercially

`document_success_rate` — the fraction of documents where **every required field is
correct**. Field accuracy of 97% sounds excellent and can still mean half of all
documents need a human, because errors cluster. Per-document is the number that
maps to the customer's experience.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from docflow.domain.confidence import normalise_for_matching
from docflow.schemas.base import DocumentTypeSpec, FieldKind
from docflow.schemas.fields import normalize_currency, parse_date, parse_decimal
from docflow.validation.paths import MISSING, flatten, get_path

# Free-text similarity above which two strings count as a fuzzy match.
FUZZY_THRESHOLD = 0.90


class MatchLevel:
    EXACT = "exact"
    NORMALISED = "normalised"
    FUZZY = "fuzzy"
    MISS = "miss"


@dataclass
class FieldOutcome:
    document_id: str
    field_path: str
    kind: str
    expected: Any
    actual: Any
    level: str
    is_required: bool = False
    is_critical: bool = False
    confidence: float | None = None
    confidence_band: str | None = None

    @property
    def correct_exact(self) -> bool:
        return self.level == MatchLevel.EXACT

    @property
    def correct_normalised(self) -> bool:
        return self.level in (MatchLevel.EXACT, MatchLevel.NORMALISED)

    @property
    def correct_fuzzy(self) -> bool:
        return self.level != MatchLevel.MISS

    @property
    def expected_present(self) -> bool:
        return not _is_empty(self.expected)

    @property
    def actual_present(self) -> bool:
        return not _is_empty(self.actual)


def _is_empty(value: Any) -> bool:
    if value is None or value is MISSING:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, list | dict) and not value


def compare(  # noqa: PLR0911 — one branch per field kind's comparison rule
    expected: Any, actual: Any, kind: FieldKind
) -> str:
    """Classify one field comparison into a match level."""
    expected_empty, actual_empty = _is_empty(expected), _is_empty(actual)

    if expected_empty and actual_empty:
        # Both agree the value is absent — a correct answer, not a non-answer.
        return MatchLevel.EXACT
    if expected_empty or actual_empty:
        return MatchLevel.MISS

    if str(expected) == str(actual):
        return MatchLevel.EXACT

    if kind in (FieldKind.MONEY, FieldKind.NUMBER):
        return MatchLevel.NORMALISED if _money_equal(expected, actual) else MatchLevel.MISS

    if kind is FieldKind.DATE:
        return MatchLevel.NORMALISED if _date_equal(expected, actual) else MatchLevel.MISS

    if kind is FieldKind.CURRENCY:
        return (
            MatchLevel.NORMALISED
            if normalize_currency(expected) == normalize_currency(actual)
            else MatchLevel.MISS
        )

    if kind is FieldKind.BOOLEAN:
        return MatchLevel.NORMALISED if bool(expected) == bool(actual) else MatchLevel.MISS

    if kind in (FieldKind.IDENTIFIER, FieldKind.BANK_ACCOUNT):
        # Identifiers are matched without separators (`19-2000145399/0800` vs
        # `192000145399/0800`) but never fuzzily — a bank account that is 90%
        # right is 100% wrong.
        left = normalise_for_matching(str(expected))
        right = normalise_for_matching(str(actual))
        return MatchLevel.NORMALISED if left == right else MatchLevel.MISS

    left = normalise_for_matching(str(expected))
    right = normalise_for_matching(str(actual))
    if left == right:
        return MatchLevel.NORMALISED
    if left and right and SequenceMatcher(None, left, right).ratio() >= FUZZY_THRESHOLD:
        return MatchLevel.FUZZY
    return MatchLevel.MISS


def _money_equal(expected: Any, actual: Any) -> bool:
    try:
        left, right = parse_decimal(expected), parse_decimal(actual)
    except (InvalidOperation, ValueError, TypeError):
        return False
    if left is None or right is None:
        return False
    # A one-hundredth tolerance absorbs rounding in the rendering, not real error.
    return abs(left - right) <= Decimal("0.01")


def _date_equal(expected: Any, actual: Any) -> bool:
    left, right = parse_date(expected), parse_date(actual)
    return left is not None and left == right


@dataclass
class DocumentOutcome:
    document_id: str
    document_type: str
    predicted_type: str
    fields: list[FieldOutcome] = field(default_factory=list)
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")
    input_tokens: int = 0
    output_tokens: int = 0
    needs_review: bool = False
    confidence: float | None = None
    validation_errors: int = 0
    failed: bool = False
    error_code: str | None = None
    difficulty: list[str] = field(default_factory=list)
    # Per-document raw diagnostics — only populated when a run opts in via
    # `RunnerConfig(persist_predictions=True)`. Aggregate FieldOutcomes above can
    # tell you *that* a field was wrong on 36 documents but not *which* 36 or what
    # the model actually returned for them; these three are what closes that gap
    # for a root-cause investigation, without growing every routine run's report.
    expected_fields: dict[str, Any] | None = None
    raw_model_output: dict[str, Any] | None = None
    parsed_fields: dict[str, Any] | None = None

    @property
    def type_correct(self) -> bool:
        return self.document_type == self.predicted_type

    @property
    def required_fields(self) -> list[FieldOutcome]:
        return [f for f in self.fields if f.is_required]

    def all_required_correct(self) -> bool:
        required = self.required_fields
        return bool(required) and all(f.correct_normalised for f in required)

    def fully_correct(self) -> bool:
        return bool(self.fields) and all(f.correct_normalised for f in self.fields)


@dataclass
class EvaluationReport:
    """Aggregated results. Every number here traces to counted outcomes."""

    label: str
    extractor: str
    provider: str
    model: str
    prompt_version: str
    documents: list[DocumentOutcome] = field(default_factory=list)
    corpus_size: int = 0
    wall_clock_seconds: float = 0.0

    # -------------------------------------------------------------- accuracy

    def field_accuracy(self, level: str = MatchLevel.NORMALISED) -> float | None:
        outcomes = [f for d in self.documents for f in d.fields]
        return _rate(outcomes, lambda f: _at_level(f, level))

    def required_field_accuracy(self, level: str = MatchLevel.NORMALISED) -> float | None:
        outcomes = [f for d in self.documents for f in d.required_fields]
        return _rate(outcomes, lambda f: _at_level(f, level))

    def critical_field_accuracy(self, level: str = MatchLevel.NORMALISED) -> float | None:
        outcomes = [f for d in self.documents for f in d.fields if f.is_critical]
        return _rate(outcomes, lambda f: _at_level(f, level))

    def document_success_rate(self) -> float | None:
        """Documents where every required field is right. The commercial metric."""
        usable = [d for d in self.documents if not d.failed]
        return _rate(usable, lambda d: d.all_required_correct())

    def classification_accuracy(self) -> float | None:
        usable = [d for d in self.documents if not d.failed]
        return _rate(usable, lambda d: d.type_correct)

    # ------------------------------------------------------------- precision

    def precision_recall(self) -> dict[str, float | None]:
        """Treating "extracting a value at all" as the positive class.

        Distinguishes the two failure modes that a single accuracy number hides:
        *omission* (the value was there and we missed it — low recall) and
        *fabrication* (we produced a value that is wrong or was not there at all —
        low precision). They call for opposite fixes.
        """
        true_positive = false_positive = false_negative = 0
        for document in self.documents:
            for outcome in document.fields:
                if outcome.expected_present and outcome.actual_present:
                    if outcome.correct_normalised:
                        true_positive += 1
                    else:
                        # A wrong value counts against both: we asserted something
                        # untrue *and* failed to capture the truth.
                        false_positive += 1
                        false_negative += 1
                elif outcome.expected_present and not outcome.actual_present:
                    false_negative += 1
                elif not outcome.expected_present and outcome.actual_present:
                    false_positive += 1

        precision = _safe_div(true_positive, true_positive + false_positive)
        recall = _safe_div(true_positive, true_positive + false_negative)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall and (precision + recall) > 0
            else None
        )
        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
        }

    # ---------------------------------------------------------- operational

    def review_rate(self) -> float | None:
        usable = [d for d in self.documents if not d.failed]
        return _rate(usable, lambda d: d.needs_review)

    def failure_rate(self) -> float | None:
        return _rate(self.documents, lambda d: d.failed)

    def validation_failure_rate(self) -> float | None:
        usable = [d for d in self.documents if not d.failed]
        return _rate(usable, lambda d: d.validation_errors > 0)

    def latency(self) -> dict[str, float]:
        values = sorted(d.latency_ms for d in self.documents if not d.failed)
        if not values:
            return {}
        return {
            "mean_ms": sum(values) / len(values),
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "p99_ms": _percentile(values, 0.99),
            "max_ms": float(values[-1]),
        }

    def cost(self) -> dict[str, float]:
        usable = [d for d in self.documents if not d.failed]
        if not usable:
            return {}
        total = sum((d.cost_usd for d in usable), Decimal("0"))
        return {
            "total_usd": float(total),
            "per_document_usd": float(total / len(usable)),
            "input_tokens": sum(d.input_tokens for d in usable),
            "output_tokens": sum(d.output_tokens for d in usable),
            "tokens_per_document": (
                sum(d.input_tokens + d.output_tokens for d in usable) / len(usable)
            ),
        }

    # ------------------------------------------------- confidence calibration

    def calibration(self) -> list[dict[str, Any]]:
        """Empirical accuracy within each confidence band.

        This is how the confidence score is *validated* rather than asserted. The
        property that must hold: fields the system calls HIGH should be right much
        more often than fields it calls LOW. If the bands do not separate, the
        score is decoration and the review-routing policy is arbitrary.
        """
        buckets: dict[str, list[FieldOutcome]] = {}
        for document in self.documents:
            for outcome in document.fields:
                if outcome.confidence_band:
                    buckets.setdefault(outcome.confidence_band, []).append(outcome)

        rows = []
        for band in ("high", "medium", "low"):
            outcomes = buckets.get(band, [])
            if not outcomes:
                continue
            correct = sum(1 for f in outcomes if f.correct_normalised)
            rows.append(
                {
                    "band": band,
                    "fields": len(outcomes),
                    "accuracy": correct / len(outcomes),
                    "mean_confidence": sum(f.confidence or 0 for f in outcomes) / len(outcomes),
                }
            )
        return rows

    def worst_fields(self, limit: int = 12) -> list[dict[str, Any]]:
        """Fields ranked by error count. The improvement backlog."""
        stats: dict[str, dict[str, Any]] = {}
        for document in self.documents:
            for outcome in document.fields:
                entry = stats.setdefault(
                    outcome.field_path,
                    {
                        "field_path": outcome.field_path,
                        "total": 0,
                        "errors": 0,
                        "required": outcome.is_required,
                    },
                )
                entry["total"] += 1
                if not outcome.correct_normalised:
                    entry["errors"] += 1

        ranked = [s for s in stats.values() if s["errors"]]
        for entry in ranked:
            entry["accuracy"] = 1 - entry["errors"] / entry["total"]
        ranked.sort(key=lambda s: (-s["errors"], s["field_path"]))
        return ranked[:limit]

    def field_accuracy_table(self) -> list[dict[str, Any]]:
        """Every scored field, not just the worst ones (see `worst_fields`).

        Distinguishes the two ways a field can be wrong, per field: a
        *missing prediction* (ground truth had a value, the extractor didn't) vs.
        a *false prediction* (the extractor produced a value ground truth doesn't
        have) — the same distinction `precision_recall()` makes in aggregate,
        broken out per field instead of summed across all of them.
        """
        stats: dict[str, dict[str, Any]] = {}
        for document in self.documents:
            for outcome in document.fields:
                entry = stats.setdefault(
                    outcome.field_path,
                    {
                        "field_path": outcome.field_path,
                        "kind": outcome.kind,
                        "total": 0,
                        "correct": 0,
                        "missing_prediction": 0,
                        "false_prediction": 0,
                        "required": outcome.is_required,
                        "critical": outcome.is_critical,
                    },
                )
                entry["total"] += 1
                if outcome.correct_normalised:
                    entry["correct"] += 1
                elif outcome.expected_present and not outcome.actual_present:
                    entry["missing_prediction"] += 1
                elif not outcome.expected_present and outcome.actual_present:
                    entry["false_prediction"] += 1

        rows = list(stats.values())
        for entry in rows:
            entry["accuracy"] = entry["correct"] / entry["total"] if entry["total"] else None
        return sorted(rows, key=lambda r: r["field_path"])

    def by_document_type(self) -> list[dict[str, Any]]:
        """Field accuracy and document success broken down by document type.

        Small-n types (see docs/EVALUATION_DATASET.md — contract is 4 documents
        in the current corpus) report `documents` alongside the rate specifically
        so a reader isn't handed a percentage with no way to judge how much
        weight it can bear.
        """
        buckets: dict[str, list[DocumentOutcome]] = {}
        for document in self.documents:
            if document.failed:
                continue
            buckets.setdefault(document.document_type, []).append(document)

        rows = []
        for doc_type, docs in sorted(buckets.items()):
            fields = [f for d in docs for f in d.fields]
            correct = sum(1 for f in fields if f.correct_normalised)
            successes = sum(1 for d in docs if d.all_required_correct())
            rows.append(
                {
                    "document_type": doc_type,
                    "documents": len(docs),
                    "field_accuracy": correct / len(fields) if fields else None,
                    "document_success_rate": successes / len(docs) if docs else None,
                }
            )
        return rows

    def by_difficulty(self) -> list[dict[str, Any]]:
        """Accuracy per injected hazard — which difficulty actually costs accuracy."""
        buckets: dict[str, list[FieldOutcome]] = {}
        for document in self.documents:
            for tag in document.difficulty or ["(none)"]:
                buckets.setdefault(tag, []).extend(document.fields)

        rows = []
        for tag, outcomes in sorted(buckets.items()):
            if not outcomes:
                continue
            correct = sum(1 for f in outcomes if f.correct_normalised)
            rows.append(
                {"difficulty": tag, "fields": len(outcomes), "accuracy": correct / len(outcomes)}
            )
        return sorted(rows, key=lambda r: r["accuracy"])

    def predictions(self) -> list[dict[str, Any]]:
        """Per-document ground truth / raw model output / parsed value.

        Empty unless the run was started with `RunnerConfig(persist_predictions=True)`
        — omitted from `to_dict()` entirely otherwise, so a routine run's report is
        byte-for-byte unchanged in shape. Exists to answer questions aggregate
        FieldOutcomes cannot: *which* documents a field failed on, and what the model
        actually returned for them, rather than only a pass/fail count.
        """
        return [
            {
                "document_id": d.document_id,
                "document_type": d.document_type,
                "difficulty": d.difficulty,
                "expected": d.expected_fields,
                "raw_model_output": d.raw_model_output,
                "parsed": d.parsed_fields,
            }
            for d in self.documents
            if d.expected_fields is not None
        ]

    def to_dict(self) -> dict[str, Any]:
        result = {
            "label": self.label,
            "extractor": self.extractor,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "corpus_size": self.corpus_size,
            "documents_evaluated": len(self.documents),
            "wall_clock_seconds": round(self.wall_clock_seconds, 2),
            "accuracy": {
                "field_exact": self.field_accuracy(MatchLevel.EXACT),
                "field_normalised": self.field_accuracy(MatchLevel.NORMALISED),
                "field_fuzzy": self.field_accuracy(MatchLevel.FUZZY),
                "required_normalised": self.required_field_accuracy(),
                "critical_normalised": self.critical_field_accuracy(),
                "document_success_rate": self.document_success_rate(),
                "classification": self.classification_accuracy(),
            },
            "precision_recall": self.precision_recall(),
            "operational": {
                "review_rate": self.review_rate(),
                "failure_rate": self.failure_rate(),
                "validation_failure_rate": self.validation_failure_rate(),
            },
            "latency": self.latency(),
            "cost": self.cost(),
            "calibration": self.calibration(),
            "worst_fields": self.worst_fields(),
            "field_accuracy_table": self.field_accuracy_table(),
            "by_document_type": self.by_document_type(),
            "by_difficulty": self.by_difficulty(),
        }
        predictions = self.predictions()
        if predictions:
            result["predictions"] = predictions
        return result


def build_field_outcomes(
    *,
    document_id: str,
    spec: DocumentTypeSpec,
    expected: dict[str, Any],
    actual: dict[str, Any],
    confidences: dict[str, tuple[float | None, str | None]] | None = None,
) -> list[FieldOutcome]:
    """Compare one document's extraction against its ground truth.

    Iterates the **union** of expected and actual leaf paths. Iterating only the
    expected paths would make fabricated fields invisible — the extractor could
    invent values for free.
    """
    from docflow.validation.paths import to_template

    confidences = confidences or {}
    paths: set[str] = set()
    for source in (expected, actual):
        for path, _value in flatten(source):
            paths.add(path)

    outcomes: list[FieldOutcome] = []
    for path in sorted(paths):
        template = to_template(path)
        field_spec = spec.field_by_path(template)
        if field_spec is None:
            continue

        expected_value = get_path(expected, path)
        actual_value = get_path(actual, path)
        confidence, band = confidences.get(path, (None, None))

        outcomes.append(
            FieldOutcome(
                document_id=document_id,
                field_path=path,
                kind=field_spec.kind.value,
                expected=None if expected_value is MISSING else expected_value,
                actual=None if actual_value is MISSING else actual_value,
                level=compare(expected_value, actual_value, field_spec.kind),
                is_required=template in spec.required_paths,
                is_critical=template in spec.critical_paths,
                confidence=confidence,
                confidence_band=band,
            )
        )
    return outcomes


# ------------------------------------------------------------------- helpers


def _at_level(outcome: FieldOutcome, level: str) -> bool:
    if level == MatchLevel.EXACT:
        return outcome.correct_exact
    if level == MatchLevel.FUZZY:
        return outcome.correct_fuzzy
    return outcome.correct_normalised


def _rate(items: list[Any], predicate: Any) -> float | None:
    if not items:
        return None
    return round(sum(1 for i in items if predicate(i)) / len(items), 4)


def _safe_div(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


# Z-scores for the confidence levels this project actually reports. A lookup
# table rather than `scipy.stats.norm.ppf` — one more dependency to avoid
# pulling in for two numbers used nowhere else in this codebase.
_Z_SCORES = {0.90: 1.645, 0.95: 1.960, 0.99: 2.576}


def wilson_score_interval(
    successes: int, total: int, *, confidence: float = 0.95
) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion.

    Chosen over the naive `p ± z*sqrt(p(1-p)/n)` normal approximation because
    the naive form produces nonsensical bounds outside [0, 1] exactly where
    this project's numbers tend to land — near 100% (document success,
    required-field accuracy) or over a small n (contract-only breakdowns, see
    docs/EVALUATION_DATASET.md). Wilson stays inside [0, 1] by construction
    and is the standard "keep it simple but not naive" choice for this
    situation, not a research-grade method chosen for its own sake.
    """
    if total == 0:
        return None
    z = _Z_SCORES.get(round(confidence, 2))
    if z is None:
        raise ValueError(f"No z-score tabulated for confidence={confidence}; add one to _Z_SCORES")

    p = successes / total
    denom = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denom
    margin = (z * ((p * (1 - p) / total + z**2 / (4 * total**2)) ** 0.5)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _percentile(sorted_values: list[int], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, round(fraction * (len(sorted_values) - 1)))
    return float(sorted_values[index])


def compare_reports(baseline: EvaluationReport, candidate: EvaluationReport) -> dict[str, Any]:
    """Side-by-side deltas. The output that answers 'is the AI worth it?'."""

    def delta(a: float | None, b: float | None) -> float | None:
        return round(b - a, 4) if a is not None and b is not None else None

    return {
        "baseline": baseline.label,
        "candidate": candidate.label,
        "field_accuracy": {
            "baseline": baseline.field_accuracy(),
            "candidate": candidate.field_accuracy(),
            "delta": delta(baseline.field_accuracy(), candidate.field_accuracy()),
        },
        "required_field_accuracy": {
            "baseline": baseline.required_field_accuracy(),
            "candidate": candidate.required_field_accuracy(),
            "delta": delta(baseline.required_field_accuracy(), candidate.required_field_accuracy()),
        },
        "document_success_rate": {
            "baseline": baseline.document_success_rate(),
            "candidate": candidate.document_success_rate(),
            "delta": delta(baseline.document_success_rate(), candidate.document_success_rate()),
        },
        "cost_per_document_usd": {
            "baseline": baseline.cost().get("per_document_usd"),
            "candidate": candidate.cost().get("per_document_usd"),
        },
        "mean_latency_ms": {
            "baseline": baseline.latency().get("mean_ms"),
            "candidate": candidate.latency().get("mean_ms"),
        },
    }


_UNUSED = (dt,)  # imported for type context in comparisons
