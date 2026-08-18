# Docflow — Project Status

Living progress log, kept honest: a checkbox here means verified, not
"written and assumed to work." Earlier drafts of this file marked several
phases done before the corresponding code existed — see git history if
that's of interest. This version reflects what actually runs, as of the
date below.

**Last updated:** 2026-08-17

---

## What this is

Docflow turns unstructured business documents (invoices, contracts, purchase
orders, receipts) into **validated structured data**, with asynchronous
processing, per-field confidence, human review, evaluation and multi-tenancy.

The core product question is not *"which LLM do you use?"* but *"can I trust
this output enough to stop having a human retype it?"* — so most of the
engineering goes into validation, confidence, review and measurement rather
than prompt cleverness. See [docs/AI.md](AI.md) for that argument in full.

---

## Phases

### Phase 1-2 — Foundation
- [x] Architecture ([ARCHITECTURE.md](ARCHITECTURE.md)), data model
      ([DATABASE.md](DATABASE.md)), 11 ADRs ([DECISIONS.md](DECISIONS.md))
- [x] SQLAlchemy 2.0 async models, all 18 tables, one baseline Alembic
      migration
- [x] Repository layer, org-scoped by construction (`OrgScopedRepository`)
- [x] JWT auth (access + refresh, `typ`-checked), Argon2id password hashing,
      hashed/prefixed API keys
- [x] Storage abstraction — local filesystem + S3-compatible

### Phase 3-4 — Ingestion & async processing
- [x] Upload with validation (size, MIME sniffing, extension agreement),
      content-hash dedup, idempotency keys
- [x] Text extraction (PDF, DOCX, TXT, images) with OCR fallback on a
      text-density heuristic
- [x] `arq` + Redis queue, 11-stage pipeline, retry-only-what's-retryable,
      dead-lettering, per-stage timing persisted for the UI timeline

### Phase 5-7 — AI extraction, validation, review
- [x] `LLMProvider` abstraction: Anthropic, OpenAI, Gemini, and a
      deterministic fixture (no real model calls, tests/CI/demos need no
      API key)
