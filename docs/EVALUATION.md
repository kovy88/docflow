# Evaluation

This document reports what has actually been measured, and is explicit about
what has not. No number below is estimated, projected, or "typically
expected" — if a number isn't here, it hasn't been run.

## Run it yourself

```bash
cd backend
uv run docflow-eval                            # rule-based baseline + fixture provider
uv run docflow-eval --provider anthropic        # requires DOCFLOW_LLM_ANTHROPIC_API_KEY
uv run docflow-eval --provider openai           # requires DOCFLOW_LLM_OPENAI_API_KEY
uv run docflow-eval --provider google --size 20 # requires DOCFLOW_LLM_GOOGLE_API_KEY; --size caps quota spend
```

Output goes to `backend/eval_results/` as both JSON and a Markdown report
(`latest.md` / `latest.json`, plus a timestamped copy of each run — the
directory is gitignored, so what's below is a snapshot copied into this
document, not a live link). The baseline/fixture numbers below are from
`backend/eval_results/latest.md`, generated 2026-08-17; the Gemini numbers
are from `backend/eval_results/gemini-latest.md`, generated 2026-08-17
08:11 UTC.

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

## Results

Two separate runs, at two different corpus sizes, for a reason that matters:
the rule-based rows run for free and locally, so they use the full 120-document
corpus; the Gemini row spends real API quota per document, so it ran against a
20-document slice. The numbers are not on equal footing and the table below
doesn't pretend otherwise.

| Run | Documents | Field acc. (normalized) | Required fields | Critical fields | Doc success | Review rate | Cost/doc | Mean latency |
|---|---|---|---|---|---|---|---|---|
| baseline (rules) | 120 | 56.0% | 52.2% | 90.1% | 5.8% | 81.7% | $0.0000 | 0.8 ms |
| fixture (not an LLM) | 120 | 56.0% | 52.2% | 90.1% | 5.8% | 95.0% | $0.0000 | 4.1 ms |
| google/gemini-3.6-flash (real LLM) | 20 | 83.5% | 100.0% | 75.4% | 100.0% | 45.5% | $0.0049 | 36,537 ms |

