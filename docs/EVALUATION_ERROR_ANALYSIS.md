# Evaluation — Error Analysis

Root-cause investigation of the persistently weak fields named in every real-LLM
run recorded in [EVALUATION.md](EVALUATION.md) (`supplier.address`,
`customer.address`, `bank_details.*`, `line_items[].tax_rate`,
`line_items[].unit`, `purchase_order_number`). Every claim below is checked
against the actual schema code and the actual generated corpus
(`backend/eval_data/corpus.jsonl`), not inferred from the field name. Where a
root cause could not be fully verified, that's stated rather than filled in
with a plausible-sounding guess.

## Error taxonomy used below

1. **OCR error** — the value was misread from a degraded scan, not mis-extracted from clean text
2. **Extraction error** — the LLM had correct, unambiguous source text and still produced the wrong value
3. **Normalization error** — the extracted value is substantively correct but fails the comparison function's matching rule
4. **Schema error** — the schema asks for something the source material can't actually support
5. **Validation error** — a downstream business-rule check, not extraction, is what's wrong
6. **Ground-truth issue** — the label the extractor is being graded against is itself incomplete or wrong
7. **Ambiguity** — the source document genuinely supports more than one defensible answer

## Finding 1 — `supplier.address` / `customer.address`: ground-truth issue, not an extraction failure

**What happened.** Every real-LLM run to date reports 0.0% accuracy on both
fields (95 and 75 occurrences respectively — see EVALUATION.md's worst-fields
tables). This has been described, repeatedly, across `PROJECT_STATUS.md`,
`FINAL_REPORT.md`, and `PRODUCTION_READINESS.md`, as one of this project's
biggest known weaknesses — "worth a real look before calling this
production-ready for a workflow that needs addresses."

**Why it happened — verified, not inferred.** The corpus generator
(`eval/dataset.py::generate_invoice`) writes the company address directly into
the rendered document text on the line immediately after the company name:

```
Northgate Supplies Ltd
42 King Street, Manchester M2 6DN
IČO: 08123456    DIČ: GB812345678
```

But the same generator's `fields` ground-truth dict for `supplier` only ever
contains `{"name": ..., "registration_id": ..., "vat_id": ...}` —
**no `address` key, on any of the 120 documents.** Confirmed directly against
the corpus file, not the generator's source:

```python
>>> inv['fields']['supplier'].keys()
dict_keys(['name', 'registration_id', 'vat_id'])
```

The same gap exists for `purchase_order.supplier` (which reuses the same
`Party` Pydantic model, and the same corpus generator pattern — "Dodavatel:
Delta Systems s.r.o." followed immediately by "Veveří 456/9, 602 00 Brno" in
the text, with no `address` key in ground truth). This is exactly why the
worst-fields occurrence count for `supplier.address` is **95, not 75**: 75
invoices + 20 purchase orders, both missing the same key.

**Mechanism.** `eval/metrics.py::build_field_outcomes` iterates the *union* of
expected and actual leaf paths specifically so a fabricated field doesn't go
unscored (see EVALUATION_PROTOCOL.md). That's the right general design — but
it means that when the LLM correctly reads an address that is plainly present
in the source text, the comparison sees "expected: absent, actual: present"
and scores it a miss. Confirmed exactly by the missing-vs-false breakdown
(`field_accuracy_table`, added specifically for this investigation):
`supplier.address` was **0 missing, 95 false, 0 correct** — the model
produced a value on every single document, none of which could possibly
match an expectation that was never populated. **The system was provably
marking a 100%-attempt-rate field wrong on every attempt, by construction.**

**Is it systematic?** Yes — 100% of occurrences, both fields, both affected
document types, both real-LLM runs to date (gpt-4.1-mini and gpt-5.6-luna).
Not a sampling artifact.

**Confidence this is really the cause:** high, with one honest gap remaining
— this analysis confirms the *ground truth is missing the key*, confirms the
*source text contains a real address*, and confirms the model *always
produces a non-null value* (0 missing) — it does not independently verify
that all 95 produced values are themselves *correct* transcriptions of that
address (the harness records match/no-match against ground truth, not the
predicted value's own text, and ground truth had nothing to match against
here by definition). The Phase 18/19 before/after measurement below is the
actual test of that remaining assumption: if fixing ground truth makes the
field's accuracy jump close to this system's other free-text fields', that's
real evidence the extracted values were right all along; if it doesn't, the
original assumption was wrong and that gets reported too.