- [x] Structured output only — no tool access, no free-text channel (see
      [SECURITY.md](SECURITY.md#prompt-injection-defense))
- [x] Cheap-first classification (heuristic, LLM only below threshold)
- [x] Three validation layers (syntax / semantic / business rules)
- [x] Multi-signal confidence scoring with calibration checked against the
      evaluation corpus
- [x] Review queue, field-level correction, approve/reject, correction
      tracking (`field_corrections` — captured, not yet consumed by an
      automated feedback loop)

### Phase 8 — Frontend
- [x] Next.js 15 + TypeScript + **Tailwind v3** (not v4 — corrected from an
      earlier draft of this file), hand-rolled shadcn-style UI kit
- [x] Dashboard, documents list, document detail (fields + timeline + inline
      correction), review queue, settings, ROI calculator
- [x] Verified against the real backend through a full browser walkthrough,
      not just a production build

### Phase 9 — Evaluation
- [x] 120-document synthetic corpus with exact ground truth, seeded for
      reproducibility, deliberately injected difficulty
- [x] Rule-based baseline, three-tier match metrics, precision/recall,
      confidence calibration — `uv run docflow-eval` (there is no `Makefile`;
      an earlier draft of this file referenced `make eval`, which was never
      accurate)
- [x] Baseline measured: 56.0% normalized field accuracy, 90.1% critical-field
      accuracy, 5.8% document success — see [EVALUATION.md](EVALUATION.md)
      for the full report
- [x] `docflow-calibrate` — re-derives confidence-band accuracy from the
      production scoring code in deciles, not just the three bands. Found
      two genuine bugs, not one: a non-breaking-space thousands separator
      silently truncating money values (fixture data), and every `currency`
      field scoring artificially low regardless of correctness (found only
      once a full real-model run existed — see below). Both also weakened
      production confidence scoring, not just eval numbers — see
      [EVALUATION.md](EVALUATION.md#confidence-calibration) and
      [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) for the full story
- [x] **Real LLM accuracy — measured, full corpus, two providers.** OpenAI
      (gpt-4.1-mini): 120/120 documents, zero hard failures, 80.0% field
      accuracy, 100% required-field accuracy, 100% doc success. Gemini:
      83.5% field accuracy on 11 of 20 documents that completed under a
      free-tier key (9 hard-failed, most likely rate limiting) — kept as a
      secondary, quota-limited data point. Fixing the currency-calibration
      bug above dropped the OpenAI run's review rate from 50.8% to
      **17.5%** — a real, measured product improvement, not just a report
      number — see [EVALUATION.md](EVALUATION.md) for the full numbers

### Phase 10 — Observability & security
- [x] Structured JSON logs (`structlog`, unified with uvicorn/SQLAlchemy/arq
      via one `ProcessorFormatter`), correlated by request/org/document/job id
- [x] Prometheus metrics, bounded label cardinality
- [x] Rate limiting (Redis-backed fixed window, fails open)
- [x] Security headers, CORS allowlist, SSRF-checked webhook registration
      (known gap: DNS rebinding — stated in
      [SECURITY.md](SECURITY.md#known-gaps))
- [x] Tenant isolation covered by dedicated tests, including a regression
      test for a real cross-tenant queue-collision bug found during manual
      testing (see [SECURITY.md](SECURITY.md#tenant-isolation))

### Phase 11 — Testing & CI
- [x] 263 backend tests (unit + integration against a real Postgres,
      transaction-rollback isolation) — passing, clean `ruff`/`mypy`
- [x] **Frontend E2E test coverage** — 5 Playwright tests against the real
      stack (`frontend/e2e/`, `npm run test:e2e`): the critical flow
      (register → upload → wait for processing → edit a field → save →
      approve) plus auth/empty-state error paths. Not yet wired into CI
      (local/on-demand only) — see
      [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)
- [x] `bandit` + `pip-audit` clean (pip-audit's one expected finding is
      auditing this project's own editable install, soft-failed in CI)
- [x] GitHub Actions: lint/typecheck/test (backend and frontend, separate
      workflows), Docker image build validation
- [x] Frontend: `npm run lint`, `npm run typecheck`, production build, all
      green

### Phase 12-14 — Deployment, integrations, documentation, audit
- [x] Backend and frontend Dockerfiles — **built and verified**, not just
      written: both images build clean, `docflow` imports correctly with
      OCR extras installed, and the frontend correctly bakes in
      `NEXT_PUBLIC_API_URL` at build time
- [x] `docker-compose.yml` — **the full stack (Postgres, Redis, API, worker,
      frontend) verified running together and talking to each other**
      through container networking, including a real upload → classify →
      extract → validate → score → review-route run and a browser login
      against the containerized frontend. This surfaced and fixed real bugs
      that a written-but-unrun config would not have caught — see below.
- [x] **Deployed to live Render and Vercel accounts, not just configured.**
      Frontend at https://frontend-nine-brown-40.vercel.app, API at
      https://docflow-api-o6o1.onrender.com, backed by Supabase
      (Postgres + storage) and Render Key Value (Redis). The
      `docflow-worker` background worker is deployed and live on Render's
      Starter plan, confirmed via the Render API rather than assumed from
      `render.yaml` existing — see [DEPLOYMENT.md](DEPLOYMENT.md) for the
      exact URLs and the deliberate trade-offs still in place (API on the
      free tier, so it cold-starts; Gemini as the configured provider since
      no Anthropic key was available at deploy time)
- [x] Seed/demo script (`docflow-seed`) — creates a demo org and runs 5
      documents (one of each built-in type) through the real pipeline;
      idempotent; verified end-to-end including in the browser
- [x] n8n example workflow (email intake → upload; webhook → Slack
      notification) — [integrations/n8n/](../integrations/n8n/README.md)
- [x] Webhook delivery (HMAC-signed) + CSV/JSON export
- [x] Full documentation set: this file plus ARCHITECTURE, DATABASE, AI,
      SECURITY, EVALUATION, API, LOCAL_DEVELOPMENT, DEPLOYMENT, DECISIONS,
      11 ADRs, and a project-root README — cross-links checked
      programmatically (every relative link and anchor resolves)
- [x] `docs/FINAL_REPORT.md` — product framing, trade-offs, and a 30+
      question interview-style Q&A

---

## Real bugs found by actually running the system, not just writing it

Kept here deliberately, because "no known issues" from a system that was
never exercised end-to-end is not a meaningful claim. All fixed, with
regression coverage added where a unit/integration test could catch a
recurrence (the queue-collision bug); the rest were configuration/Docker
issues no test suite would exercise, only caught by actually running
`docker compose up` and clicking through the result:

- **Cross-tenant queue-job-id collision** — two organizations uploading
  byte-identical content collided on `arq`'s global job-id key; the second
  organization's document silently never got processed. Found via manual
  browser testing, fixed, regression test added.
- **`docker-compose.yml` bind mount shadowing the container's `.venv`** with
  the host's incompatible one — `uvicorn`/`arq` failed to launch at all on a
  fresh `docker compose up`. Fixed with an anonymous-volume mask.
- **Backend Docker image**: `uv sync` ran before application source was
  copied in, silently producing a non-functional editable install with the
  `ocr` extras missing entirely — OCR would have failed at runtime despite
  the system packages being present. Fixed by reordering to the standard
  two-phase `uv sync` Docker pattern.
- **`/data/storage` named volume created with root ownership** — every
  upload would fail with a permission error on a clean `docker compose up`,
  since the container runs as a non-root user. Fixed by pre-creating and
  `chown`ing the directory in the image.
- **`NEXT_PUBLIC_API_URL` set as a runtime `environment:` variable** in
  `docker-compose.yml` instead of a build arg — silently ignored, since
  Next.js inlines `NEXT_PUBLIC_*` values at build time. The frontend was
  calling its own origin instead of the API. Fixed; verified by grepping the
  built bundle for the URL and by a real browser login through the
  containerized stack.
- Plus earlier-phase fixes (config `NoDecode` bug, classification cascade
  threshold bug, confidence self-agreement inflation, `/timeline` 404
  inconsistency, post-approval UI contradiction) — see git history for
  detail; not repeated here since they predate this file's last rewrite.

Not a bug, but found the same way — by checking rather than assuming: the
worker's retry/dead-letter/fail decision logic
(`docflow.worker.tasks.process_document` — the code the module's own
docstring describes as "retry only what's retryable... never lose a
document silently") had **zero test coverage**. `test_pipeline.py` exercises
the pipeline directly, bypassing the job/retry machinery entirely, and
nothing else called `process_document`. Closed with 9 new integration tests
against real job/document rows (`tests/integration/test_worker_retry.py`)
covering every branch: retryable vs. non-retryable errors, exhausted vs.
not-yet-exhausted attempts, a returned-FAILED result vs. a raised exception,
and the dead-letter path specifically.

---

## Known gaps / future work

Tracked honestly — see [docs/FINAL_REPORT.md](FINAL_REPORT.md) for the full
discussion.

- **Real-LLM accuracy is measured against the full, unlimited 120-document
  corpus** (OpenAI, 80.0% field accuracy, 100% doc success), not just a
  quota-limited slice — a secondary Gemini data point remains quota-limited
  (20 documents, 9 hard-failed under a free-tier key). Neither has been run
  at a production customer's actual document volume/variety, which is a
  different claim than "not run at scale" was implying before this was
  fixed. See [EVALUATION.md](EVALUATION.md#whats-measured-vs-not-summary).
- **The correction feedback loop is data-ready, not built.**
  `field_corrections` is indexed for "which fields does prompt version X get
  wrong most," but nothing automated consumes it yet.
- **DNS rebinding on webhook URLs is an accepted, documented gap** — SSRF
  protection checks at registration time, not at delivery time. See
  [SECURITY.md](SECURITY.md#known-gaps).
- Billing is architected (plans, quotas, usage records) but not wired to a
  payment processor — no real money moves through this system.
- OCR quality on real low-DPI scans is untested — the evaluation corpus is
  synthetic text rendered to PDF, not photographed/scanned paper.
- No third-party security review or penetration test has been run.
- Frontend E2E tests exist (5, covering critical flows) but aren't wired
  into CI yet — they run locally/on-demand only.
- No load testing has been run against the live deployment.

**For the full evidence-backed list — what's verified, how, and what's
explicitly not — see [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).**
It also documents three real bugs found and fixed after this file's phases
were originally marked done: a non-functional worker retry mechanism,
missing refresh-token revocation, and a concurrency bug that could
double-queue a reprocessed document.
