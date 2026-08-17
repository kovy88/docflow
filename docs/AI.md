# AI design

This document covers the parts of Docflow that involve a language model: the
provider abstraction, the extraction strategy, prompt injection defense, and
— the part with the most engineering in it — confidence scoring. Pipeline
staging and validation are covered in [ARCHITECTURE.md](ARCHITECTURE.md);
measured results are in [EVALUATION.md](EVALUATION.md).

## The central bet

Nobody adopts a document-processing tool because the extraction is powered
by an LLM. They adopt it if they can trust the output enough to stop having
someone retype it. An LLM is good at turning messy text into structured
guesses; it is not, by itself, a system anyone should trust unsupervised.
The gap between "the model produced an answer" and "a business can act on
this without checking" is what most of this codebase is: three validation
layers, multi-signal confidence scoring, a review queue with correction
tracking, and an evaluation harness that measures rather than assumes. The
model call is one stage out of eleven.

## Provider abstraction

```python
class LLMProvider(abc.ABC):
    @abc.abstractmethod
    async def complete_structured(self, request: LLMRequest) -> LLMResponse: ...
    async def health_check(self) -> bool: ...
    async def aclose(self) -> None: ...
```

Four implementations (`backend/src/docflow/llm/`): `AnthropicProvider`,
`OpenAIProvider`, `GeminiProvider` (Google), and `FixtureProvider` — a
deterministic, rule-based stand-in that implements the same interface
without calling a real model. Selected by `DOCFLOW_LLM_PROVIDER`; nothing
above this interface (pipeline, classification, confidence scoring) knows or
cares which one is active.

The fixture provider exists for three reasons, not one:
- **Tests and CI never need a network call or a real API key.**
- **The full pipeline is demoable with zero setup** — `docker compose up` and
  `docflow-seed` produce real classification, extraction, validation, and
  confidence scores with no credentials, because every stage after "get some
  structured data" runs exactly as it would against a real model.
- **It draws an honest line in the evaluation report.** Every number the
  fixture produces is labeled "NOT a language model" wherever it appears
  ([EVALUATION.md](EVALUATION.md)) — it establishes that the pipeline
  works, not that extraction is accurate.

Swapping providers, or adding another, touches one file and zero pipeline
code — see [ADR-004](adr/004-llm-provider-abstraction.md) for why this was
worth the abstraction cost.

## Structured output, not free text