**Possible fix.** Add `address` to the `fields` dict in `generate_invoice`
(both `supplier` and `customer`) and `generate_purchase_order` (`supplier`
and `buyer` — checked directly: both use the same `Party` schema and the
same text-without-ground-truth pattern), using the same address string
already written into `text`. Small, mechanical, low-risk — this is a
corpus-generator fix, not a product-code fix.

**Has the fix been implemented?** Yes — see Phase 18/19 in EVALUATION.md for
the before/after measurement. This was judged high-value specifically because
it was cheap to fix and, if the hypothesis is right, corrects a
misleading headline number that's been repeated across four documents.

## Finding 2 — `line_items[].tax_rate`: a schema field the source documents never populate

**What happened.** `line_items.0.tax_rate` measured 6.7–37.3% accuracy across
runs (the range itself is a flag — a stable, well-defined field shouldn't
swing that much run to run).

**Why it happened.** `LineItem.tax_rate` is a real, declared schema field
(`schemas/types/invoice.py`, a `Money | None` on the line-item model,
independent of the document-level `tax_rate`). But the corpus generator's
per-line-item text rendering only ever prints description, quantity, unit
price, and line total — **no per-line tax rate ever appears in the source
text**, and the ground-truth `line_items[]` dicts correspondingly never
include a `tax_rate` key (verified: `{'description', 'quantity', 'unit',
'unit_price', 'line_total'}`, no `tax_rate`, on every line item checked).

This is a different shape of problem than Finding 1. For addresses, the
correct value is unambiguously present in the source and ground truth is
simply missing it. For per-line tax rate, **there is no correct value to
extract from most of these documents** — only a single document-level rate
is ever stated. A model that returns `null` for every line item's tax rate
would be behaving defensibly; a model that infers "the document says 21% VAT,
so each line probably carries 21%" is making a plausible but unstated
assumption — and either way, ground truth has nothing to check the answer
against, so a non-null prediction registers as a miss regardless of whether
the inference was reasonable.

**Classification:** schema error (asking for a value the source material
doesn't reliably support), compounded by a ground-truth issue (the
corpus doesn't even define what the "right" answer would be if the model
does infer one).

**Is it systematic?** Yes, in the sense that no document in this corpus ever
gives per-line tax rate data — but the wide accuracy range across runs
suggests the model's behavior here (guess vs. leave null) isn't stable, which
is itself informative.

**Possible fix — a genuine design decision, not a mechanical one.** Either
(a) instruct the model explicitly (prompt-level) to leave line-item tax rate
null unless a per-line rate is actually shown, and confirm ground truth
agrees (null across the board, matching current generator behavior), or (b)
decide that inferring the document-level rate per line is the desired
product behavior, and update both the corpus generator and ground truth to
reflect that. This wasn't fixed in this pass — it needs a product decision
about what "correct" means here, not just a data-generation fix, and Phase 18
prioritizes fixes with a clear right answer over one that would require
guessing at product intent.

## Finding 3 — `bank_details.iban` / `bank_details.account_number`: a recall problem, not a transcription problem

**What happened.** 20% accuracy on both fields, both real models
(gpt-4.1-mini and gpt-5.6-luna, independently measured).

**Original hypothesis (wrong — corrected here with real evidence).** The
first pass at this document assumed "long alphanumeric strings are genuinely
hard to transcribe exactly" — a reasonable-sounding guess, but a guess. Once
the eval harness was extended to record *missing* vs. *false* predictions
per field, not just a pass/fail rate (`eval/metrics.py::field_accuracy_table`,
added specifically because this document's first draft kept running into
exactly this kind of unverifiable claim), the real breakdown told a different
story:

