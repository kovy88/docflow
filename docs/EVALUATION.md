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
DOCFLOW_LLM_MODEL=gpt-5.6-luna uv run docflow-eval --provider openai  # a specific OpenAI model
uv run docflow-eval --provider google --size 20 # requires DOCFLOW_LLM_GOOGLE_API_KEY; --size caps quota spend
```

Output goes to `backend/eval_results/` as both JSON and a Markdown report
(`latest.md` / `latest.json`, plus a timestamped copy of each run — the
directory is gitignored, so what's below is a snapshot copied into this
document, not a live link). The baseline/fixture numbers below are from a
2026-08-18 run; the OpenAI numbers (the primary real-LLM result — full
120-document corpus, no quota limit) are from a 2026-08-18 run against
`gpt-4.1-mini`, after the currency-calibration fix below (an earlier
same-day run, before the fix, is what found it); a second OpenAI run, also
2026-08-18, full 120-document corpus, same seed, targets `gpt-5.6-luna` —
OpenAI's July 2026 cost/speed-tier model — for a direct, same-day comparison
against `gpt-4.1-mini`, together with a third pass of the same corpus
through a real render→degrade→OCR step before extraction (`--scan`, see
`eval/scan_simulation.py`) to measure what a real scan actually costs
accuracy-wise, not just clean synthetic text; the Gemini numbers (a
secondary data point — free-tier quota capped the sample at 20 documents)
are from a 2026-08-17 run, predating the fix.

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

Six runs, at two different corpus sizes, for a reason that matters: the
rule-based row runs for free and locally, so it uses the full 120-document
corpus; every LLM row spends real API cost per document, but only Gemini's
was constrained to a slice — by a free-tier daily quota (20 requests/day),
not by choice. `gpt-4.1-mini`, `gpt-5.6-luna`, and `gpt-5.6-luna (scanned)`
each use the full 120-document corpus against a real model, same as the
rule-based row, with zero hard failures across any of them.

| Run | Documents | Field acc. (normalized) | Required fields | Critical fields | Doc success | Review rate | Cost/doc | Mean latency |
|---|---|---|---|---|---|---|---|---|
| baseline (rules) | 120 | 56.0% | 52.2% | 90.1% | 5.8% | 81.7% | $0.0000 | 0.8 ms |
| fixture (not an LLM) | 120 | 56.0% | 52.2% | 90.1% | 5.8% | 81.7% | $0.0000 | 4.1 ms |
| **openai/gpt-4.1-mini (real LLM)** | **120** | **80.0%** | **100.0%** | **79.6%** | **100.0%** | **17.5%** | **$0.0018** | **8,227 ms** |
| openai/gpt-5.6-luna (real LLM) | 120 | 80.8% | 100.0% | 77.1% | 100.0% | 17.5% | $0.0010 | 6,795 ms |
| openai/gpt-5.6-luna (scanned) | 120 | 79.3% | 99.0% | 79.6% | 93.3% | 81.7% | $0.0010 | 6,959 ms |
| google/gemini-3.6-flash (real LLM, quota-limited, predates the fix below) | 20 (11 completed) | 83.5% | 100.0% | 75.4% | 100.0% | 45.5% | $0.0049 | 36,537 ms |

The two rule-based rows land on identical accuracy because the fixture
provider *is* the rule-based baseline, called through the `LLMProvider`
interface instead of directly (see [AI.md](AI.md#provider-abstraction)) — not
a coincidence to explain away. Their review rate now matches too (81.7%
each) — it didn't always; see the confidence-calibration section for why a
real bug used to make the fixture row's review rate higher for a reason that
had nothing to do with real risk.

**The OpenAI row is the number to trust for "does this actually work."** Full
corpus, zero hard failures, real cost tracking (not the false $0.0000 a model
missing from the pricing table would silently report — verified against
`llm/pricing.py` before trusting this number, see below). Required-field
accuracy and document success rate both hit 100% — every one of the 120
documents had every field a human actually needs correct. The gap to 100%
field accuracy (80.0%) is concentrated in a specific, explicable place: see
"fields with the most errors" below, not spread evenly across everything.
**Review rate is the number worth pausing on: 17.5%, not the ~51% an earlier
same-day run showed** — see the confidence-calibration section for why
that first number was itself measuring a bug, not real risk, and why fixing
it changed the *product's actual behavior* (a third fewer documents sent to
human review), not just a report footnote.

This is a standard, unmodified run — same corpus, same prompts, same command
as every other row in this table, no special-casing for this provider or this
model. The "upper bound, not a prediction" caveat in Methodology applies to
it exactly as much as to the rows above it.

**gpt-5.6-luna vs. gpt-4.1-mini, same corpus, same day.** OpenAI's July 2026
GPT-5.6 family is not a strict upgrade over gpt-4.1-mini on this corpus, and
the numbers say so plainly rather than needing to be argued. Overall field
accuracy is a statistical tie (80.8% vs. 80.0% — well inside normal noise at
120 documents); required-field accuracy and document success rate are
identical (100.0%/100.0%); critical-field accuracy is 2.5 points *lower* for
Luna (77.1% vs. 79.6%) — small enough to plausibly be noise at this sample
size (Luna's own clean-run critical-field number moved from 77.8% to 77.1%
between two otherwise-identical same-day runs — see the temperature note
below for why — so a 2.5-point gap against a different model is not
obviously more than that same noise floor), but a real measured number, not
the improvement a newer, cheaper model gets assumed to deliver by default.
What *is* unambiguously better: cost (44% lower — $0.0010/doc vs.
$0.0018/doc) and latency (17% lower — mean 6,795 ms vs. 8,227 ms). Review
rate lands on the identical 17.5% for both, a coincidence worth naming
rather than reading a story into. The two models also fail on the same
fields for what is very likely the same reason — see "Where the models
actually fail" below. **The honest read: gpt-5.6-luna is a cost/latency win
here, not an accuracy win.** Whether that trade is worth making for a given
deployment is a product decision this document surfaces the numbers for,
not one it makes for you.

**Why two "clean" gpt-5.6-luna runs don't match exactly.** gpt-5.6-luna
rejects any non-default `temperature` outright (see
[openai_provider.py](../backend/src/docflow/llm/openai_provider.py) —
confirmed against the real API, not assumed), so unlike gpt-4.1-mini it
cannot be pinned to `temperature=0` for reproducibility. Every number
attributed to it above is real and measured, but expect low-single-point
drift between independent runs of the identical corpus/prompt/code as a
property of the model, not of this harness.

### Does OCR actually hurt accuracy? A real scan simulation

Every row above this point, including every real-LLM row, hands the
extractor **clean synthetic text** — `eval/runner.py` says so in its own
docstring ("documents are already text"), and it was true at every corpus
size ever run. `docs/AI.md`'s OCR routing and `documents/text_extraction.py`
have existed since early in this project, and until this run **the eval
harness had never once exercised that code path.** No number in this
document, before this section, says anything about what happens to accuracy
when a document actually needs OCR.

**Methodology** (`backend/src/docflow/eval/scan_simulation.py`, `--scan`).
For each of the 120 documents: render its ground-truth text to a PDF
(reportlab, monospace font, preserving the generators' column alignment) →
rasterise at 150 DPI (below the pipeline's own 300 DPI default, deliberately
worse than a good scan) → degrade with a small random rotation, Gaussian
noise, blur, and reduced contrast (seeded per document id via SHA-256, not
Python's `hash()` — the latter is randomised per process and would silently
break reproducibility between runs) → run the degraded **image** through the
real `TextExtractor`, the same code a real scanned upload hits. The
resulting OCR'd text — not the clean original — is what gpt-5.6-luna
actually saw. Ground-truth fields are unchanged, because the underlying
document didn't change; only what OCR could recover from it did.

| | Clean text | Real scan simulation | Delta |
|---|---|---|---|
| Field accuracy | 80.8% | 79.3% | −1.5 pt |
| Required-field accuracy | 100.0% | 99.0% | −1.0 pt |
| Document success rate | 100.0% | 93.3% | **−6.7 pt** |
| Validation failure rate | 17.5% | 24.2% | +6.7 pt |
| **Review rate** | **17.5%** | **81.7%** | **+64.2 pt** |
| Recall | 88.7% | 86.2% | −2.5 pt |
| Precision | 82.4% | 81.3% | −1.1 pt |

**The headline isn't the accuracy drop — it's the review-rate jump.** Field
accuracy barely moved (−1.5 points); review rate moved by 64 points, from
"most documents go straight through" to "four in five need a human." That
gap is the confidence-scoring machinery working as designed, not a
contradiction to explain away: `pipeline/stages/extract.py`'s real
`context_signal = 0.65 if used_ocr else 0.95` now actually fires in this
harness for the first time (`eval/runner.py::_score` previously hardcoded
`context=0.95` unconditionally — every eval run before this one was
implicitly scoring as if OCR never happens), and grounding
(weight 0.40, the largest single signal) checks whether the extracted value
appears in the **OCR'd** source text, not the original clean text. A model
that correctly infers `13` from context when OCR actually produced `E3` gets
the field right — good for accuracy — but scores low on grounding, because
`13` genuinely does not appear anywhere in what OCR gave it — appropriately
flagging a document a human should glance at, even though the final answer
was correct. That is arguably the *correct* product behavior for a
real deployment (see `AI.md`'s "central bet": nobody should trust an LLM
extraction unsupervised), not a flaw in either the scorer or the model.

Doc success (−6.7 pt) and validation failure rate (+6.7 pt) landing on the
identical figure is not a coincidence to chase — a required field going
wrong is exactly the kind of thing the validation layer (e.g. a
subtotal/total mismatch) tends to catch too, so the same underlying
documents plausibly drive both numbers.

**Confidence calibration, scanned run — one honest wrinkle, not chased
further.** The scanned run's medium confidence band is *more* accurate
(89.3%, n=205) than its high band (81.0%, n=2,706) — high should be ≥
medium in a well-calibrated score, and here it isn't. This is a smaller,
single-band version of the shape that turned out to be a real bug twice
before in this document (nbsp thousands-separators, ungrounded currency
codes), so it is named here rather than smoothed over. It has **not** been
investigated to a root cause the way those two were — the saved report
keeps aggregate band statistics, not the per-field detail that would be
needed to tell "a third real calibration bug" apart from "205 fields is a
modest bucket and OCR-era noise is a plausible enough explanation on its
own." Flagged for whoever looks at this next, same as the Gemini
hard-failure cause below.

**What this does and does not prove.** Same caveat as the rest of this
document, one layer deeper: this measures accuracy under a *simulated* scan
of *synthetic* text, not a real scan of a real document — no rendering
pipeline reproduces a genuine flatbed scanner, phone camera, or fax the way
an actual bad scan does, and the simulated degradation parameters (150 DPI,
±2° rotation, light noise/blur) are a reasonable guess, not a measured
match to any real scanner's output. What it *does* support, same as
everywhere else in this document: relative comparison — clean vs. scanned,
same model, same corpus, same code — which is exactly why the −1.5-point
accuracy delta and the +64-point review-rate delta are both trustworthy
numbers even though "79.3% is accurate on real scans" would not be.

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

OpenAI (gpt-4.1-mini, 120 docs): precision 81.7%, recall 88.4%, F1 84.9%
(2,436 true positives, 547 false positives, 320 false negatives) — recall far
above the rule-based baseline (it attempts nearly everything), at some
precision cost (it also gets some of those attempts wrong) — the normal
shape of an LLM-vs-regex trade, and exactly why confidence scoring and human
review exist downstream rather than trusting either number alone.

OpenAI (gpt-5.6-luna, 120 docs): precision 82.4%, recall 88.7%, F1 85.4%
(2,444 true positives, 521 false positives, 312 false negatives) — the same
shape as gpt-4.1-mini on the same corpus, marginally higher on both precision
and recall rather than trading one for the other.

Gemini (20 docs): precision 86.2%, recall 88.1%, F1 87.1% (237 true
positives, 38 false positives, 32 false negatives) — consistent with the
OpenAI rows on the same corpus, different provider.

### Where the models actually fail

The rule-based baseline fails completely (0% accuracy) on `line_items.*` (all
sub-fields), `customer`/`customer.name`/`customer.registration_id`, and
`tax_rate` — a regex/keyword baseline has no mechanism for table extraction
or disambiguating "the second party mentioned" as the customer rather than
the supplier. Expected shape of a rule-based floor, not a bug — it's
*supposed* to be beatable.

The real model's failures are a different, more specific shape — not spread
across whole field categories, concentrated in a few fields (120-doc OpenAI
run):

| Field | Accuracy | Occurrences |
|---|---|---|
| `supplier.address` | 0.0% | 95 |
| `customer.address` | 0.0% | 75 |
| `line_items.0.tax_rate` | 6.7% | 75 |
| `line_items.0.unit` | 14.7% | 95 |
| `bank_details.account_number` | 20.0% | 75 |
| `bank_details.iban` | 20.0% | 75 |
| `purchase_order_number` | 52.0% | 75 |

Multi-line addresses fail completely — 0% on both `supplier.address` and
`customer.address`, not "mostly right." Worth a real look before calling this
production-ready for a workflow that needs addresses; not investigated
further in this pass. Everything else in that list is a narrower, more
believable LLM failure mode: per-line-item tax rates and units (easy to
transpose across rows in a multi-line-item document), and bank identifiers
(long alphanumeric strings genuinely hard to transcribe exactly). None of
this is spread evenly across the schema — the fields a business actually
keys off (invoice number, dates, total) are the required fields that hit
100%; the fields still weak are specifically the ones this table names.

gpt-5.6-luna's failure list (120 docs) overlaps almost entirely with
gpt-4.1-mini's, which is the more informative result than either list on its
own: both models land on 0% for the exact same two fields, almost certainly
for the exact same reason.

| Field | Accuracy | Occurrences |
|---|---|---|
| `supplier.address` | 0.0% | 95 |
| `customer.address` | 0.0% | 75 |
| `line_items.0.unit` | 21.1% | 95 |
| `bank_details.account_number` | 20.0% | 75 |
| `bank_details.iban` | 20.0% | 75 |
| `line_items.1.unit` | 22.5% | 71 |
| `line_items.2.unit` | 25.0% | 40 |
| `line_items.0.tax_rate` | 37.3% | 75 |
| `line_items.1.tax_rate` | 50.9% | 55 |
| `purchase_order_number` | 52.0% | 75 |
| `amount_due` | 68.0% | 75 |
| `payment_terms` | 75.8% | 99 |

`purchase_order_number` lands on the identical 52.0% for both models — not a
story, just two models converging on the same ambiguous subset of the
corpus. `payment_terms` and `amount_due` show up as secondary weak spots for
Luna without being notable for gpt-4.1-mini — a smaller, model-specific
pattern layered on top of the dominant one. The dominant one — addresses and
bank identifiers failing on both models — is a property of this extraction
approach on this corpus, not of either specific model's release.

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

OpenAI (gpt-4.1-mini, 120 docs — full corpus, not a slice):

| Hazard | Accuracy |
|---|---|
| label_variants | 77.9% |
| extra_numbers | 78.4% |
| diacritics_stripped | 78.6% |
| ambiguous_date | 79.3% |
| no_currency_code | 79.5% |
| (none) | 79.5% |
| decimal_comma | 81.2% |

Tight range (77.9%–81.2%) across every injected hazard — no single difficulty
type disproportionately breaks it, and the "no injected difficulty" baseline
(79.5%) isn't higher than most of the hazard rows, meaning the accuracy gap
documented above (addresses, per-line tax rates) isn't explained by these
hazards either — it's a distinct, separate weakness.

OpenAI (gpt-5.6-luna, 120 docs — full corpus, not a slice):

| Hazard | Accuracy |
|---|---|
| ambiguous_date | 77.3% |
| label_variants | 77.5% |
| extra_numbers | 78.1% |
| diacritics_stripped | 78.5% |
| no_currency_code | 79.8% |
| (none) | 81.8% |
| decimal_comma | 83.2% |

Same shape as gpt-4.1-mini's row directly above: same worst hazards
(`ambiguous_date`/`label_variants`), same best (`decimal_comma`), a
marginally wider range (77.3%–83.2% vs. 77.9%–81.2%). The address/bank-
identifier gap is, again, not explained by any of these hazards — it shows
up in the "no injected difficulty" column too (81.8%).

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

**Production-band view** (fixture, 120 docs, after both fixes below):

| Band | Fields | Actual accuracy | Mean score |
|---|---|---|---|
| high | 1,165 | 95.2% | 0.972 |
| medium | 120 | 100.0% | 0.816 |

No `low` band at all anymore — every low-band field in this corpus was a
`currency` field wrongly scored (see the second bug, immediately below); once
that stopped happening, nothing else in this corpus scores low enough to
land there.

### Second bug this tool found: every `currency` field scored low, regardless of correctness

The nbsp fix above was found against the fixture provider. This one wasn't
found until a full, unlimited real-LLM run existed (120 documents, OpenAI,
see Results) — the first calibration sample with enough real-model volume to
show something with confidence rather than noise:

| Band (before this fix) | Fields | Actual accuracy | Mean score |
|---|---|---|---|
| high | 2,922 | 81.6% | 0.985 |
| low | 62 | 98.4% | 0.544 |

The *low* band was *more* accurate (98.4%) than the *high* band (81.6%) — the
opposite of what a working score should show, and not small-sample noise (62
fields is a small band, but 81.6% vs. 98.4% on the *high* band's 2,922 fields
is not). Investigating instead of shrugging (same approach as the nbsp bug)
found every field in the low band was of kind `currency`. Root cause: the
corpus (like real Czech-market documents) mixes currency notation — some
documents spell it out (`...64 700,00 CZK`), others use the local symbol
(`...72 300,00 Kč`) — and the model correctly normalises either to an ISO
4217 code (`CZK`). But `currency` fields defaulted to `groundable=True`, so
grounding checked whether `CZK` appears *verbatim* in the source text — which
it doesn't, for every document using the symbol form. A **correct** value
scored as ungrounded, dragging every currency field's confidence down to
~0.544 (hand-computed from the weighted formula before trusting the
diagnosis: `(0.10×0.40 + 1.0×0.15 + 1.0×0.15 + 0.95×0.10) / 0.80 = 0.544` —
an exact match to the observed mean score).

**This wasn't just a calibration curiosity — it was quietly changing the
product's actual behavior.** `currency` is a `required` field on three of the
four document types, and document-level confidence is floored at the minimum
confidence of any required field (`domain/confidence.py::aggregate`, by
design — see its own docstring). Every document with a required currency
field had its overall confidence dragged down by this bug, regardless of how
correct everything else was. Measured effect: the OpenAI run's review rate
dropped from **50.8% to 17.5%** after the fix — roughly a third of all
documents in a from-scratch run were being sent to human review for a reason
that had nothing to do with actual risk.

**Fix.** `groundable=False` on the `currency` field across all four document
schemas (`schemas/types/{invoice,receipt,purchase_order,contract}.py`) — the
same shape of fix already applied to `notes`/enum/boolean fields, just missed
for this one kind. Correctness for a currency field is "is it a valid ISO
code," which the existing format signal already checks; grounding was simply
the wrong check to apply to it. Regression tests:
`test_normalised_currency_code_does_not_match_a_local_symbol` and
`test_currency_fields_are_not_graded_on_grounding` (parametrized across all
four document types) in `tests/unit/test_confidence_and_security.py` —
confirmed the second one fails without the fix before trusting it, same
discipline as every other regression test in this project.

**Result, before and after** (OpenAI, gpt-4.1-mini, full 120-doc corpus):

| | Before | After |
|---|---|---|
| Confidence bands | high 81.6% (n=2,922), low 98.4% (n=62) — inverted | high 81.7% (n=2,983) — clean, no low band |
| Review rate | 50.8% | 17.5% |
| Field/required/critical/doc-success accuracy | unchanged (80.4/100/79.5/100%) | unchanged (80.0/100/79.6/100%) — confirms this was a confidence bug, not an extraction bug |

Gemini, 20 docs (predates this fix, too small to have shown the pattern
clearly on its own — shown for historical comparison only):

| Band | Fields | Actual accuracy | Mean score |
|---|---|---|---|
| high | 270 | 85.9% | 0.985 |
| low | 5 | 100.0% | 0.544 |

**Known limitation, stated plainly.** Both bugs found by this tool share a
structural shape: a signal (grounding) scoring a *correct* value as
suspicious because of a representation mismatch between what the model
correctly produced and what literally appears in the source — truncated
digits for the nbsp bug, a normalised ISO code for this one. Both concrete
instances are fixed; the *class* of blind spot (grounding assumes the
correct value always appears verbatim, which is false for anything the
pipeline is expected to normalise) is not eliminated. Any other normalised
field kind added in the future should be checked against this before
assuming `groundable=True` is safe.

**A second real-LLM data point, post-fix.** The gpt-5.6-luna run (see
Results) is a later, independent check that both fixes above hold outside
gpt-4.1-mini: its low band (32 fields, mean score 0.544 — the same number the
currency bug used to produce, since the weighted-average formula is
unchanged) is 0.0% actually accurate, i.e. genuinely wrong values scoring
low, not the inverted pattern either bug produced. Expected, since both fixes
live in the document-type schemas and the baseline extractor rather than in
any provider-specific code — but which *fields* landed in that low band
isn't determined here; the saved report keeps aggregate band statistics, not
a per-field breakdown, the same limitation noted for the Gemini hard-failure
investigation above.

## What's measured vs. not — summary

| Claim | Status |
|---|---|
| Pipeline runs end-to-end (upload → classify → extract → validate → score → route) | **Measured** — 263 automated tests, plus manual full-stack verification through `docker compose` and the live deployment |
| Rule-based baseline accuracy on synthetic corpus | **Measured** — this document, 120 docs |
| Confidence-scoring machinery functions correctly | **Measured** — found and fixed a real bug (above), which is stronger evidence than a clean run would have been |
| Classification accuracy (heuristic cascade) | **Measured** — 100% on this corpus (see caveat: synthetic documents use the same vocabulary the heuristic was written against) |
| Real LLM extraction accuracy | **Measured, full corpus** — 120 documents, openai/gpt-4.1-mini, 80.0% field accuracy, 100% required-field accuracy, 100% doc success, zero hard failures. A second OpenAI model (gpt-5.6-luna, same corpus) lands within noise on accuracy (80.8%) but is cheaper and faster — see below. A third provider (Gemini) corroborates on a smaller, quota-limited slice |
| Real LLM cost per document | **Measured** — $0.0018/doc (gpt-4.1-mini, 120-doc run); $0.0010/doc (gpt-5.6-luna, 120-doc run — 44% cheaper); $0.0049/doc (Gemini, 20-doc run) |
| Real LLM latency | **Measured** — mean 8.2s/doc (gpt-4.1-mini); mean 6.9s/doc (gpt-5.6-luna — 16% faster); mean 36.5s/doc (Gemini). None have been investigated or optimized |
| Real LLM confidence calibration | **Measured, full corpus** — 2,983 fields (gpt-4.1-mini), large enough to draw a real conclusion from, and it did: found and fixed a second real bug (every `currency` field scoring low regardless of correctness), which is why the OpenAI review rate above went from a first-pass 50.8% to a final 17.5% — a real, verified product improvement, not a report footnote |
| Accuracy on real (non-synthetic) documents | **Not measured** — no ground-truth corpus of real documents exists for this project |
| OCR accuracy on scanned/low-DPI documents | **Measured, full corpus, simulated scans** — 120 documents rendered, degraded, and OCR'd for real (`--scan`; see "Does OCR actually hurt accuracy?" above): field accuracy −1.5 pt (80.8%→79.3%), doc success −6.7 pt (100%→93.3%), review rate **+64.2 pt** (17.5%→81.7%). **Caveat that still applies:** simulated degradation of synthetic text, not a real scanner on a real document — see the section above for exactly what that does and doesn't support |
| Czech OCR language configuration | **Measured** — `ocr_language` defaulted to English-only despite the Docker image installing the Czech pack for exactly this market; real Tesseract runs against real Czech text (ICDAR2019 academic ground truth + a written invoice paragraph) showed 7.9-10.3% character error rate with `eng` alone vs. 0.0-0.7% with `eng+ces`. Fixed; regression test in `tests/unit/test_ocr_language.py` |
| Why the Gemini run's hard failures happened | **Not conclusively determined** — rate limiting is the strong suspect given the code path and the free-tier key used, but per-document error codes aren't persisted in the report, so this is an informed inference, not a re-derivable fact |

If you're evaluating this project and want to reproduce or extend this: any
of Anthropic, OpenAI, or Google will work — run the command at the top of
this document, optionally with `--size` to control cost. Nothing else
changes — the corpus, the metrics, and the report format are identical
regardless of which extractor is under test.
