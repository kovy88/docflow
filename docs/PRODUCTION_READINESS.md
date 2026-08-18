# Production Readiness

## What this document is

An evidence-backed checklist, not a certification. Every row in the table
below points at a specific test, file, or command — if a claim isn't
verifiable that way, it's marked **Not done** or **Not measured**, not
rounded up to "done." This document itself is a snapshot as of the commits
listed below, not a standing guarantee; re-run the commands in
[Re-verifying this document](#re-verifying-this-document) rather than trusting
the prose if time has passed.

**Methodology:** every claim in the table was checked against the actual
running code and test suite during this pass, not copied from an earlier
doc or an agent's summary. Several claims that an earlier internal audit
described as gaps turned out, on inspection, to be *already* fixed, already
tested, or not actually live problems yet — those distinctions are called
out inline rather than smoothed over. Five real problems were found and
fixed in the course of writing this document (below); that's the point of
doing the check for real instead of asserting from memory.

**Commits this covers:** `7638d04` onward — see `git log` for the full
history. The five most relevant to this document specifically:

| Commit | What |
|---|---|
| `a9c57a7` | Worker retries were structurally non-functional (`arq.Retry` not raised); refresh-token revocation was missing entirely; stale-job recovery didn't exist |
| `f140942` | Built the confidence-calibration tool; it found a real bug (non-breaking-space thousands separator silently truncating amounts) that was also weakening the production baseline cross-check signal, not just eval numbers |
| `71fbd47` | Five Prometheus metrics were defined and documented but never called from anywhere — wired them up |
| `ca3cf58` | Concurrent reprocess requests could both succeed and double-queue a document (TOCTOU race); fixed with row locking, verified the regression test fails without the fix |
| (see below) | A full, unlimited real-LLM evaluation run (120 documents, OpenAI) found a second confidence-calibration bug: every `currency`-kind field scored artificially low regardless of correctness |

## Evidence table

Status legend: **Verified** (has a passing automated test or a command you
can run to confirm it right now) · **Verified, limited** (real, but with a
stated scope limit — e.g. measured on a small or synthetic sample) ·
**Not measured** (the claim requires data that hasn't been collected) ·
**Not done** (known gap, no code exists for it yet).

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | Pipeline runs end-to-end (upload → classify → extract → validate → score → route) | Verified | `TestHappyPath` (`tests/integration/test_pipeline.py`); manual full-stack verification through `docker compose` |
| 2 | Worker retries transient failures (rate limit, timeout, storage error) | Verified | `TestRaisedDocflowErrors`/`TestReturnedFailedResult` (`tests/integration/test_worker_retry.py`) assert `arq.Retry` is actually raised with the correct `defer_score` — this was silently broken before `a9c57a7` (a plain exception was raised, which arq's dispatcher does not redeliver on; found by reading `arq==0.28.0` source directly, not by inference) |
| 3 | Jobs that exhaust retries are dead-lettered, not lost or retried forever | Verified | `test_retryable_result_exhausted_is_dead_lettered`, extended in `ca3cf58` to also assert the `jobs_dead_lettered` metric increments |
| 4 | Documents stuck mid-processing (worker died) are recovered | Verified | `TestStaleSweep` (`tests/integration/test_worker_retry.py`); `sweep_stale_jobs` runs on a cron (`worker/main.py`, every 5 minutes) |
| 5 | Reprocessing a document twice concurrently doesn't double-queue it | Verified | `test_concurrent_reprocess_only_one_wins` (`tests/integration/test_reprocess_race.py`) — real bug, fixed and tested in `ca3cf58`; the test was confirmed to fail against the pre-fix code before being trusted |
| 6 | Refresh tokens can be revoked (logout actually invalidates a session) | Verified | `TestAuth::test_logout_revokes_the_refresh_token` and siblings (`tests/integration/test_api.py`); this endpoint did not exist before `a9c57a7` despite tokens carrying a `jti` "to support revocation" since the schema was written |
| 7 | Tenant isolation (org A cannot read/modify org B's data) | Verified | `TestTenantIsolation`, plus cross-tenant cases in `TestApiKeys`/`TestWebhookRegistration` (`tests/integration/test_api.py`); enforced structurally via `OrgScopedRepository`, not per-route checks — see [SECURITY.md](SECURITY.md#tenant-isolation) |
| 8 | Role-based authorization (viewer cannot upload/delete/manage keys) | Verified | `TestRoleEnforcement` (`tests/integration/test_api.py`), 6 cases across the admin- and member-gated endpoints |
| 9 | Prompt injection cannot cause a side effect, only a wrong field value | Verified | Structural, not pattern-matched — no tool access, no free-text output channel, constrained JSON schema; `TestPromptInjection` (`tests/integration/test_pipeline.py`); full reasoning in [SECURITY.md](SECURITY.md#prompt-injection-defense) |
| 10 | Webhook SSRF protection (can't register an internal URL) | Verified | Resolves and checks every A/AAAA record at registration time; **known limitation, stated in the code and in SECURITY.md**: does not protect against DNS rebinding (a hostname that resolves differently at delivery time) — this is a real, unfixed gap, not a false claim |
| 11 | Prometheus metrics reflect real pipeline/LLM/job activity | Verified | Before `71fbd47`, `record_document`, `record_llm_call`, `record_llm_error`, `record_stage`, `record_validation_issue`, `job_retries`, and `jobs_dead_lettered` were defined with tests for none of them and callers for none of them — `/metrics` would report zero forever regardless of load. Now wired at one choke point per concern (see `docs/ARCHITECTURE.md#observability`) and covered by `TestMetricsEmission`, which runs the real worker task unstubbed and reads back actual Prometheus samples |
| 12 | Structured logs support tracing one document's path end to end | Verified | `request_id`/`organization_id`/`document_id`/`job_id` bound throughout; manually traced during Docker verification |
| 13 | Health/readiness endpoints reflect real dependency state | Verified | `/health` and `/readiness` check DB and Redis for real, not a hardcoded 200; see `tests/integration/test_api.py::TestPreviouslyUncoveredEndpoints` |
| 14 | Rule-based baseline extractor accuracy | Verified | 56.0% field accuracy / 90.1% critical-field accuracy on the 120-doc synthetic corpus, current as of the nbsp fix — see [EVALUATION.md](EVALUATION.md) |
| 15 | Confidence-score calibration (does the score predict correctness?) | Verified | `docflow-calibrate` re-derives band accuracy from the same production scoring code; found and fixed *two* real bugs, not one — the nbsp thousands-separator bug (below) against fixture data, and a second, arguably more important one against a full real-model run: every `currency` field scored artificially low regardless of correctness, which — because currency is a required field — silently inflated the review rate on **50.8% of documents to a corrected 17.5%**. Post-fix: fixture shows only `high`/`medium` bands, no `low` at all; the real-model run (2,983 fields, OpenAI, full 120-doc corpus) shows a clean single `high` band at 81.7% accuracy, no inversion. See [EVALUATION.md](EVALUATION.md#confidence-calibration) |
| 16 | Real LLM extraction accuracy | Verified | 120 documents, openai/gpt-4.1-mini, full corpus, zero hard failures: 80.0% field accuracy, 100% required-field accuracy, 100% doc success. A second provider (Gemini) corroborates on a smaller, quota-limited slice (20 documents, 11 completed, 83.5% field accuracy) — see [EVALUATION.md](EVALUATION.md) for both |
| 17 | Real LLM cost/latency per document | Verified | $0.0018/doc, mean 8.2s/doc (gpt-4.1-mini, 120-doc run); $0.0049/doc, mean 36.5s/doc (Gemini, 20-doc run). Neither latency figure has been investigated or optimized |
| 18 | FK indexes present for cascade-delete and future query patterns | Verified | Fixed in `ca3cf58` — `field_corrections.document_id`, `usage_records.document_id`/`extraction_id`, `webhook_deliveries.organization_id` were missing indexes their sibling FK columns on the same tables already had. Confirmed these weren't live query bottlenecks (no code path filters on them directly yet), but `ON DELETE CASCADE`/`SET NULL` from `documents` still forces a sequential scan of these tables on every document delete without the index |
| 19 | CI gates the things it claims to | Verified | Backend: ruff check + format check, mypy, full test suite with coverage, bandit, pip-audit (`.github/workflows/backend.yml`). Frontend: lint, typecheck, build (`.github/workflows/frontend.yml`) |
| 20 | Frontend has automated test coverage | Verified | 5 Playwright E2E tests (`frontend/e2e/`) against the real stack (Postgres, Redis, API, worker, frontend — fixture LLM provider, not mocked network calls): the critical flow (register → upload → wait for processing → edit a field → save → approve) plus auth/empty-state error paths (wrong password, duplicate email registration, fresh-account empty states, logout blocking authenticated routes). `npm run test:e2e`. Not yet wired into CI — runs locally/on demand only |
| 21 | OCR accuracy on scanned/low-DPI documents | **Not measured** | No scanned ground-truth corpus exists for this project |
| 22 | Accuracy on real (non-synthetic) customer documents | **Not measured** | No real-document ground-truth corpus exists or can be committed publicly; synthetic-corpus numbers are stated as an upper bound, not a prediction — see EVALUATION.md's methodology section |
| 23 | Billing wired to a real payment processor | **Not done** | Plans/quotas are architected and enforced transactionally; no money moves through the system. Stated plainly in SECURITY.md's known gaps, not implied elsewhere |
| 24 | Load testing / capacity planning | Verified, limited | `docflow-loadtest` (`backend/src/docflow/scripts/load_test.py`) against the local stack: 10 concurrent uploads (0 errors, mean 199ms), 50 general API requests (0 errors, p95 167ms), and — the one that matters — 10 concurrent reprocess calls on the *same* document: exactly 1 accepted (202), 9 refused (409), confirming the `ca3cf58` row-lock fix holds under real 10-way HTTP contention, not just the 2-connection unit test. Re-ran at 25-way contention too: still exactly 1/24. Light local check, not a capacity-planning benchmark or a test of the live Render deployment specifically — connection pool sizing and autoscaling under sustained real-world load are still unverified |
| 25 | Frontend loading/empty/error states audited across every page | **Not verified in this document** | Not re-checked in this pass; see [Open items](#open-items) |
| 26 | Docker images rebuild clean and `docker compose up` works from a fresh clone | **Not verified in this document** | Last manually verified earlier in this project's history (see git log around the Docker-bug-fix commits); not re-run in this pass |
| 27 | OpenAPI docs match actual request/response shapes | **Not verified in this document** | FastAPI generates these automatically from the Pydantic models, which makes drift less likely than hand-written docs, but this was not explicitly re-diffed against the schemas in this pass |
| 28 | Architecture/status docs are current | Verified | Swept every doc under `docs/` plus `README.md` for the two specific stale-claim patterns found (provider count stuck at three; test count stuck at 227) and fixed all of them: `AI.md`, `PROJECT_STATUS.md`, `FINAL_REPORT.md`, `LOCAL_DEVELOPMENT.md`, `README.md`. `DEPLOYMENT.md` had a third, more serious one — it said "no worker is deployed," which a live check of the Render API (`GET /v1/services`, `GET /v1/services/{id}/deploys`) showed was false: the worker exists, its latest deploy status is `live`, and it's on a paid Starter plan. Fixed, and cited as evidence rather than re-asserted, since a doc claiming "verified live" is exactly the kind of claim that goes stale silently |

## Four real problems, for context on how seriously to take "Verified" above

Read in full in their respective docs; summarized here because they're the
best evidence that "Verified" in this document means something:

1. **Retry mechanism was completely non-functional** (`a9c57a7`). A custom
   `RetryableJobError` was raised where `arq.Retry` was needed; arq's
   dispatcher redelivers on `arq.Retry` and nothing else, so every
   "retryable" failure was actually failing permanently after one attempt.
   Found by reading the installed `arq` library's source, not by inference
   — the existing tests at the time asserted the wrong thing and passed
   anyway. See [ARCHITECTURE.md](ARCHITECTURE.md).

2. **A non-breaking-space thousands separator silently truncated money
   values** (`f140942`). `Celkem: 78 287,00 CZK` (with `\xa0`, what Czech
   documents actually produce) was parsed as `287.00` — the leading digit
   group was dropped. This wasn't caught by confidence scoring because the
   grounding signal's string-normalization strips the exact separator that
   would have told the truncated value apart from the correct one. Found by
   the confidence-calibration tool flagging a non-monotonic accuracy curve
   and then investigating *why* instead of adjusting a threshold to hide it.
   The same code backs the production baseline cross-check signal for real
   LLM extractions, not just the eval path. See
   [EVALUATION.md](EVALUATION.md#confidence-calibration).

3. **Concurrent reprocess requests could both succeed** (`ca3cf58`). A
   read-check-write sequence on `document.status` with no row lock meant two
   near-simultaneous reprocess calls could both read "not in flight" and
   both queue a job. Verified with two genuinely separate Postgres
   connections racing each other, and verified the test actually catches
   the bug (fails against the pre-fix code, passes against the fix) rather
   than trusting the assertion on faith. Re-checked later at real HTTP scale
   with `docflow-loadtest` (row 24 below): 10, then 25, concurrent reprocess
   calls over real HTTP against the same document — exactly one accepted
   every time. See [ARCHITECTURE.md](ARCHITECTURE.md#concurrency-on-shared-rows).

4. **`DEPLOYMENT.md` said the worker wasn't deployed; it was.** Not a code
   bug, but the same failure mode as one — a document said something about
   the world that a five-minute live check would have disproven. The worker
   had been deployed to Render's Starter plan in an earlier session, but the
   doc describing the live deployment was never updated afterward, so it
   kept telling readers "uploaded documents will sit in `pending` forever"
   — which would have been actively wrong operational guidance. Caught only
   by querying the Render API directly instead of trusting the file.

5. **Every `currency`-kind field scored artificially low, regardless of
   correctness.** Found only once a full, unlimited real-LLM run (120
   documents, OpenAI, see rows 16/17) made the confidence-calibration
   sample large enough to trust: the *low* confidence band was *more*
   accurate than the *high* band, the opposite of what a working score
   should show. Root cause, fully verified (not left as a guess): the model
   correctly normalises a document's currency notation to an ISO 4217 code
   (`CZK`), but the corpus itself mixes notations — some documents spell it
   out, others use the local symbol (`Kč`) — and the code doesn't literally
   appear in source text using the symbol form. Grounding (the confidence
   signal that checks for literal appearance) scored a *correct* value as
   ungrounded, pulling every currency field's score down to ~0.544 — the
   exact mean score observed, confirmed by hand-computing the weighted
   formula before trusting the diagnosis. Fixed by marking `currency`-kind
   fields `groundable=False` across all four document schemas (correctness
   for this kind is "is it a valid ISO code," already covered by the format
   signal, not "does it appear verbatim") — the same shape of fix as the
   `notes`/enum/boolean fields that were already non-groundable, just missed
   for this one kind. Regression tests:
   `test_normalised_currency_code_does_not_match_a_local_symbol` and
   `test_currency_fields_are_not_graded_on_grounding` (parametrized across
   all four document types) in `tests/unit/test_confidence_and_security.py`
   — confirmed the second one fails without the fix before trusting it.

## Open items

Tracked, not hidden. In rough priority order:

- Wire the new Playwright E2E suite into CI — currently local/on-demand only.
- A real capacity-planning load test against the live Render deployment
  (sustained load, not a 10-25 request burst against localhost).
- Re-verify `docker compose up` from a clean clone; re-check OpenAPI docs
  against the actual schemas.
- Decide whether the DNS-rebinding gap in webhook SSRF protection is worth
  closing (delivery-time re-resolution or an egress proxy) or stays a stated
  limitation.

**Resolved, not just deferred:** a larger real-LLM evaluation run was
initially declined (Gemini's 20-request/day free tier would need a paid
tier or a different provider's key), but a working OpenAI key became
available afterward and was used to run the complete, unlimited 120-document
corpus — see row 16 above and [EVALUATION.md](EVALUATION.md). That run is
what surfaced the second confidence-calibration bug (row 15) — direct
evidence that this wasn't a wasted step even though the first-choice provider
(Gemini) didn't pan out.

## Re-verifying this document

```bash
cd backend
uv sync --extra dev --extra ocr
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy
uv run alembic upgrade head
uv run pytest tests/ -v                    # 263 tests as of this writing
uv run bandit -c pyproject.toml -r src -ll
uv run pip-audit
DOCFLOW_LLM_PROVIDER=fixture uv run docflow-calibrate   # confidence calibration, free/local
uv run docflow-eval                                     # baseline + fixture accuracy, free/local
cd ../frontend && npm run test:e2e                      # needs docker compose running
```

Row 16/17 (real LLM numbers) require a provider API key and spend real quota
— see [EVALUATION.md](EVALUATION.md#run-it-yourself).