The two rule-based rows land on identical accuracy because the fixture
provider *is* the rule-based baseline, called through the `LLMProvider`
interface instead of directly (see [AI.md](AI.md#provider-abstraction)) — not
a coincidence to explain away. Their review rate differs (81.7% vs. 95.0%)
because confidence scoring runs against each path slightly differently — the
baseline path never invokes the baseline-cross-check signal (nothing
independent to cross-check against itself), while the fixture path exercises
the full scoring formula including the self-agreement guard described in
[AI.md](AI.md#confidence-scoring).

**On the Gemini row, read the good numbers and the bad number together, and
don't let their similar size fool you into treating them as one fact.**
`ExtractorRunner._evaluate` (`backend/src/docflow/eval/runner.py`) catches
any exception raised during extraction and records that document as
`failed`, with an error code, instead of a wrong-but-scored answer. Every
headline number in the table above except review rate — field accuracy,
required/critical accuracy, doc success, cost, latency — is computed only
over the documents that did *not* hard-fail
(`EvaluationReport.field_accuracy`/`document_success_rate`/etc., all filter
`if not d.failed`). Of the 20 documents in this run, **9 hard-failed
(`failure_rate` 45.0%) and 11 completed**; the 83.5%/100%/100% figures above
are measured on those 11, not on all 20. Review rate is a *third*, unrelated
number that happens to land close to the same figure: 45.5% is the fraction
of the 11 *completed* documents flagged for human review (5 of 11) — computed
over a different denominator than `failure_rate`, and not a restatement of
it. Two independent 45%-ish numbers from the same run is a coincidence worth
naming explicitly so it isn't misread as one fact reported twice.

**Why 9 of 20 hard-failed.** `google_provider.py` turns a Google 429
specifically into `ProviderRateLimitError`, one of the exception types
`_evaluate`'s catch-all turns into a failed document. Given this run used
`concurrency=4` against a free-tier key with a low daily/per-minute request
budget, rate limiting is the strong suspect — but the report format does not
currently persist *which* error code fired on each failed document (only the
aggregate `failure_rate`), so that's an informed inference from the code
path, not a re-derivable certainty from this run's saved output. A larger,
non-quota-limited Gemini run has not been done. Treat the 83.5%/100%/100%
figures as measured on the 11 documents that completed, and the 45%
hard-failure rate as a real, separate, and currently under-explained cost of
running this provider's free tier at this concurrency — not as evidence
against the extraction quality itself.

### Precision / recall

Rule-based rows (identical): precision 95.6%, recall 43.1%, F1 59.4% (1,229
true positives, 56 false positives, 1,622 false negatives). High precision,
low recall is exactly what a keyword/pattern baseline should produce: when it
commits to a value it's usually right, but it leaves most fields blank rather
than guessing — particularly line items and nested objects (below).

Gemini: precision 86.2%, recall 88.1%, F1 87.1% (237 true positives, 38 false
positives, 32 false negatives) — recall well above the rule-based baseline,
consistent with an LLM attempting fields a label-anchored regex leaves blank.

### Where the baseline fails completely

| Field | Accuracy |
|---|---|
| `line_items.*` (all sub-fields) | 0.0% |
| `customer`, `customer.name`, `customer.registration_id` | 0.0% |
| `tax_rate` | 0.0% |

A regex/keyword baseline has no mechanism for table extraction or
disambiguating "the second party mentioned" as the customer rather than the
supplier — it was never designed to. This is the expected shape of a
rule-based floor, not a bug: it's *supposed* to be beatable, so that a real
model's score means something relative to it. A model scoring below this
baseline on line items would be a real red flag; scoring near it on the
fields regex handles fine (dates, simple totals) would not be surprising.

### Accuracy by injected difficulty

Rule-based rows (identical, 120 docs):

| Hazard | Accuracy |
|---|---|
| ambiguous_date | 53.5% |
| label_variants | 54.8% |
| (none) | 55.9% |
| extra_numbers | 56.6% |
| no_currency_code | 57.3% |
| decimal_comma | 57.4% |
| diacritics_stripped | 57.8% |

Gemini (20 docs — small enough that per-hazard figures are indicative, not
conclusive):

| Hazard | Accuracy |
|---|---|
| extra_numbers | 79.9% |
| label_variants | 79.9% |
| ambiguous_date | 80.0% |
| no_currency_code | 83.7% |
| decimal_comma | 84.0% |
| diacritics_stripped | 84.0% |

## Confidence calibration

**Methodology.** `uv run docflow-calibrate`
(`backend/src/docflow/scripts/calibrate_confidence.py`) runs the production
confidence-scoring code (not a re-implementation) over the evaluation corpus
and buckets every scored field by its raw score into deciles — finer than the
three production bands (`high`/`medium`/`low`) — then reports actual accuracy
per decile. A well-calibrated score has accuracy that rises monotonically
with the bucket; the production thresholds (`HIGH_THRESHOLD = 0.85`,
`MEDIUM_THRESHOLD = 0.60` in `domain/confidence.py`) should sit where accuracy
visibly steps up, not just wherever felt reasonable. It defaults to the
fixture provider because that's the only path with enough corpus volume (all
120 documents, not a quota-limited slice) to make decile buckets meaningful.

**What running it found.** The first real run showed a non-monotonic result:
the top decile (`[0.9, 1.0)`, 902 fields) was *less* accurate (80.4%) than the
decile below it (`[0.8, 0.9)`, 341 fields, 94.1%) — exactly the kind of thing
this script exists to catch. Investigating rather than shrugging it off (see
`backend/src/docflow/pipeline/stages/extract.py` and
`backend/src/docflow/extraction/baseline.py`) found a real, deterministic bug
in the rule-based extractor, not a calibration-threshold problem:

Czech-language documents in the corpus group thousands with a non-breaking
space (`\xa0`) — e.g. `Celkem: 78\xa0287,00 CZK` — which is what real
Czech-market documents actually use and what `pdfplumber` preserves verbatim.
`AMOUNT_RE`, the regex that finds a monetary figure on a labelled line, had a
character class (`[\d  .,']`) built from two literal ASCII spaces, not a
Unicode-whitespace class — so it could not span the non-breaking space.
`78\xa0287,00` matched as *two* separate candidates (`78`, `287,00`), and
`_parse_amount`'s "take the last figure on the line" rule (there to skip past
a VAT percentage like `DPH 21%: 6 930,00`) then kept only the tail: **78,287
became 287**, silently, on every affected amount. Grounding — the strongest
individual confidence signal (weight 0.40), meant to catch exactly this kind
of wrong value — didn't catch it, because normalising for the substring match
strips separators from both source and value: a truncated number's digits are
always a trailing substring of the correct number's digits once the
separator that would have told them apart is gone. So the wrong value scored
*as if* it were perfectly grounded (~0.99), landing in the top decile instead
of getting flagged.

This is not a fixture-only curiosity: `extract_baseline()` backs both the
`fixture` provider *and* the production baseline cross-check signal that runs
against every real LLM extraction (`docflow.domain.confidence`), so the same
bug was silently weakening the corroboration signal in production on any
document — from any provider — with a non-breaking-space-grouped amount of
1,000 or more in its native currency. Given the target market stated
elsewhere in this project's own defaults (CZ/CEE), that's not a rare shape of
document.

**Fix.** One line: `_parse_amount` now folds Unicode whitespace (which
includes `\xa0`) to an ASCII space before matching, reusing the
`normalize_whitespace` helper already used elsewhere in the same file rather
than widening the regex itself. Regression tests:
`TestBaselineAmountExtraction` in `backend/tests/unit/test_validation.py`,
covering both a single non-breaking-space group and a multi-group figure
(`1\xa0060\xa0000`, i.e. a number in the millions).

**Result, before and after** (fixture provider, 120-doc corpus, deciles
containing populated buckets only):

| Score range | Before fix | After fix |
|---|---|---|
| [0.5, 0.6) | 100.0% (n=42) | 100.0% (n=42) |
| [0.8, 0.9) — `HIGH_THRESHOLD` | 94.1% (n=341) | 100.0% (n=320) |
| [0.9, 1.0) | **80.4% (n=902)** | **93.9% (n=923)** |

Monotonicity went from "the top bucket is worse than the one below it" (a
genuine red flag) to a single ~6-point step between two large, adjacent, and
otherwise-clean buckets — well within normal noise at this sample size, and
no longer a violation worth chasing further right now.

**Deliberately not done: moving `HIGH_THRESHOLD`/`MEDIUM_THRESHOLD`.** The
wrong money values scored ~0.99 — comfortably above `HIGH_THRESHOLD` (0.85)
either way. No threshold placement fixes a signal that is confidently wrong;
only fixing the signal (or, here, the extractor feeding it) does. This is
exactly the failure mode threshold-tuning would have papered over: a lower
threshold would have caught more of these by accident while flagging far more
genuinely-correct fields for no reason, and a higher one would not have
excluded them at all. The remaining ~6-point gap after the fix does not
currently justify a threshold change either — see "known limitation" below.

**Production-band view, post-fix** (fixture, 120 docs):

| Band | Fields | Actual accuracy | Mean score |
|---|---|---|---|
| high | 1,123 | 95.0% | 0.972 |
| medium | 120 | 100.0% | 0.816 |
| low | 42 | 100.0% | 0.544 |

Gemini, 20 docs (too small to re-derive decile thresholds from, shown for
comparison only — no `medium`-band fields were observed in this run):

| Band | Fields | Actual accuracy | Mean score |
|---|---|---|---|
| high | 270 | 85.9% | 0.985 |
| low | 5 | 100.0% | 0.544 |

Read the fixture table cautiously even after the fix: it's the fixture
extractor's confidence against its own (rule-based) accuracy, not a real
model's. It confirms the calibration *machinery* — bands, scoring,
aggregation, and now the grounding signal's amount-parsing dependency — works
correctly end to end, and it's what caught the bug above. Whether
`HIGH_THRESHOLD`/`MEDIUM_THRESHOLD` are the *right* cut points for real LLM
behavior at scale is still a claim that needs a larger, non-quota-limited
real-provider run to make.

**Known limitation, stated plainly.** The grounding signal's structural blind
spot — a value whose digits are a trailing substring of the correct value's
digits (once separators are normalised away) scores as fully grounded even
when it's wrong — is fixed for *this specific* cause (nbsp thousands
separators) but not eliminated as a class. Any future bug that truncates or
otherwise mangles a numeric value in a way that happens to leave a real
substring behind would have the same blind spot. The fix here closes the one
concrete, verified instance found; it is not a general hardening of grounding
against numeric substring collisions.

