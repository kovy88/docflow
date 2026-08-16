# Docflow

Docflow turns unstructured business documents — invoices, contracts, purchase
orders, receipts — into validated structured data. Upload a PDF, image, or
scanned document; get back typed fields with per-field confidence scores, an
audit trail, and a human review queue for anything the system isn't sure about.

The core engineering problem is not "which LLM do you call" — it's **can a
business trust the output enough to stop retyping it by hand.** Most of this
codebase is validation, confidence scoring, and human review, not prompt
cleverness. See [docs/AI.md](docs/AI.md) for that argument in full, and
[docs/EVALUATION.md](docs/EVALUATION.md) for what's actually been measured
(and what hasn't — this project does not invent numbers it hasn't run).

## What's here

- **Generic document pipeline**, not an invoice-only tool: upload → validate →
  extract text (native or OCR) → classify document type → LLM structured
  extraction against a versioned JSON Schema → three-layer validation → confidence
  scoring → human review → persistence → export/webhook.
- **Swappable LLM provider** (Anthropic / OpenAI / a deterministic fixture for
  tests and offline demos) behind one interface — see [docs/AI.md](docs/AI.md).
- **Multi-tenant from the schema up**: every table is organization-scoped, every
  repository injects `organization_id` server-side, cross-tenant access returns
  404 rather than 403 (see [docs/SECURITY.md](docs/SECURITY.md)).
- **Async by default**: uploads return immediately; an `arq`/Redis worker does
  the actual processing, with idempotent job IDs and retry-only-what's-retryable
  error handling.
- **A real evaluation harness**: a 120-document synthetic corpus with exact
  ground truth, a rule-based baseline, and match-level/precision-recall/
  confidence-calibration metrics — not a vibe check. Real-LLM numbers are
  explicitly marked "not yet measured" rather than guessed (no API key has been
  configured in this environment).
- **A Next.js frontend** — dashboard, document list/detail with inline
  correction, review queue, settings, an ROI calculator — built to be
  demoed, not just to prove the API works.

## Quickstart

Requires Docker and Docker Compose. Nothing else — the stack builds its own
Python and Node environments inside containers.

```bash
docker compose --profile full up -d --build
docker compose exec api docflow-seed
```

Then open **http://localhost:3000/login** with the credentials the seed
script prints (`demo@docflow.dev` / `DocflowDemo2026!`). The demo account
starts with five documents already processed — one of each built-in document
type — so the dashboard, document list, and review queue aren't empty.

No LLM API key is required to run the demo: with no key configured, Docflow
uses a deterministic fixture provider that exercises the full pipeline
(classification, extraction, validation, confidence scoring, review routing)
without calling a real model. Set `DOCFLOW_LLM_ANTHROPIC_API_KEY` or
`DOCFLOW_LLM_OPENAI_API_KEY` to use a real one — see
[docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md).

The API is at `http://localhost:8000` (interactive docs at `/docs`); Postgres
and Redis are exposed on non-default host ports (5433, 6380) so they won't
collide with anything already running on your machine.

## Documentation

| Doc | Covers |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, request/processing flow, component boundaries |
| [AI.md](docs/AI.md) | Pipeline stages, prompt strategy, provider abstraction, confidence model |
| [DATABASE.md](docs/DATABASE.md) | Schema, multi-tenancy pattern, migrations |
| [API.md](docs/API.md) | Endpoint reference, auth, webhooks, rate limits, error format |
| [SECURITY.md](docs/SECURITY.md) | Threat model, tenant isolation, prompt-injection defense, secrets |
| [EVALUATION.md](docs/EVALUATION.md) | Methodology and **actual measured results** — including what isn't measured yet |
| [LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md) | Running it locally, with or without Docker, running tests |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Render/Vercel deployment, environment variables, scaling notes |
| [DECISIONS.md](docs/DECISIONS.md) | Index of architecture decision records, `docs/adr/` |
| [FINAL_REPORT.md](docs/FINAL_REPORT.md) | Product framing, trade-offs, cost model, and a 30+ question interview-style Q&A |

## Tech stack

**Backend:** Python 3.11, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL, Alembic,
`arq` + Redis for the job queue, Pydantic v2, `uv` for dependency management.

**Frontend:** Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS.

**AI:** Anthropic and OpenAI providers behind a shared interface; a
deterministic fixture provider for tests, CI, and API-key-free demos.

**Infra:** Docker (single backend image for API + worker), Docker Compose for
local dev, Render for API/worker/Redis, Vercel for the frontend, GitHub
Actions CI (lint, typecheck, tests against real Postgres/Redis, `bandit` +
`pip-audit` security scans).

## Status

227 backend tests (unit + integration, against a real Postgres via
transaction-rollback isolation), clean `ruff`/`mypy`/`bandit`. See
[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for what's built versus
what's deliberately left as future work, and
[docs/FINAL_REPORT.md](docs/FINAL_REPORT.md) for the honest trade-offs.