| Model | Field | Correct | Missing | False |
|---|---|---|---|---|
| gpt-4.1-mini | `bank_details.iban` | 15/75 | 60/75 | 0/75 |
| gpt-4.1-mini | `bank_details.account_number` | 15/75 | 60/75 | 0/75 |
| gpt-5.6-luna | `bank_details.iban` | 15/75 | 60/75 | 0/75 |
| gpt-5.6-luna | `bank_details.account_number` | 15/75 | 60/75 | 0/75 |

**Zero false predictions, on both fields, both models.** Every time either
model actually produced a value, it was correct. The 20% accuracy is not "the
model got it wrong 80% of the time" — it's "the model *declined to answer*
80% of the time, and was never once wrong when it did answer." That is a
precision-vs-recall story, not a transcription-accuracy story, and it's the
opposite of what "genuinely hard to transcribe" would predict (which would
show wrong-but-attempted values, not blanks).

**Why it happened — still not fully determined, but narrowed.** Ground truth
is correctly populated (verified: real IBAN/account values on every
invoice), and the values are present in the source text
(`Bankovní spojení: 19-2000145399/0800` / `IBAN: CZ...`). Two models
independently converging on the identical 80% non-response rate suggests
something about the document layout or field framing makes the model
under-confident about this specific field, not a per-model quirk — but this
document does not have a confirmed mechanism, only a ruled-out one (it is not
a transcription-error problem).

**Classification:** revised from "extraction error" to **ambiguity /
extraction error (recall-side)** — the model is behaving conservatively
rather than incorrectly, which is a different engineering problem (why is it
under-confident here specifically?) than the one originally assumed (why does
it mistype long strings?).

**Systematic:** yes — identical shape (0 false, ~80% missing) across both
independently-measured models.

**Possible fix, not attempted in this pass:** since the failure mode is "the
model won't commit," a fix would target confidence/willingness (e.g.
prompt guidance explicitly permitting a best-effort IBAN even under
uncertainty, since downstream validation already checksum-checks IBANs per
`docs/DATABASE.md`), not transcription accuracy. Not implemented here because
it's a prompt-behavior change that needs its own measurement, not a
mechanical data fix like Findings 1 and 2.

## Finding 4 — `purchase_order_number`: RESOLVED — a ground-truth issue, not a model weakness

**Status: resolved 2026-08-19.** Originally left "unresolved" below (kept
verbatim for the audit trail) — a follow-up investigation, requested and
scoped specifically to close this gap, found the root cause, verified it with
live per-document data, and fixed it conservatively. Jump to "Resolution" for
the current state; the original write-up follows first, unedited, because it
records real work and a real (if incomplete) finding.

### Original write-up (2026-08-18) — kept for the record

**What happened.** A stable 52.0% across every gpt-4.1-mini and gpt-5.6-luna
run to date — suspiciously exact-round and identical between two different
models.

**What was checked.** Ground truth is correctly populated, and the exact
ground-truth string **is present verbatim in the source text**
(`"Číslo objednávky: OBJ-2024-0001"` — confirmed `po_number in text` is
`True`). The missing-vs-false breakdown (same instrumentation as Finding 3)
narrows this further:

| Model | Correct | Missing | False |
|---|---|---|---|
| gpt-4.1-mini | 39/75 | 0/75 | 36/75 |
| gpt-5.6-luna | 39/75 | 0/75 | 36/75 |

**Zero missing predictions — both models attempt this field on every single
document, and both are wrong on the identical 36.** This rules out "the model
can't find it" entirely (that would show as missing, not false) and rules out
random per-call noise (two different models landing on the exact same 36/75
would be an extraordinary coincidence otherwise). What remains is: **some
identifiable subset of ~36 documents has something about them — a label
variant, a second number, an ambiguity — that reliably makes an LLM pick a
plausible-but-wrong value instead of the correct one**, consistently, across
models.

**What this analysis still could not determine.** Which ~36 documents, and
what they have in common — that requires pulling the actual predicted value
per document, which the harness does not currently persist to the JSON
report (aggregate `field_accuracy_table` counts, not per-document values —
see "What this analysis could not check" below). Narrowed from "unexplained"
to "a specific, findable subset of documents, mechanism not yet identified"
— genuine progress, not a full answer, and reported as exactly that rather
than rounded up to a diagnosis.

