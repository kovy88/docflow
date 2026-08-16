# Evaluation

This document reports what has actually been measured, and is explicit about
what has not. No number below is estimated, projected, or "typically
expected" — if a number isn't here, it hasn't been run.

## Run it yourself

```bash
cd backend
uv run docflow-eval                        # rule-based baseline + fixture provider
uv run docflow-eval --provider anthropic    # requires DOCFLOW_LLM_ANTHROPIC_API_KEY
uv run docflow-eval --provider openai       # requires DOCFLOW_LLM_OPENAI_API_KEY
```

Output goes to `backend/eval_results/` as both JSON and a Markdown report
(`latest.md` / `latest.json`, plus a timestamped copy of each run). The
report reproduced below is `backend/eval_results/latest.md`, generated
2026-08-13.

## Methodology

**Corpus:** 120 synthetic documents (`docflow-eval`'s default; `--size` to
change it), generated from templates in
`backend/src/docflow/eval/dataset.py` with a fixed seed (`20240613`) so the
corpus is byte-identical across runs — two evaluation runs that disagree,
disagree because the system changed, not because the input did. Covers all
four built-in document types (invoice, purchase order, receipt, contract) in
a realistic mix, with **deliberately injected difficulty**: European
decimal-comma numbers, ambiguous day/month dates, currency shown only as a
symbol, unusual field-label phrasing, values split across pages,
phone-numbers-that-look-like-amounts, table-only data with no inline labels,
diacritic-stripped text (simulating OCR loss on Czech), rounded totals, and
credit notes (negative amounts).

**Why synthetic, and what that costs.** A public, field-labeled corpus of
real Czech/Central-European business documents does not exist, and real
customer invoices cannot be committed to a public repository. So the corpus
is generated from records the harness already knows, making ground truth
exact by construction instead of hand-labeled. **The honest limitation:**
synthetic documents are drawn from a distribution this codebase authored, so
they cannot surprise it the way real documents do — unusual vendor layouts,
multi-column tables, poor scans, and handwriting are under-represented.
Numbers here are an **upper bound** on real-world accuracy, not a prediction
of it. What synthetic data *does* support honestly: relative comparison
(baseline vs. model, prompt v1 vs. v2, model A vs. model B over identical
inputs) and regression detection (a change that breaks date parsing shows up
immediately).

**Match levels:** *exact* (string-identical after minimal cleanup), and
*normalized* (type-aware — `39 930,00 Kč` equals `39930.00`; the headline
number). **Doc success** is the fraction of documents where *every required*
field is correct — the number that maps to "does a human have to
intervene," which is stricter than average field accuracy and the more
honest headline for a product decision.

## Results — 2026-08-13 run

> **Language-model accuracy is NOT measured in this run.** Both rows below
> were produced by the same deterministic, rule-based logic — one directly
> (`baseline`), one wrapped behind the `LLMProvider` interface as a fixture
> (`fixture/claude-opus-5`, labeled `[deterministic local extractor, NOT a
> language model]` in the report itself). **No API key was configured in
> this environment, so no language model was called, and neither row
> describes model performance.** Model accuracy, cost per document, and
> latency are deliberately left blank rather than estimated. To produce
> them: set `DOCFLOW_LLM_ANTHROPIC_API_KEY` and run
> `uv run docflow-eval --provider anthropic`.

| Run | Field acc. (normalized) | Required fields | Critical fields | Doc success | Review rate | Cost/doc | Mean latency |
|---|---|---|---|---|---|---|---|
| baseline (rules) | 52.2% | 46.7% | 81.2% | 5.8% | 83.3% | $0.0000 | 0.8 ms |
| fixture (not an LLM) | 52.2% | 46.7% | 81.2% | 5.8% | 95.8% | $0.0000 | 4.3 ms |
| real LLM (Anthropic/OpenAI) | **not yet measured** | — | — | — | — | — | — |

Both measured rows land on identical accuracy because the fixture provider
*is* the rule-based baseline, called through the LLM interface instead of
directly — that's the point of it (see [AI.md](AI.md#provider-abstraction)),
not a coincidence to explain away. The review rate differs (83.3% vs. 95.8%)
because confidence scoring runs against each path slightly differently — the
baseline path never invokes the baseline-cross-check signal (there's nothing
independent to cross-check against itself), while the fixture path exercises
the full scoring formula including the self-agreement guard described in
[AI.md](AI.md#confidence-scoring).

### Precision / recall (both rows, identical)

Precision 84.7%, recall 38.2%, F1 52.6% (1,088 true positives, 197 false
positives, 1,763 false negatives). High precision, low recall is exactly
what a keyword/pattern baseline should produce: when it commits to a value
it's usually right, but it leaves most fields blank rather than guessing —
particularly line items and nested objects (below).

### Where the baseline fails completely

| Field | Accuracy |
|---|---|
| `line_items.*` (all sub-fields) | 0.0% |
| `customer`, `customer.name`, `customer.registration_id` | 0.0% |
| `tax_rate` | 0.0% |
| `subtotal` | 36.2% |

A regex/keyword baseline has no mechanism for table extraction or
disambiguating "the second party mentioned" as the customer rather than the
supplier — it was never designed to. This is the expected shape of a
rule-based floor, not a bug: it's *supposed* to be beatable, so that a real
model's score means something relative to it. A model scoring below this
baseline on line items would be a real red flag; scoring near it on the
fields regex handles fine (dates, simple totals) would not be surprising.

### Accuracy by injected difficulty (both rows, identical)

| Hazard | Accuracy |
|---|---|
| diacritics_stripped | 57.8% |
| (none) | 55.9% |
| no_currency_code | 53.7% |
| extra_numbers | 52.8% |
| label_variants | 51.9% |
| decimal_comma | 51.6% |
| ambiguous_date | 50.4% |

### Confidence calibration (fixture row only — the metric requires a
### confidence score, which the raw baseline path doesn't produce)

| Band | Fields | Actual accuracy | Mean score |
|---|---|---|---|
| high | 1,102 | 83.9% | 0.972 |
| medium | 141 | 85.8% | 0.816 |
| low | 42 | 100.0% | 0.544 |

Read cautiously: this is the fixture extractor's confidence against its own
(rule-based) accuracy, not a real model's. It confirms the calibration
*machinery* works end to end — bands, scoring, aggregation — not that the
thresholds are correctly tuned for actual LLM behavior. That's exactly the
kind of claim that has to wait for a real run.

## What's measured vs. not — summary

| Claim | Status |
|---|---|
| Pipeline runs end-to-end (upload → classify → extract → validate → score → route) | **Measured** — 227 automated tests, plus manual full-stack verification through `docker compose` |
| Rule-based baseline accuracy on synthetic corpus | **Measured** — this document |
| Confidence-scoring machinery functions correctly | **Measured** — calibration table above, against the fixture |
| Classification accuracy (heuristic cascade) | **Measured** — 100% on this corpus (see caveat: synthetic documents use the same vocabulary the heuristic was written against) |
| Real LLM extraction accuracy | **Not measured** — no API key configured in this environment |
| Real LLM cost per document | **Not measured** |
| Real LLM latency | **Not measured** |
| Accuracy on real (non-synthetic) documents | **Not measured** — no ground-truth corpus of real documents exists for this project |
| OCR accuracy on scanned/low-DPI documents | **Not measured** — no scanned ground-truth set has been run |

If you're evaluating this project and want the real-LLM row filled in:
supply an Anthropic or OpenAI API key and run the command at the top of this
document. Nothing else changes — the corpus, the metrics, and the report
format are identical regardless of which extractor is under test.
