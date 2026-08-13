"""Per-field confidence scoring.

## What this is, and what it is not

This is **not** a calibrated probability from the model. Asking an LLM "how confident
are you?" produces a number that is well known to be poorly calibrated, and treating
it as a probability would be dishonest. What this module produces is a *heuristic risk
score* built from several independent signals, most of which are deterministic and
verifiable without the model's cooperation.

The score's job is to answer one operational question: **should a human look at this
field?** Its quality is therefore measured by how well the bands separate correct from
incorrect fields on the evaluation set (see `docs/EVALUATION.md`), not by whether the
number resembles a probability.

## Signals

| Signal | Weight | Rationale |
|---|---|---|
| Evidence grounding | 0.40 | Does the value actually occur in the source text? The single strongest hallucination detector available without a second model call, and completely deterministic. Also carries cross-extractor agreement — a value the rule-based baseline independently found is strongly supported. |
| Model self-report | 0.20 | **Not currently collected.** See below. |
| Type/format cleanliness | 0.15 | A date that needed fuzzy parsing, or a number recovered from a mangled string, is more likely wrong. |
| Validation outcome | 0.15 | A field implicated in a failed rule is suspect. |
| Extraction context | 0.10 | OCR'd text is harder; fields extracted from it start lower. |

### On model self-reported confidence

The weight exists but nothing supplies it, so it is passed as `None` and the
remaining weights renormalise (`ConfidenceSignals.weighted` divides by the weight
actually present, not by the total). This is a deliberate choice, not an omission:

* asking for a per-field confidence roughly doubles the output schema and the
  output tokens, on every document;
* LLM self-reported confidence is well documented as poorly calibrated, and
  correlates with fluency rather than correctness;
* the deterministic signals above are *verifiable*, and a bad value that the model
  is confident about is exactly the case they catch.

The slot is kept so that adding it later — if evaluation shows it earns its cost —
is a one-line change rather than a re-weighting exercise.

Weights are declared as constants rather than learned, because we do not have enough
labelled production data to fit them without overfitting. They are a defensible prior;
`scripts/calibrate_confidence.py` re-derives the *band thresholds* from the evaluation
set, which is the part that actually matters operationally.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from docflow.domain.enums import ConfidenceBand

# --------------------------------------------------------------------- thresholds

# Band boundaries. Re-derived from the evaluation set by
# `scripts/calibrate_confidence.py`; see docs/EVALUATION.md for the current
# empirical accuracy within each band.
HIGH_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.60


def band_for(score: float) -> ConfidenceBand:
    if score >= HIGH_THRESHOLD:
        return ConfidenceBand.HIGH
    if score >= MEDIUM_THRESHOLD:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


# ------------------------------------------------------------------------ weights

W_GROUNDING = 0.40
W_MODEL = 0.20
W_FORMAT = 0.15
W_VALIDATION = 0.15
W_CONTEXT = 0.10

_TOTAL_WEIGHT = W_GROUNDING + W_MODEL + W_FORMAT + W_VALIDATION + W_CONTEXT


@dataclass(frozen=True, slots=True)
class ConfidenceSignals:
    """Inputs to the score for one field.

    Every component is in [0, 1]. Anything unknown should be passed as `None` so it
    can be excluded from the weighted mean rather than silently scored as zero —
    "we didn't measure this" and "this looks bad" are different statements.
    """

    grounding: float | None = None
    model_reported: float | None = None
    format_cleanliness: float | None = None
    validation: float | None = None
    context: float | None = None

    def weighted(self) -> float:
        pairs = [
            (self.grounding, W_GROUNDING),
            (self.model_reported, W_MODEL),
            (self.format_cleanliness, W_FORMAT),
            (self.validation, W_VALIDATION),
            (self.context, W_CONTEXT),
        ]
        present = [(v, w) for v, w in pairs if v is not None]
        if not present:
            # No signal at all: sit exactly on the review boundary rather than
            # pretending either confidence or alarm.
            return MEDIUM_THRESHOLD
        total_weight = sum(w for _, w in present)
        return sum(v * w for v, w in present) / total_weight


@dataclass(slots=True)
class FieldConfidence:
    field_path: str
    score: float
    band: ConfidenceBand
    signals: ConfidenceSignals
    reasons: list[str] = field(default_factory=list)
    # Set when a field is flagged for a reason other than its band — currently a
    # `critical` field scoring below its type's stricter critical threshold. Without
    # this the document is routed to review with a reason naming the field, but the
    # field itself renders as green in the UI, so the reviewer is told to check
    # something the interface has not highlighted.
    forced_review: bool = False

    @property
    def needs_review(self) -> bool:
        return self.forced_review or self.band is not ConfidenceBand.HIGH


# ------------------------------------------------------------------- normalisation

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalise_for_matching(text: str) -> str:
    """Aggressively fold text for substring comparison.

    Grounding must survive the difference between how a value appears in a PDF and
    how it appears after normalisation: `1 234,56 Kč` in the document vs `1234.56`
    in the extracted record. Stripping everything but alphanumerics and lowercasing
    handles thousands separators, currency symbols, NBSPs and decimal-comma locales
    in one step.
    """
    folded = unicodedata.normalize("NFKD", text).casefold()
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _NON_ALNUM.sub("", folded)


# Backwards-compatible private alias used inside this module.
_normalise = normalise_for_matching


def _tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text).casefold()
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return [t for t in _WS.split(_NON_ALNUM.sub(" ", folded)) if t]


def grounding_score(value: Any, source_text: str, *, source_normalised: str | None = None) -> float:
    """How strongly is `value` supported by literal evidence in the source?

    1.00 — the normalised value appears verbatim in the normalised source
    0.75 — every token of a multi-token value appears somewhere in the source
    0.40 — a majority of tokens appear
    0.10 — the value does not appear at all (likely hallucinated or inferred)

    Values that are legitimately *not* expected to appear verbatim (booleans,
    enums, computed fields) should not be scored with this function; the caller
    passes `None` for the grounding signal instead.

    `source_normalised` lets the caller normalise the (large) source text once per
    document instead of once per field.
    """
    if value is None:
        return 0.10
    text = str(value).strip()
    if not text:
        return 0.10

    haystack = source_normalised if source_normalised is not None else _normalise(source_text)
    needle = _normalise(text)
    if not needle:
        return 0.10
    if needle in haystack:
        return 1.0

    toks = _tokens(text)
    if not toks:
        return 0.10
    hits = sum(1 for t in toks if _normalise(t) and _normalise(t) in haystack)
    ratio = hits / len(toks)
    if ratio == 1.0:
        return 0.75
    if ratio >= 0.5:
        return 0.40
    return 0.10


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_field(
    field_path: str,
    *,
    signals: ConfidenceSignals,
    reasons: list[str] | None = None,
) -> FieldConfidence:
    score = clamp(signals.weighted())
    return FieldConfidence(
        field_path=field_path,
        score=round(score, 4),
        band=band_for(score),
        signals=signals,
        reasons=reasons or [],
    )


def aggregate(
    fields: list[FieldConfidence],
    *,
    required_paths: set[str] | None = None,
) -> float:
    """Document-level confidence.

    The naive mean is the wrong aggregate: a document with 20 correct fields and one
    wrong bank account is not 95% good — it is unusable, because the one field that
    matters is wrong. So we take the mean but floor it at the minimum confidence of
    any *required* field. A single low-confidence required field drags the document
    down to that field's level, which is exactly the routing behaviour we want.
    """
    if not fields:
        return 0.0

    mean = sum(f.score for f in fields) / len(fields)
    required = required_paths or set()
    required_scores = [f.score for f in fields if f.field_path in required]
    if required_scores:
        return round(min(mean, min(required_scores)), 4)
    return round(mean, 4)