**One error in the original write-up, corrected here rather than silently
fixed:** "Ground truth is correctly populated... confirmed `po_number in
text`" above is checking the wrong field. `po_number` is the identifying
field of the *`purchase_order`* document type (`PurchaseOrder.po_number`) —
genuinely well-populated and unrelated to this finding. The field actually at
52.0% in the worst-fields table is *`Invoice.purchase_order_number`* — a
different field, on a different document type, that the original check never
actually looked at. Ground truth for it was not correctly populated; it did
not exist at all. Left as originally written above rather than silently
edited, because the mix-up is itself informative about how this class of bug
hides — even a careful-sounding verification note can check the wrong thing
when two fields share most of a name.

### Resolution (2026-08-19)

**Root cause.** `generate_invoice` (`backend/src/docflow/eval/dataset.py`)
writes a labelled purchase-order reference into the document text — `"Č.
objednávky: OBJ-XXXX"` / `"PO: OBJ-XXXX"` — on exactly 36 of 75 invoices, as
part of its `extra_numbers` difficulty injection (a block whose own code
comment describes it as decoy "numbers that look like amounts"). But
`Invoice.purchase_order_number` is a real, declared schema field
(`schemas/types/invoice.py:213`, `FieldKind.IDENTIFIER`, schema description
"PO number", no disambiguating hint anywhere in the prompt or
`extraction_guidance`) — and ground truth never recorded a value for it, on
*any* of the 75 invoices, decoy-tagged or not. Structurally identical to
Finding 1: a real, extractable value the corpus generator itself writes into
the text, never recorded in the `fields` ground-truth dict.

**How it was verified.** A harness change made this possible for the first
time: `RunnerConfig(persist_predictions=True)` (`eval/runner.py`) and
`EvaluationReport.predictions()` (`eval/metrics.py`) record each document's
ground truth, raw model output, and parsed value, opt-in, off by default. A
live, deterministic (`temperature=0`) gpt-4.1-mini run against the *pre-fix*
corpus, joined against the corpus text directly, gave an exhaustive
cross-tabulation over all 75 invoices — not a sample:

| Check | Result |
|---|---|
| Invoices with the decoy PO line | 36 |
| Invoices where the model returned a non-null `purchase_order_number` | 36 |
| Overlap between the two sets, both directions | **36 — full** |
| Wrong predictions with no decoy line in the source | **0** |
| Decoy-tagged invoices the model returned null for anyway | **0** |
| Wrong predictions that exactly equal the decoy text (normalised) | **36 / 36 — 100%** |
| Invoices with only a decoy phone number (no PO line) that still fabricated a PO value | **0 / 39** |

**Multiple plausible numbers, checked rather than assumed.** Every one of the
36 documents contains a second decoy in the same block — a fabricated phone
number, printed immediately before the PO line — specifically so this could
be checked, not asserted. The model never once confused the two: 0/36 raw
predictions matched the phone number, and 0/39 phone-only (no PO line)
documents produced a spurious `purchase_order_number` value.

**Classification (of the 36).** 36/36 — ground-truth issue (taxonomy
category 6, above). Zero in every other category: no extraction error (every
predicted value matched its source line character-for-character), no
wrong-value selection (no case of the phone number or any other field's
value being picked instead), no parsing/normalization/validation
involvement (`purchase_order_number` appears in no rule in `invoice`'s
`rule_ids` — confirmed by grep, not assumed), no harness bug
(`build_field_outcomes`/`compare()` scored every case exactly as documented,
correctly, given the ground truth they were handed).

