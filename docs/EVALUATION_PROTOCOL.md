# Evaluation Protocol

This document defines *exactly* how Docflow's AI quality is measured — the dataset,
the fields, and every metric's precise formula. It is the specification;
[EVALUATION.md](EVALUATION.md) is the results measured against it. If a number in
EVALUATION.md can't be reproduced from the definitions here, the protocol document
is wrong and should be fixed, not the number.

Implementation: `backend/src/docflow/eval/` (`dataset.py`, `runner.py`, `metrics.py`,
`cli.py`, `scan_simulation.py`). This document describes what that code does; it does
not duplicate the code.

## Dataset

- **Size:** 120 documents (the harness's default; `--size` overrides for a cheaper
  slice, `--regenerate` rebuilds).
- **Document types:** invoice, purchase_order, receipt, contract — the four types
  `docflow.schemas.registry` has schemas for. No fifth type exists to evaluate.
- **Synthetic vs. real:** 100% synthetic. Generated from Python templates
  (`eval/dataset.py`), not sourced from real business documents. See
  [EVALUATION_DATASET.md](EVALUATION_DATASET.md) for the actual measured
  composition (type/language mix, and where it diverges from the target mix) and
  why that matters for which claims the corpus can support.
- **Language:** Czech-majority, mixed with English. Invoices are 70% Czech / 30%
  English by construction; contracts are always English; purchase orders and
  receipts are always Czech. See EVALUATION_DATASET.md for the real measured split.
- **Source:** `backend/src/docflow/eval/dataset.py`'s four `generate_*` functions,
  driven by a seeded `random.Random(20240613)` — the same seed on every run
  produces a byte-identical corpus, checked into `backend/eval_data/corpus.jsonl`
  (via `write_corpus`/`read_corpus`).
- **Ground truth format:** one `GroundTruth` record per document (`document_id`,
  `document_type`, `text`, `fields: dict`, `difficulty: list[str]`, `language`,
  `notes`), serialized as JSON Lines. `fields` is a dict matching the document
  type's schema shape exactly — the same shape the extractor is asked to produce —
  so comparison is a structural diff, not a translation between two formats.
- **Injected difficulty:** every document may carry one or more hazard tags
  (`DIFFICULTY_FEATURES` in `dataset.py`): `decimal_comma`, `ambiguous_date`,
  `no_currency_code`, `label_variants`, `extra_numbers`, `diacritics_stripped`,
  `rounding`, `credit_note`, plus `ocr_scanned` (added by `scan_simulation.py`,
  not a generator). These are what make the corpus harder than a clean-text
  toy benchmark; `EvaluationReport.by_difficulty()` reports accuracy per tag.

## Fields

The fields evaluated are exactly the fields each document type's Pydantic schema
declares (`backend/src/docflow/schemas/types/*.py`), retrieved from the live
schema registry — nothing here is invented or aspirational.

### Invoice (`schemas/types/invoice.py`)
`invoice_number`, `supplier` (`.name`, `.registration_id`, `.vat_id`), `customer`
(`.name`, `.registration_id`), `issue_date`, `due_date`, `currency`, `subtotal`,
`tax_amount`, `tax_rate`, `total`, `variable_symbol`, `bank_details` (`.iban`,
`.account_number`), `line_items[]` (`.description`, `.quantity`, `.unit`,
`.unit_price`, `.line_total`)

- **Required:** `invoice_number`, `issue_date`, `total`, `currency`, `supplier`,
  `supplier.name`, `customer.name`, `line_items[].description`
- **Critical:** `invoice_number`, `due_date`, `total`, `variable_symbol`,
  `bank_details.iban`, `bank_details.account_number`
- Review threshold `0.85` · critical-field threshold `0.92`

### Purchase order (`schemas/types/purchase_order.py`)
`po_number`, `buyer` (`.name`), `supplier` (`.name`), `order_date`,
`requested_delivery_date`, `currency`, `subtotal`, `tax_amount`, `total`,
`shipping_terms`, `line_items[]` (`.description`, `.quantity`, `.unit`,
`.unit_price`, `.line_total`)

- **Required:** `po_number`, `order_date`, `total`, `currency`, `buyer`,
  `buyer.name`, `supplier`, `supplier.name`, `line_items`,
  `line_items[].description`, `line_items[].quantity`
- **Critical:** `po_number`, `requested_delivery_date`, `total`
- Review threshold `0.85` · critical-field threshold `0.92`

### Receipt (`schemas/types/receipt.py`)
`merchant_name`, `merchant_vat_id`, `receipt_number`, `purchase_date`, `currency`,
`subtotal`, `tax_amount`, `total`, `payment_method`