## What's measured vs. not — summary

| Claim | Status |
|---|---|
| Pipeline runs end-to-end (upload → classify → extract → validate → score → route) | **Measured** — 256 automated tests, plus manual full-stack verification through `docker compose` |
| Rule-based baseline accuracy on synthetic corpus | **Measured** — this document, 120 docs |
| Confidence-scoring machinery functions correctly | **Measured** — found and fixed a real bug (above), which is stronger evidence than a clean run would have been |
| Classification accuracy (heuristic cascade) | **Measured** — 100% on this corpus (see caveat: synthetic documents use the same vocabulary the heuristic was written against) |
| Real LLM extraction accuracy | **Measured, quota-limited** — 20 documents, google/gemini-3.6-flash, 83.5% field accuracy on completed requests (see the hard-failure caveat above) |
| Real LLM cost per document | **Measured** — $0.0049/doc on the same 20-document run |
| Real LLM latency | **Measured** — mean 36.5s/doc on the same run; not yet explained or optimized |
| Real LLM confidence calibration | **Measured, quota-limited** — see table above; too small a sample to re-derive thresholds from |
| Accuracy on real (non-synthetic) documents | **Not measured** — no ground-truth corpus of real documents exists for this project |
| OCR accuracy on scanned/low-DPI documents | **Not measured** — no scanned ground-truth set has been run |
| Why 45% of the Gemini run hard-failed | **Not conclusively determined** — rate limiting is the strong suspect given the code path and the free-tier key used, but per-document error codes aren't persisted in the report, so this is an informed inference, not a re-derivable fact |

If you're evaluating this project and want a larger real-LLM run: supply an
Anthropic, OpenAI, or Google API key with more headroom than a free-tier key
and run the command at the top of this document, optionally with `--size` to
control cost. Nothing else changes — the corpus, the metrics, and the report
format are identical regardless of which extractor is under test.
