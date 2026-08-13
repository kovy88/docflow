"""Document classification — cheap first, model second.

Classification picks the extraction schema. Getting it wrong is expensive in a
specific way: the document is extracted against the wrong field set, so every field
is confidently wrong rather than obviously missing.

The strategy is a **cascade**:

  1. Score the text against each type's keyword and pattern hints. Deterministic,
     sub-millisecond, free.
  2. If the winning score is confident *and* clearly ahead of the runner-up,
     take it.
  3. Otherwise ask the model, using a truncated sample rather than the whole
     document.

Step 1 resolves the large majority of real documents, because business documents
announce themselves — an invoice has the word "invoice" on it. Paying an LLM to
confirm that is waste. The cascade is why per-document cost stays low without
sacrificing the hard cases, and the split between the two paths is measured by the
evaluation harness rather than assumed.

The margin requirement in step 2 matters as much as the threshold. A document
scoring 0.9 for "invoice" and 0.85 for "purchase_order" is *not* confidently
classified even though 0.9 looks high — those two types are genuinely confusable
and the runner-up being close is the signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from docflow.schemas.base import DocumentTypeSpec

logger = structlog.get_logger(__name__)

# Only the first N characters are scored. Type-identifying signals cluster at the
# top of a document, and scoring a 40-page contract in full adds cost without
# adding signal.
SAMPLE_CHARS = 6000
# Characters of document sent to the model when the cheap path is not confident.
LLM_SAMPLE_CHARS = 4000

# The runner-up must be this far behind for a heuristic result to be trusted.
MIN_MARGIN = 0.15

# Evidence weight at which a type scores 0.5. Chosen so that the three-or-four
# strong signals a real document actually carries clear the 0.65 escalation
# threshold, while a single incidental keyword does not. Calibrated against the
# evaluation corpus — see `docs/EVALUATION.md` for the measured split between the
# heuristic and model paths.
HALF_EVIDENCE = 4.0


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    document_type_key: str
    confidence: float
    method: str  # "heuristic" | "llm" | "fallback" | "explicit"
    scores: dict[str, float]
    runner_up: str | None = None

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.65


def classify_heuristic(text: str, specs: list[DocumentTypeSpec]) -> ClassificationResult:
    """Keyword and pattern scoring with saturating evidence.

    Scores use `earned / (earned + HALF_EVIDENCE)` rather than
    `earned / total_possible`. The difference is not cosmetic — it is the
    difference between a classifier that works and one that escalates everything.

    Dividing by the total possible weight asks "does this document contain *every*
    hint I know about?", which no real document does: a genuine invoice says
    "invoice", "due date" and "VAT" and stops. Under that normaliser a perfectly
    obvious invoice scored 0.34 and fell below the escalation threshold, so every
    document went to the model and the cheap path saved nothing.

    Saturation asks the right question — "is there *enough* evidence?" — and
    plateaus once there is. Additional confirming keywords stop mattering, which is
    correct: the tenth invoice-ish word tells you nothing the first three did not.
    """
    sample = text[:SAMPLE_CHARS].lower()
    folded = _fold(sample)
    scores: dict[str, float] = {}

    for spec in specs:
        hints = spec.classification
        if not hints.keywords and not hints.patterns:
            continue

        earned = 0.0

        for keyword, weight in hints.keywords.items():
            if _fold(keyword.lower()) in folded:
                earned += weight

        for pattern, weight in hints.patterns.items():
            try:
                if re.search(pattern, sample, re.IGNORECASE):
                    earned += weight
            except re.error:  # pragma: no cover - patterns are code-defined
                logger.warning("classification.bad_pattern", spec=spec.key, pattern=pattern)

        # Negative evidence subtracts before saturation, so a document that looks
        # like two types at once lands in the ambiguous band rather than scoring
        # high for both.
        for keyword, weight in hints.negative_keywords.items():
            if _fold(keyword.lower()) in folded:
                earned -= weight

        earned = max(0.0, earned)
        scores[spec.key] = round(earned / (earned + HALF_EVIDENCE), 6) if earned else 0.0

    if not scores:
        return ClassificationResult("generic", 0.0, "fallback", {})

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_key, best_score = ranked[0]

    # No evidence for anything. Naming whichever type sorted first would be an
    # arbitrary guess presented as a result; `generic` with zero confidence is the
    # honest answer and routes the document to review.
    if best_score <= 0:
        return ClassificationResult("generic", 0.0, "fallback", dict.fromkeys(scores, 0.0))
    runner_up_key, runner_up_score = ranked[1] if len(ranked) > 1 else (None, 0.0)

    # An ambiguous result is reported at its *margin*, not its raw score, so the
    # caller's confidence test sees the ambiguity rather than the leader's height.
    margin = best_score - runner_up_score
    effective = best_score if margin >= MIN_MARGIN else min(best_score, 0.5 + margin)

    return ClassificationResult(
        document_type_key=best_key,
        confidence=round(effective, 4),
        method="heuristic",
        scores={k: round(v, 4) for k, v in ranked},
        runner_up=runner_up_key,
    )


def _fold(text: str) -> str:
    """Accent-insensitive comparison — see `extraction.baseline.fold_accents`."""
    from docflow.extraction.baseline import fold_accents

    return fold_accents(text)


def build_llm_candidates(specs: list[DocumentTypeSpec]) -> str:
    return "\n".join(f"- {spec.key}: {spec.description}" for spec in specs)


CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "description": "The key of the best-matching document type",
        },
        "confidence": {
            "type": "number",
            "description": "How certain you are, from 0 to 1",
        },
        "reasoning": {
            "type": "string",
            "description": "One short sentence explaining the choice",
        },
    },
    "required": ["document_type", "confidence"],
    "additionalProperties": False,
}


def truncate_for_classification(text: str) -> str:
    """Head + tail sample.

    The head carries the document title and issuer; the tail carries totals,
    signature blocks and payment instructions. The middle of a long document is
    mostly line items, which are the least type-discriminating part of it.
    """
    if len(text) <= LLM_SAMPLE_CHARS:
        return text
    head = int(LLM_SAMPLE_CHARS * 0.7)
    tail = LLM_SAMPLE_CHARS - head
    return f"{text[:head]}\n\n[...]\n\n{text[-tail:]}"