- **Required:** `merchant_name`, `purchase_date`, `total`, `currency`
- **Critical:** `merchant_name`, `purchase_date`, `total`
- Review threshold `0.78` · critical-field threshold `0.88` (both deliberately
  lower than invoice's — a receipt is a lower-stakes document by design)

### Contract (`schemas/types/contract.py`)
`title`, `contract_type`, `parties[]` (`.name`), `effective_date`,
`expiration_date`, `term_months`, `auto_renewal`, `notice_period_days`,
`total_value`, `currency`, `governing_law`, `confidentiality`

- **Required:** `title`, `contract_type`, `effective_date`, `parties`,
  `parties[].name`
- **Critical:** `effective_date`, `expiration_date`, `auto_renewal`,
  `notice_period_days`
- Review threshold `0.80` · critical-field threshold `0.90`

`supplier.address` and `customer.address` are declared in the invoice schema and
are scored (see EVALUATION.md's worst-fields tables) but are not `required` or
`critical` — a deliberate schema choice given the corpus's own 0% measured
accuracy on them (see [EVALUATION_ERROR_ANALYSIS.md](EVALUATION_ERROR_ANALYSIS.md)).

## Metrics

All formulas are implemented in `eval/metrics.py`; this section states them in
prose so they can be checked without reading Python.

### Field-level accuracy
`correct fields / evaluated fields`, evaluated over the **union** of expected and
actual leaf paths across every document (`build_field_outcomes`) — not just the
paths the model happened to answer. A fabricated field the model invents but
ground truth doesn't have counts as an error here, same as a missed one; iterating
only expected paths would make fabrication invisible.

### Required-field accuracy
Same formula, restricted to paths in that document type's `required_paths`. This
is the number that most directly answers "did the system get the things a human
actually needs."

### Document-level success
**Not the same thing as field accuracy — this is the stricter, commercially
meaningful number.** A document "succeeds" when **every required field** on it is
correct (`DocumentOutcome.all_required_correct()`): `bool(required) and
all(f.correct_normalised for f in required)`. One wrong required field fails the
whole document, even if every other field (required or not) is perfect. This is
deliberately pessimistic: a document that's 95% right field-by-field but wrong on
`total` is not a document a human can skip checking.

Computed only over documents that did not hard-fail (`if not d.failed`) — a
provider error (rate limit, timeout, malformed output) is a separate, explicit
failure mode (`failure_rate`), not silently folded into a low success score.

### Normalized accuracy
The headline number everywhere in this project. Three match levels are computed
per field (`compare()` in `metrics.py`), reported side by side rather than
blended into one number:

- **exact** — identical strings after minimal cleanup. The pessimistic floor.
- **normalized** — equal after type-aware normalization (below). **This is what
  "accuracy" means unqualified in every report in this repo.**
- **fuzzy** — normalized, plus a `SequenceMatcher` similarity ≥ 0.90 for free-text
  fields (names, addresses) where a trailing comma isn't a real error.

Normalization rules, by field kind:

| Kind | Rule |
|---|---|
| Money / number | Parsed to `Decimal` (`schemas/fields.py::parse_decimal`, handles both `1234.56` and European `1 234,56`); equal within a Kč 0.01 tolerance that absorbs rounding, not real error |
| Date | Parsed to a calendar date (`parse_date`, handles ISO, `DD.MM.YYYY`, `DD/MM/YYYY`, and fuzzy text like "1 February 2024"); compared as dates, not strings |
| Currency | Normalized to an ISO 4217 code (`normalize_currency`) before comparing — `Kč` and `CZK` are the same value. **Not scored on grounding** (see below) for exactly this reason: a correctly-normalized code often doesn't appear verbatim in the source text |
| Boolean | Python truthiness comparison, not string equality |
| Identifier / bank account | Compared with all separators stripped (`normalise_for_matching`) but never fuzzily — `19-2000145399/0800` matches `192000145399/0800`; a 90%-right IBAN is 100% wrong, so no fuzzy tier applies |
| Free text (names, addresses, descriptions) | Whitespace/diacritic/case-folded exact match, or fuzzy (≥0.90 similarity) as a second tier |

Whitespace and diacritics: `normalise_for_matching` (`domain/confidence.py`) does
NFKD decomposition, strips combining marks (diacritics) and everything that isn't
alphanumeric, and lowercases — applied identically to both sides of every
non-money/date/currency comparison, so `"ACME Solutions s.r.o."` and `"ACME
Solutions, s.r.o."` match, and Czech diacritics don't cause a spurious mismatch
against an OCR- or user-stripped variant.

### Review rate
`documents flagged needs_review / documents processed` (excluding hard failures).
`needs_review` is `True` if *any* of: overall document confidence below
`min(document type's review_threshold, the global review_confidence_threshold)`,
any `critical` field's own score below that type's stricter
`critical_field_threshold` (even if overall confidence looks fine), a required
field is missing, or a business validation rule fails outright
(`pipeline/stages/extract.py`). Every trigger is recorded as a specific reason
string, not a single boolean.

### Automation rate
`1 − review rate`, over the same denominator. Not currently reported as a
separate named field in `EvaluationReport` — it is exactly the complement of
review rate and is reported as such rather than as a second, potentially
inconsistent, independently-computed number.

### Latency
Mean, p50, p95 (`EvaluationReport.latency()`), computed only over non-failed
documents, in milliseconds, per document (one document = one extraction call in
this harness's design — see EVALUATION_DATASET.md on what that does and doesn't
represent for a real multi-page document).

### Cost
Mean cost per document (`EvaluationReport.cost()`), from the provider's actual
reported token usage run through `llm/pricing.py`'s published per-model rates —
**measured from real API responses, not estimated from prompt length.** A model
missing from the pricing table returns `$0.0000` rather than raising, and that
gap is itself visible in the report rather than silently passing as "free."

## What this protocol does not define

Confidence calibration (bucket definitions, ECE), the review-threshold
sensitivity analysis, statistical uncertainty (confidence intervals), and cost
attribution beyond per-document mean are defined in
[EVALUATION_ERROR_ANALYSIS.md](EVALUATION_ERROR_ANALYSIS.md) and
[EVALUATION.md](EVALUATION.md) directly, since they're analyses run *on top of*
these base metrics rather than base definitions themselves.
