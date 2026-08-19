# Evaluation Dataset — Quality Report

A direct inspection of the actual generated corpus file
(`backend/eval_data/corpus.jsonl`, seed `20240613`). Every number below comes
from parsing that file; none is inferred from reading `dataset.py`. Ground
truth was **not** modified in the course of the original 2026-08-18 audit this
document records — issues found were flagged, not silently patched.

**Note (2026-08-19):** the corpus was regenerated once more since this
document was written, to fix `purchase_order_number` ground truth on invoices
(see [EVALUATION_ERROR_ANALYSIS.md, Finding
4](EVALUATION_ERROR_ANALYSIS.md#finding-4--purchase_order_number-resolved-a-ground-truth-issue-not-a-model-weakness)).
Verified field-by-field that the regeneration changed nothing this document
measured — same document count, type mix, language mix, and difficulty-tag
counts — so every number below still describes the current corpus, with one
exception flagged inline below (the "Caveat on 'present'" section, which this
fix falsified).

## Headline

| | |
|---|---|
| Total documents | **120** |
| Duplicate document IDs | **0** |
| Documents with empty `text` | **0** |
| Documents with empty `fields` | **0** |
| Documents missing an expected schema key in `fields` | **0, as of 2026-08-19** — see correction below; this was **not actually true** when first published |
| Real (non-synthetic) documents | **0** — the corpus is 100% generated |

## Document type distribution

| Type | Count | Share | Target mix (`DEFAULT_MIX`) |
|---|---|---|---|
| invoice | 75 | 62.5% | 55% |
| receipt | 21 | 17.5% | 20% |
| purchase_order | 20 | 16.7% | 15% |
| contract | **4** | **3.3%** | 10% |

**Flag: contract sample size is too small to support any contract-specific
claim.** The generator sampling is weighted-random, not exact-quota (`dataset.py`
draws each of the 120 documents independently via `rng.choices(..., weights=...)`),
so at n=120 the realized mix drifts from the target — expected statistical
behavior, not a bug — but the drift landed hardest on the smallest category:
contract was targeted at ~12 documents and landed at 4. **Any per-field accuracy
number computed only over contracts (e.g. `auto_renewal`, `notice_period_days`)
is not a claim this corpus can support** — a single mis-extracted field on 4
documents swings the reported accuracy by 25 points. Field-level and difficulty
breakdowns in this project's reports are computed across all document types
together specifically to avoid resting a headline number on this thin a slice;
where a contract-only number is shown (Phase 4 of the evaluation work), it is
labeled with its n and read as indicative, not conclusive.

## Language

| Language | Count | Share |
|---|---|---|
| Czech (`cs`) | 93 | 77.5% |
| English (`en`) | 27 | 22.5% |

Matches the generators' design: invoices are Czech 70% of the time (English the
rest), purchase orders and receipts are always Czech, contracts are always
English. No document mixes Czech and English content within itself — language is
a per-document property, not a per-field one.

## Injected difficulty — measured vs. documented

`DIFFICULTY_FEATURES` in `dataset.py` documents **10** hazard kinds. The actual
corpus contains **6**:

| Hazard | Documents carrying it | Share |
|---|---|---|
| `decimal_comma` | 82 | 68.3% |
| `label_variants` | 48 | 40.0% |
| `no_currency_code` | 40 | 33.3% |
| `extra_numbers` | 36 | 30.0% |
| `diacritics_stripped` | 26 | 21.7% |
| `ambiguous_date` | 20 | 16.7% |

**Finding: four documented hazards are never actually generated.**
`multi_page`, `no_labels`, `rounding`, and `credit_note` all have entries in
`DIFFICULTY_FEATURES` (with descriptions) but **zero** `difficulty.append(...)`
call anywhere in any of the four `generate_*` functions ever adds them —
confirmed by grep, not inference (`grep -n '"rounding"\|"credit_note"\|"multi_page"\|"no_labels"' dataset.py` matches only the dict's own declaration lines, 42/44/46/47). This means:

- The corpus has never tested split-across-pages extraction, table-only/no-label
  extraction, rounded-total handling, or credit notes (negative amounts) —
  despite `docs/AI.md` and this project's own eval reports implicitly suggesting
  those are covered difficulty classes.
- Every "accuracy by injected difficulty" table in `EVALUATION.md` to date is
  correspondingly *complete* for the 6 hazards it reports, but silently *absent*
  four categories a reader could reasonably assume were included.

This is a real gap in the evaluation harness's coverage, not a data-quality defect
in the 120 documents that do exist. Documented here rather than quietly fixed by
generating four new hazard types under audit-task time pressure — inventing
plausible-looking "credit note" documents without deliberate design (what should
the extractor do with a negative total? does the schema even support it today?)
risks producing a hazard class that looks handled but was never actually
validated against real system behavior. Flagged as a concrete follow-up, not
fixed in this pass.

## OCR coverage

**Zero** documents in the base corpus (`corpus.jsonl`) carry the `ocr_scanned`
tag — the stored ground truth is 100% clean synthetic text. OCR coverage is
added, not stored: `eval/scan_simulation.py`'s `build_scanned_corpus()` takes
this same 120-document corpus at run time, renders each document to a degraded
image, and re-derives a *new* in-memory `GroundTruth` list with `ocr_scanned`
added to `difficulty` — see [EVALUATION.md](EVALUATION.md#does-ocr-actually-hurt-accuracy-a-real-scan-simulation).
There is no *partially*-OCR'd corpus variant (some documents clean, some
scanned, mixed at random) — a `--scan` run degrades all 120 uniformly. A mixed
corpus (only some documents needing OCR, matching a more realistic inbox) is a
capability the current harness does not have.

## Malformed / duplicate / missing-ground-truth samples

None found. Every document has: a unique `document_id`, non-empty `text`,
a `fields` dict with every key its document type's schema expects present (see
below for what "present" does and doesn't check), and a valid `document_type`
matching one of the four registered schemas.

**Caveat on "present":** this check confirms every expected *key* exists in
`fields` — it does not independently re-verify that every value is
non-degenerate (e.g., a non-empty string, a parseable date) for every optional
field on every document.

**Correction (2026-08-19) — the claim below this line was wrong when first
published, and the "0 missing keys" headline number above was too, for the
same reason.** This section originally asserted the generators "always
populate real, type-appropriate values... none of them conditionally omit a
field's value, only whether a *difficulty tag* is applied to already-present
text." That was false at the time of writing: `generate_invoice`'s
`fields` dict had **no `purchase_order_number` key at all, on any of the 75
invoices** — not a degenerate value, an entirely absent key, on 100% of one
document type — see
[EVALUATION_ERROR_ANALYSIS.md, Finding 4](EVALUATION_ERROR_ANALYSIS.md#finding-4--purchase_order_number-resolved-a-ground-truth-issue-not-a-model-weakness).
That this audit's own headline "0 documents missing an expected schema key"
didn't catch it indicates the check behind that number was validating against
`required_paths` (or a similarly narrower list) rather than every field a
document type's Pydantic schema declares — `purchase_order_number` is neither
required nor critical, so a required-only check would report clean while
missing it entirely. **Not independently re-verified against the original
audit script** (not committed, and not re-derivable from this document alone)
— stated as the most likely explanation consistent with the evidence, not as
a confirmed mechanism.

As of 2026-08-19, `generate_invoice` does conditionally populate one field
now: `purchase_order_number` is a real value on the 36 invoices where the
generator's decoy block writes a labelled PO reference into the text, and
`None` — an explicit key with a null value, not an absent key — on the other
39. This is now a **deliberate, verified exception** to "always populated"
(see the regression test cited in Finding 4), not an unnoticed gap: it is
the one field in this corpus where "key present, value null" is the
*correct*, audited state for a majority-adjacent share of documents, not an
oversight.

## What this corpus does and does not support

Same governing statement as [EVALUATION_PROTOCOL.md](EVALUATION_PROTOCOL.md) and
every prior version of `EVALUATION.md`, restated here with the specific numbers
behind it:

- **Supports:** relative comparison (model vs. model, prompt vs. prompt, clean vs.
  OCR'd) and regression detection, at good statistical volume for invoice (n=75),
  reasonable volume for receipt (n=21) and purchase_order (n=20), and
  **insufficient volume for any contract-specific claim (n=4)**.
- **Does not support:** any claim about real, non-synthetic documents (0 in this
  corpus); any claim about multi-page documents, table-only/unlabeled layouts,
  rounded totals, or credit notes (0 documents exercise any of these, despite
  being documented hazard classes); any claim about mixed clean/scanned inboxes
  (the OCR path is all-or-nothing per run, not per-document).
