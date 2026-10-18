# Docflow backend

Template-driven document extraction. Upload a PDF, pick a template (invoice, contract,
insurance policy, or your own inline), and get structured fields where **every value is
grounded**: the LLM must return the page and a verbatim quote, and deterministic code
checks that the quote really is in the PDF before trusting it. Template rules then catch
business errors (IČO checksum, net + VAT ≠ total, due date before issue date).

```
PDF ─► text per page ─► LLM (JSON per field: value, page, quote, confidence)
     ─► grounding check ─► normalisation (CZ numbers/dates) ─► template rules ─► issues
```

## Run

```bash
uv sync
cp .env.example .env
ollama pull qwen3.8:27b   # default: local model via Ollama, no API key
uv run uvicorn app.main:app --reload
```

`LLM_PROVIDER=openai` + `OPENAI_API_KEY` switches to OpenAI structured outputs. Ollama
cannot grammar-constrain this model, so the local path uses a forced tool call, falls back
to plain JSON when Ollama's tool parser fails, and validates/retries with Pydantic.
Measured on the 33-document evaluation set (Mac, 32 GB): 99.7 % field accuracy with prompt
v2; median 69 s/document for qwen3.8:27b, 23 s for qwen3.6:35b-a3b (`OLLAMA_MODEL`).

API docs at http://localhost:8000/docs. Sample PDFs (fictional) are in `samples/`;
regenerate with `uv run python scripts/make_samples.py`.

## Evaluation dataset

`dataset/` holds 33 realistic but fictional PDFs (16 invoices in 3 layouts incl. a bilingual
EUR one, a 2-page one and a credit note, 10 contracts of 4 types, 7 insurance policies) with
ground truth in `dataset/labels.json`. 11 documents contain planted errors (bad IČO checksum,
totals that don't add up, dates in the wrong order) and 7 are hard cases (swapped party
boxes, "splatné ihned", spaced IČO, a duration instead of an end date, dates with times). Regenerate deterministically with
`uv run python scripts/make_dataset.py`; `tests/test_dataset.py` checks every label against
the PDF text and the rule engine. Evaluate with `uv run python scripts/evaluate.py`
(`--model`, `--limit`, `--only invoice`, `--resume evals/<run>.json`); reports land in
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
