# Docflow backend

Template-driven document extraction. Upload a PDF, pick a template (invoice, contract,
insurance policy, or your own inline), and get structured fields where **every value is
grounded**: the LLM must return the page and a verbatim quote, and deterministic code
checks that the quote really is in the PDF before trusting it. Template rules then catch
business errors (IČO checksum, net + VAT ≠ total, due date before issue date).

```
PDF ─► text per page (text layer, or Tesseract OCR for scans) ─► LLM (JSON per field: value, page, quote, confidence)
     ─► grounding check ─► normalisation (CZ numbers/dates) ─► template rules ─► issues
```

## Run

```bash
uv sync
cp .env.example .env
ollama pull qwen3.8:27b   # default: local model via Ollama, no API key
brew install tesseract tesseract-lang   # OCR of scanned PDFs (Czech model `ces`)
uv run uvicorn app.main:app --reload
```

`LLM_PROVIDER=openai` + `OPENAI_API_KEY` switches to OpenAI structured outputs. Ollama
cannot grammar-constrain this model, so the local path uses a forced tool call, falls back
to plain JSON when Ollama's tool parser fails, and validates/retries with Pydantic.
Measured on the 33 text-layer documents of the evaluation set (Mac, 32 GB): 99.7 % field accuracy with prompt
v2; median 69 s/document for qwen3.8:27b, 23 s for qwen3.6:35b-a3b (`OLLAMA_MODEL`).

Pages without a text layer are rendered at 300 DPI (pypdfium2), cleaned with a 3×3 median
filter and read by Tesseract (`ces`) locally; `ocr_pages` on the document lists them. Quotes
on OCR pages are matched against the best quote-sized window with a 0.85 similarity
threshold (`OCR_QUOTE_THRESHOLD`) instead of 0.9 longest-substring, because one misread
character in the middle of a number would otherwise fail the check. Without Tesseract,
scans still upload but extraction reports `no_text`.

API docs at http://localhost:8000/docs. Sample PDFs (fictional) are in `samples/`;
regenerate with `uv run python scripts/make_samples.py`.

## Lease pilot

Built-in template `lease` extracts rental evidence without calculating missing dates or amounts.
Generate its fictional walkthrough with `uv run python scripts/make_lease_sample.py`.
It is separate from the measured evaluation dataset. The public demo exporter includes it
and skips the already computed examples on rerun.

- `POST /exports/xlsx`: body `{ "extraction_ids": [1, 2], "columns": ["tenant", "rent"] }`.
  Up to 100 unique extraction IDs, one per document; `columns` is an optional ordered subset
  of template field keys. Default: every field in template order. Filename and UTC review date
  are always included. Amounts/dates are native Excel cells, missing values blank, text never formulas.
  Entire batch rejected with 409 unless all results are human-approved, have `reviewed_at`,
  have no error issues, and share a template ID and field schema. Unknown IDs: 404; invalid
  selection/columns: 422. Existing individual JSON/CSV exports are unchanged.
- `DELETE /documents/{id}` removes all extraction rows, stored PDF and document text in one
  transaction. Active extraction: 409; absent document: 404. Historical backups are separate.
- Both operations and template reads require `X-Access-Code` when configured. This now also
  protects custom template names and instructions. Public static demo remains readable.
- `PILOT_MODE=true` refuses startup without a non-empty `ACCESS_CODE`.

See [pilot operations](../docs/pilot/operations.md) for separate deployment, SQLite backup and
an isolated PostgreSQL restore drill. No client instance is provisioned by checking out this code.

## Evaluation dataset

`dataset/` holds 37 realistic but fictional PDFs (18 invoices in 3 layouts incl. a bilingual
EUR one, a 2-page one and a credit note, 11 contracts of 4 types, 8 insurance policies) with
ground truth in `dataset/labels.json`. 12 documents contain planted errors (bad IČO checksum,
totals that don't add up, dates in the wrong order) and 7 are hard cases (swapped party
boxes, "splatné ihned", spaced IČO, a duration instead of an end date, dates with times). 4 are scans: image-only PDFs, slightly rotated, blurred and speckled,
read by OCR (`difficulty: "scan"`). Regenerate deterministically with
`uv run python scripts/make_dataset.py`; `tests/test_dataset.py` checks every label against
the PDF text (for scans: that OCR reads ≥ 80 % of the values) and the rule engine. Evaluate with `uv run python scripts/evaluate.py`
(`--model`, `--limit`, `--only invoice`, `--difficulty scan`, `--resume evals/<run>.json`); reports land in
`evals/` and on the Evaluace page.

## Test

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests use a fake extractor, so they need no API key.

## Layout

- `app/templates/*.json` – the only domain-specific part; add a file for a new document type
- `app/extractor.py` – Ollama (tool call + JSON fallback) and OpenAI extractors behind one `Extractor` protocol
- `app/checks.py` – grounding, normalisation, rules (no LLM, fully unit-tested)
- `app/pipeline.py` – glues extraction and checks into a reviewable result
- `app/main.py` – FastAPI endpoints; SQLite locally, Postgres/Supabase via `DATABASE_URL`
