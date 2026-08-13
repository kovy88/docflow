# Docflow — Project Status

Living progress log. Updated as work proceeds.

**Last updated:** 2026-08-13

---

## What this is

Docflow turns unstructured business documents (invoices, contracts, purchase orders,
receipts) into **validated structured data**, with asynchronous processing, per-field
confidence, human review, evaluation and multi-tenancy.

The core product question is not *"which LLM do you use?"* but *"can I trust this output
enough to stop having a human retype it?"* — so most of the engineering goes into
validation, confidence, review and measurement rather than prompt cleverness.

---

## Phases

### Phase 1 — Architecture & project setup
- [x] Repository inspection (empty — greenfield)
- [x] Toolchain check (Python 3.11/uv, Node 22, Docker, Postgres client, poppler)
- [x] Architecture defined (`docs/ARCHITECTURE.md`)
- [x] Data model defined (`docs/DATABASE.md`)
- [x] ADRs for the load-bearing decisions (`docs/adr/`)
- [x] Backend package skeleton + config + tooling (ruff, mypy, pytest)
- [x] docker-compose for Postgres + Redis + API + worker

### Phase 2 — Persistence, auth, storage
- [x] SQLAlchemy 2.0 async models, all 18 tables
- [x] Alembic migration baseline
- [x] Repository layer (org-scoped by construction)
- [x] JWT auth (access + refresh), argon2 password hashing
- [x] API keys for machine access (hashed, prefixed, revocable)
- [x] Organization / membership / role model
- [x] Storage abstraction — local FS + S3-compatible

### Phase 3 — Document ingestion
- [x] Upload endpoint with file validation (size, MIME sniffing, extension)
- [x] Content-hash deduplication + idempotency keys
- [x] Text extraction: PDF (native), DOCX, TXT, images
- [x] OCR fallback with text-density heuristic
- [x] Page/metadata extraction

### Phase 4 — Async processing
- [x] arq queue + worker (ADR-002)
- [x] Explicit pipeline stages with typed contracts
- [x] Retries with exponential backoff, only for retryable error classes
- [x] Idempotent job enqueue (deterministic job IDs)
- [x] Failure states + dead-letter handling
- [x] Step-level timing persisted for the UI timeline

### Phase 5 — AI extraction
- [x] `LLMProvider` abstraction + Anthropic / OpenAI / fixture implementations
- [x] Structured output (tool-use / JSON schema), never free prose
- [x] Versioned prompt registry
- [x] Document classification (heuristic + LLM, cheap-first)
- [x] Schema registry with configurable document types
- [x] Token/cost/latency accounting per call
- [x] Prompt-injection defence (delimited untrusted content + instruction stripping)

### Phase 6 — Validation & confidence
- [x] Layer 1: Pydantic syntax validation
- [x] Layer 2: semantic rules (dates, arithmetic, currency, checksums)
- [x] Layer 3: per-document-type business rules
- [x] Per-field confidence with calibrated bands
- [x] Review-routing policy

### Phase 7 — Human review
- [x] Field-level edit / approve / reject / reprocess
- [x] Correction tracking (feeds evaluation)
- [x] Audit log

### Phase 8 — Frontend
- [x] Next.js 15 + TS + Tailwind v4, hand-rolled shadcn-style UI kit
- [x] Dashboard, documents table, document detail (preview + fields), settings
- [x] Processing timeline, confidence/validation surfacing, inline editing
- [x] ROI calculator

### Phase 9 — Evaluation
- [x] Ground-truth dataset + generator
- [x] Field-level metrics (exact / normalized), doc-level success, review rate
- [x] Rule-based baseline for comparison (ADR-009)
- [x] `make eval` runner with markdown/JSON reports

### Phase 10 — Observability & security
- [x] Structured JSON logging with request/job/document/org correlation
- [x] Prometheus metrics endpoint
- [x] Rate limiting
- [x] Security headers, CORS allowlist, tenant-isolation tests

### Phase 11 — CI/CD
- [x] GitHub Actions: lint, format, typecheck, unit + integration tests, security scan
- [x] Docker image builds

### Phase 12 — Deployment
- [x] Dockerfiles (API, worker), compose, Render blueprint, Vercel config
- [x] `docs/DEPLOYMENT.md`

### Phase 13 — Product polish
- [x] Landing page, pricing concept, ROI calculator
- [x] Seed/demo data + one-command demo
- [x] Webhook + CSV/JSON export integration
- [x] n8n example workflow

### Phase 14 — Documentation
- [x] README, ARCHITECTURE, LOCAL_DEVELOPMENT, DEPLOYMENT, API, SECURITY,
      EVALUATION, AI, DATABASE, DECISIONS, ADRs
- [x] `docs/FINAL_REPORT.md` with measured numbers + interview Q&A

---

## Known gaps / future work

Tracked honestly — see `docs/FINAL_REPORT.md` for the full list.

- Real-LLM accuracy numbers require an API key; see "Measured vs not measured" in
  `docs/EVALUATION.md`. Baseline and fixture-provider numbers are measured and real.
- Billing is architected (plans, quotas, usage records) but Stripe is not wired.
- OCR quality on low-DPI scans is untested against a scanned ground-truth set.