**Fix.** `generate_invoice` now captures the exact value it writes into the
decoy line (the same `rng.randint()` draw, not a value derived from any
model's prediction) and records it as `fields["purchase_order_number"]` —
only on the 36 documents where that line is actually written; the other 39
keep `None`. Corpus regenerated from the same seed (`20240613`); verified
field-by-field, document-by-document, that nothing else in the corpus
changed. Regression test
(`tests/unit/test_purchase_order_number_ground_truth.py`) re-derives the
invariant directly from `generate_invoice` across a 600-call seed/index
sweep, independent of whatever happens to be in the checked-in corpus.

**Measured effect, gpt-4.1-mini, full 120-document corpus:**
`purchase_order_number` accuracy 52.0%→**100.0%** (39/75→75/75, 0 false, 0
missing); overall field accuracy 88.96%→89.77% (+0.81 pt); required-,
critical-, document-success, and review-rate **unchanged** (neither
required nor critical). Full before/after, including how a −1-field
run-to-run noise contribution on 8 unrelated fields was distinguished from
the fix's own precisely-+36 effect: [EVALUATION.md, "Finding 4
resolved"](EVALUATION.md#finding-4-resolved-purchase_order_number--a-third-ground-truth-gap).
**gpt-5.6-luna was not re-measured** — no prior gpt-5.6-luna run persisted
per-document predictions, so there was nothing to re-score offline, and a
fresh API call was deliberately not made this pass.

**What this does not mean.** Not that gpt-4.1-mini "got better" at this
field — the model, prompt, and code are byte-for-byte unchanged between the
before and after runs. Only what its output was graded against changed.

## Finding 5 — the same bug class, found again, once Findings 1–2 stopped hiding it

After fixing Findings 1 and 2 and re-running (see Phase 18/19 in EVALUATION.md),
`worst_fields()`'s top-12 list — previously dominated by `supplier.address`,
`customer.address`, and the two `line_items[].unit` entries — made room for
fields that were always weak but never visible in a top-12 view:
`supplier.country` (74.7% accuracy, 95 occurrences), and three fields at a
flat **0%**: `receipt.expense_category`, `receipt.purchase_time`, and
`purchase_order.delivery_address`.

Checked directly against the schemas: all four are real, declared fields
(`schemas/types/invoice.py`'s `Party.country`, `schemas/types/receipt.py`'s
`purchase_time`/`expense_category`, `schemas/types/purchase_order.py`'s
`delivery_address`) that the corpus generator never populates in ground
truth — the same bug class as Finding 1, not a new phenomenon. **Not the
same fix, though, and not applied in this pass:** `expense_category`
specifically carries prompt guidance calling it "your inference, not a
quotation from the receipt" — meaning a 0% ground truth isn't obviously a
generator oversight the way `address` was (there may be no single correct
inferred category for a synthetic receipt without real design work on what
"correct" means here), while `country`/`delivery_address`/`purchase_time`
look, on a first read, more like straightforward misses in the same shape as
Finding 1. Distinguishing "simple oversight" from "needs a design decision"
for each of these four, individually, was not done in this pass — flagged as
the clear next follow-up rather than fixed under time pressure with an
unverified guess at which category each belongs to.

## What this analysis could and could not check

Originally, the saved JSON report only had aggregate accuracy per field, with
no missing-vs-false breakdown — not enough to distinguish "the model won't
answer" from "the model answers wrong," which is exactly the distinction that
overturned the original Finding 3 hypothesis. `eval/metrics.py::field_accuracy_table`
was added specifically to close that gap (every field, not just the worst 12;
correct/missing/false counts, not just a pass rate) — every table in this
document is real output from that method, not a hand-derived estimate.

**What was still not available as of the original pass, and is now — this is
the follow-up the previous paragraph flagged, closed:** `field_accuracy_table`
gave aggregate counts per field path, not *which specific documents* landed
in the "false" bucket. That gap is exactly what kept Finding 4 at "a findable
subset exists" rather than "here is the subset and what they share."
`RunnerConfig(persist_predictions=True)` / `docflow-eval
--persist-predictions` (`eval/runner.py`, `eval/metrics.py`, added
2026-08-19) is the debug flag this section said would eventually be needed:
opt-in, off by default (measured to grow a single-run report ~17x, so it
stays off for routine runs), it persists ground truth, raw model output, and
parsed value per document. It is what let Finding 4 go from "a findable
subset exists" to "here is the subset, and here is exactly what each of the
36 documents contains and what the model returned for it" — see Finding 4's
"Resolution" section, above. Available now for any future field-level
investigation of this shape, not a one-off.
