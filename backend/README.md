# Docflow — Backend

FastAPI application, arq worker and document-processing pipeline.

See the repository root `README.md` for the product overview and
`docs/LOCAL_DEVELOPMENT.md` for how to run this.

```bash
uv venv && uv pip install -e ".[dev]"
uv run uvicorn docflow.main:app --reload
uv run arq docflow.worker.main.WorkerSettings
uv run pytest
```

Layout:

```
src/docflow/
  api/            HTTP layer — routing, auth dependencies, serialisation only
  domain/         Pure business concepts. No I/O.
  schemas/        Document type registry (invoice, contract, PO, receipt, ...)
  validation/     Three-layer validation engine + rule catalogue
  pipeline/       Explicit processing stages with typed contracts
  llm/            Provider abstraction (Anthropic / OpenAI / fixture) + pricing
  prompts/        Versioned prompt templates
  extraction/     LLM extractor, normalisation, rule-based baseline
  documents/      File validation, text extraction, OCR, classification
  storage/        Object-storage abstraction (local / S3-compatible)
  db/             SQLAlchemy models, session, repositories
  services/       Orchestration — the layer routes call into
  worker/         arq queue definition and tasks
  observability/  Structured logging, metrics, middleware
  eval/           Evaluation harness and metrics
```
