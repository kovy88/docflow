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
Measured on the samples (M-series Mac, 32 GB): all 4 docs correct, ~130–170 s per document.

API docs at http://localhost:8000/docs. Sample PDFs (fictional) are in `samples/`;
regenerate with `uv run python scripts/make_samples.py`.

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