Every extraction call requests structured output constrained to the target
document type's JSON Schema (Anthropic's `output_config.format`, OpenAI's
equivalent, Gemini's `response_json_schema`). The model does not have tool
access and does not produce a free prose channel in this path — its only way
to respond is a value conforming to a schema Docflow controls. This is a
security property as much as a correctness one: see
[SECURITY.md#prompt-injection](SECURITY.md#prompt-injection-defense) for what
that closes off.

Schemas are normalized to a common subset before being handed to a provider,
because "JSON Schema" is not quite one format across vendors — differences in
supported keywords and strictness are reconciled in
`backend/src/docflow/llm/schema.py` so the same document-type schema works
unchanged across every provider.

## Classification: cheap-first

A heuristic scorer runs first (keyword/pattern matching against each
registered document type). Its output uses a saturating combination —
`earned / (earned + HALF_EVIDENCE)` — rather than dividing by the total
possible weight, so one strong, unambiguous signal (e.g., the literal word
"faktura" plus an invoice-number pattern) is enough to classify confidently
without needing corroboration from every other weaker signal. An LLM
classification call only happens when the heuristic's best score is below a
threshold. Most documents have unambiguous tells; reserving the model call
for the ambiguous minority is both cheaper and — per
[EVALUATION.md](EVALUATION.md)'s classification-accuracy numbers — not a
meaningful accuracy trade at the classification step specifically.

## Prompt versioning

Every prompt template is a row in `prompt_versions`
([DATABASE.md](DATABASE.md)), immutable once created, keyed by
`(key, version)`. Every `extractions` row records exactly which
`prompt_key`/`prompt_version` (plus model, model version, schema version,
pipeline version) produced it. The reason this is a database table and not
just a string constant in code: reproducibility and the future feedback
loop both need to ask "which prompt version produced this result" as a
query, not an inference from a deploy timestamp.

## Confidence scoring

Confidence is a **weighted combination of independent signals**, not a model
logprob and not a single threshold. A model's self-reported confidence
(`model_reported`) is deliberately not requested from any provider and
carries weight `0.20` in the formula below that is simply never filled in
this codebase — self-reported LLM confidence is well documented as poorly
calibrated, and folding an unreliable signal into a otherwise-principled
score would make the whole score less trustworthy, not more. If it's ever
added, it comes back the same way every other signal does: as one input,
never the whole answer.

| Signal | Weight | What it measures |
|---|---|---|
| `grounding` | 0.40 | Does the value appear (post-normalization) in the source document text? |
| `model_reported` | 0.20 | *Not currently populated — see above.* |
| `format_cleanliness` | 0.15 | Did the value parse cleanly into its declared type (date, money, currency)? |
| `validation` | 0.15 | Did this field pass, warn, or fail the validation layers? |
| `context` | 0.10 | Was the source text OCR'd (0.65) or native (0.95)? OCR errors produce plausible-looking wrong values. |

The final score is a weighted mean over whichever signals are actually
present for that field — `sum(value × weight) / sum(weight of present
signals)`, not a mean over a constant denominator. A signal that wasn't
measured is *excluded*, not scored as zero: "we didn't check this" and
"this looks wrong" are different statements, and conflating them would
punish fields whose type has no format check (nothing to parse) as if they
were suspect. If literally no signal is available for a field, its score
sits exactly on the medium/review boundary rather than defaulting to
confident or alarmed.

**Grounding** compares the extracted value against the source text after an
aggressive normalization pass (NFKD-fold, strip everything but alphanumerics,
lowercase) so `1 234,56 Kč` in the document and `1234.56` in the extracted
record are recognized as the same evidence. Tokens under three characters are
excluded from the fallback partial-match path — without that floor, a value
like `99` spuriously "grounds" against an unrelated larger number that merely
contains those two digits (`999999.99`), which made grounding a false
positive machine for short numeric fields.

**Baseline agreement** (does the rule-based baseline extractor land on the
same value?) is folded *into* grounding rather than given its own weight —
both signals answer "is this value supported by something other than the
model's say-so," and scoring them independently would double-count the same
evidence. It's skipped entirely when the "model" being scored *is* the
baseline (the fixture provider runs the same heuristic internally) — without
that guard, the fixture appears to agree with itself on every field and
confidence inflates toward 1.0 for a reason that has nothing to do with
accuracy. This was a real bug caught during manual testing, not a
hypothetical: confidence dropped from a spurious ~0.99 to a correct ~0.87
once the self-agreement case was excluded.

**Bands:** `high` ≥ 0.85, `medium` ≥ 0.60, otherwise `low`. Thresholds are
empirical starting points tuned against the evaluation corpus
([EVALUATION.md](EVALUATION.md) reports actual calibration: whether "high"
fields are actually more often correct than "medium" ones), not arbitrary
round numbers.

## Review routing

A document is routed to human review if *any* of: overall confidence is
below the document type's threshold, a field marked `critical` for that
document type is below a stricter critical-field threshold (even if overall
confidence is fine), a required field is missing, or a business validation
rule fails outright. Every trigger is recorded as a specific reason string
on the document (`review_reasons`), and a field that caused the routing is
itself flagged (`forced_review`) — a document can't be routed to review
"because of field X" while field X renders as green and unflagged in the UI.
That mismatch was a real bug found during testing; the fix
(`FieldConfidence.forced_review`) keeps the two in sync structurally rather
than by convention.

## What isn't built

- **Model-reported confidence** is designed for (a column in the weight
  table, a `None` waiting to be filled) but not populated — see above.
- **The correction feedback loop** — using `field_corrections` to actually
  improve prompts or flag systematically weak fields — has the data model
  in place (`ix_corrections_prompt`) but no automated consumer yet. It's
  future work, not a claim.
- **Real-model accuracy** is not measured in this environment — see
  [EVALUATION.md](EVALUATION.md) for exactly what that means and how to
  produce the number yourself with an API key.
