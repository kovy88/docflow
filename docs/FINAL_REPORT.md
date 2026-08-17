# Final report

A synthesis document — product framing, the trade-offs behind the
architecture, what's actually measured, and an interview-style Q&A. The
detailed docs it links to are the source of truth; this is the tour.

## What this is

Docflow turns unstructured business documents — invoices, contracts,
purchase orders, receipts — into validated structured data, with async
processing, per-field confidence, human review, and multi-tenancy built in
from the schema up. Initial target vertical: legal/accounting/business
services SMBs in the Czech/Central-European market, chosen because that
segment does high-volume, template-ish document processing by hand today
(bookkeepers retyping invoices into accounting software), has clear
willingness to pay for time saved, and has specific locale requirements
(Czech diacritics, IČO/DIČ/IBAN checksums, EU number/date formats) that a
US-first extraction tool typically handles poorly — a real, defensible wedge
rather than a generic "AI document processor."

The product thesis, stated once because it shapes every other decision in
this codebase: **the differentiator isn't extraction, it's trust.** Any
competent team can wire an LLM to a JSON Schema. The harder, more valuable
problem — three validation layers, multi-signal confidence scoring, a review
queue with correction tracking, an evaluation harness that measures instead
of assumes — is what turns "the model produced an answer" into "a business
can act on this without checking by hand." See [AI.md](AI.md) for that
argument at full length.

## Architecture, in one paragraph

FastAPI handles synchronous HTTP concerns and enqueues work; an `arq`/Redis
worker runs an 11-stage pipeline (validate → extract text → OCR if needed →
classify → select schema → extract via LLM → cross-check against a
rule-based baseline → validate → score confidence → route to review) off the
request path. PostgreSQL holds metadata and results with `organization_id`
enforced at the repository layer, not trusted from request input; files live
in object storage, never as DB blobs. One Docker image serves both API and
worker. Full detail: [ARCHITECTURE.md](ARCHITECTURE.md). Every load-bearing
choice — why FastAPI, why `arq`, why Postgres, why one image, why Render
over Kubernetes, and six more — is recorded as an ADR with alternatives
considered and consequences stated honestly:
[DECISIONS.md](DECISIONS.md).

## What's measured, and what isn't

Stated here because it's the single most important honesty check on this
whole project, not because it's flattering:

| | |
|---|---|
| Pipeline runs correctly end-to-end | **Measured** — 258 automated tests + manual full-stack verification through `docker compose` and the live Render/Vercel deployment |
| Rule-based baseline accuracy (120-doc synthetic corpus) | **Measured** — 56.0% normalized field accuracy, 90.1% critical-field accuracy, 5.8% document success. [EVALUATION.md](EVALUATION.md) |
| Confidence-scoring machinery is internally consistent | **Measured** — and it found a real bug: a non-breaking-space thousands separator was silently truncating money values, weakening the production confidence signal, not just an eval number. Found by the calibration tool flagging a non-monotonic accuracy curve and investigating why instead of adjusting a threshold to hide it. Fixed; see [EVALUATION.md](EVALUATION.md#confidence-calibration) |
| **Real LLM (Gemini) extraction accuracy** | **Measured, quota-limited.** 83.5% field accuracy, 100% required-field accuracy on the 11 of 20 documents that completed under a free-tier key (9 hard-failed, most likely rate limiting). Not yet run at production scale. [EVALUATION.md](EVALUATION.md) |
| **Real LLM cost per document** | **Measured, quota-limited.** $0.0049/document on the same 20-document run. |
| **Real LLM latency** | **Measured, quota-limited.** Mean 36.5s/document on the same run — not yet investigated or optimized. |
| Accuracy on real (non-synthetic) documents | **Not measured** — no real-document ground-truth set exists for this project. |
| OCR accuracy on scanned/low-DPI documents | **Not measured** — the corpus is synthetic text rendered to PDF, not photographed paper. |

Nothing in this report estimates or extrapolates a number in place of these.
Where a number is quota-limited rather than final, it says so explicitly
rather than presenting it as a production-scale result.

## Cost model

The dashboard's ROI calculator (`frontend/src/components/roi-calculator.tsx`)
is a **planning tool with editable assumptions**, not a report of measured
figures — it says so in its own UI copy. Three inputs (documents/month,
minutes to process manually, fully-loaded hourly cost) plus an AI
cost-per-document field that defaults to a labeled estimate (`$0.08`,
captioned "see docs/EVALUATION.md for measured figures") unless real usage
data is available, in which case it's pre-filled and captioned "from your
measured usage" instead. The formula models human review realistically —
10% of documents still cost review time, not assumed away — rather than
claiming 100% automation. Real LLM calls have now been made (the Gemini
evaluation run, $0.0049/document — see the table above); whether the *live
deployed* organization's own usage has accumulated enough real
`usage_records` rows to trigger the calculator's measured-usage pre-fill
specifically has not been checked. Locally, against the fixture provider
(zero real API calls, by design — see [AI.md](AI.md)), every dashboard cost
figure still reads `$0.0000`, honestly.

## Security summary

Full detail: [SECURITY.md](SECURITY.md). The two properties that matter most
for this shape of product: **tenant isolation** (every query scoped by
`organization_id` at the repository layer, cross-tenant access returns `404`
not `403`, covered by dedicated tests) and **prompt injection containment**
(the extraction call has no tool access and no free-text output channel —
structured output only — so a malicious document's worst case is a wrong
field value, not an action). Known, stated gaps: webhook SSRF protection
checks at registration time, not delivery time (DNS-rebinding is not
closed); no third-party security review has been run.

## Scalability

API is stateless and scales horizontally behind a load balancer. The worker
has no natural autoscaling signal (no HTTP traffic to scale against) —
`docflow_queue_depth` in Prometheus is the metric to watch, and today
instance counts are set manually in [render.yaml](../render.yaml), not
policy-driven. The database is the eventual bottleneck at real scale: one
Postgres instance serves every tenant (see
[ADR-006](adr/006-multi-tenancy.md)), mitigated by org-scoped composite
indexes rather than physical separation, which is the right trade until a
specific customer's compliance requirement forces it, not before. Full
discussion: [DEPLOYMENT.md](DEPLOYMENT.md#scaling-notes).

## Biggest trade-offs, stated against myself

- **Chose repository-enforced multi-tenancy over database-per-tenant.**
  Cheaper to operate, weaker isolation ceiling. Right call at zero
  customers; wrong call the day one customer's compliance requirement needs
  physical separation — see [ADR-006](adr/006-multi-tenancy.md).
- **Chose a synthetic evaluation corpus over hand-labeling real documents.**
  The only honest option given no real, licensable, field-labeled Czech
  business-document corpus exists — but it means every accuracy number in
  this project is explicitly an upper bound, not a production estimate. See
  [EVALUATION.md](EVALUATION.md).
- **A real LLM key was obtained (Gemini), but the free tier caps it at 20
  requests/day.** The single most commercially important number — "how
  accurate is this against a real model" — now has a real answer (83.5%
  field accuracy), but on a sample too small and too failure-prone (9 of 20
  requests hard-failed, most likely rate limiting) to call final. Everything
  upstream of that call (validation, confidence, review, evaluation harness)
  was built and measured against the fixture first specifically so that
  running the real number was a config change, not a rebuild — which is
  exactly what happened. What's still missing is a larger, paid-tier run.
- **Chose Render + Compose over Kubernetes.** Right-sized for the actual
  workload today (two process kinds); would need revisiting if the product
  grows into genuinely independent services. See
  [ADR-011](adr/011-render-over-kubernetes.md).

## What I'd build next

In priority order, and why that order: (1) a larger, non-quota-limited real
LLM evaluation run — the current 83.5% is measured on 11 completed
documents, not enough to be a production claim, and every other roadmap
decision below is downstream of knowing this number with confidence; (2)
close the correction-feedback loop — the data (`field_corrections`, indexed
by prompt version) is already there, unconsumed; (3) a real-document pilot
with one design partner, since synthetic-corpus accuracy is explicitly a
ceiling, not a floor; (4) DNS-rebinding-safe webhook delivery; (5)
autoscaling policy for the worker once there's real traffic to tune it
against, not before.

---

## Interview Q&A

### Product & business

**What problem does this solve, and for whom?**
SMBs (initial vertical: Czech/CEE legal/accounting/business services) that
process a steady volume of structured-but-messy documents by hand — an
accountant retyping invoice line items into bookkeeping software, a firm
logging incoming purchase orders. The buyer is usually the owner or
operations lead; the user is often someone else (a bookkeeper), which
matters for onboarding — the tool has to be trustworthy to the user, not
just cheap to the buyer.

**Why the Czech/CEE market specifically, not a generic "AI invoice tool"?**
Locale specificity is the wedge: Czech diacritics, IČO/DIČ/IBAN checksum
validation, EU date/number formats, decimal-comma parsing — a US-first
extraction tool typically gets these wrong or ignores them entirely. That's
a real, defensible advantage over a generic competitor, not a nice-to-have.

**How would you price this?**
Per-document or tiered-by-volume, not per-seat — the value delivered scales
with documents processed, not with headcount. The `plan`/
`monthly_document_quota` fields already in the schema
([DATABASE.md](DATABASE.md)) support tiering; nothing about billing
mechanics (a payment processor, metered billing) is built yet — see
[PROJECT_STATUS.md](PROJECT_STATUS.md).

**What's the path from this project to something sellable?**
In order: measure real LLM accuracy against a design partner's actual
documents (not the synthetic corpus), wire a payment processor to the
already-architected plan/quota model, run one pilot customer's real volume
through it to find what actually breaks. Everything before that step is
necessary but not sufficient.

**What's the actual moat here, if the core idea (LLM + structured output) is
easy to copy?**
Not the extraction call — the validation/confidence/review loop, and
eventually the correction data it captures. A competitor can call an LLM in
an afternoon; reproducing a calibrated confidence model and a working
feedback loop from real correction data takes real usage, which takes time
in market.

### Architecture

**Walk me through what happens when someone uploads a document.**
See [ARCHITECTURE.md#request-flow-upload-to-result](ARCHITECTURE.md#request-flow-upload-to-result)
for the full version. Short version: validate → checksum → write to object
storage → insert `documents`/`processing_jobs` rows → return immediately
with a job id → a worker picks it up asynchronously and runs the pipeline →
webhook fires on completion.

**Why async processing instead of just handling it in the request?**
Processing is seconds-to-tens-of-seconds (OCR, one or more LLM calls,
validation) with a long tail. Holding an HTTP request open for that ties up
a request worker for I/O-bound work and gives the client nothing to render
in the meantime. See
[ARCHITECTURE.md#why-async-processing-not-synchronous-in-request](ARCHITECTURE.md#why-async-processing-not-synchronous-in-request).

**Why `arq` instead of Celery?**
Everything else in this stack is asyncio-native (SQLAlchemy's async engine,
`httpx.AsyncClient` for LLM calls). Celery's async story is a sync-worker
model with async bolted on; `arq` is asyncio-native throughout, for one
workload that doesn't need Celery's broader ecosystem. Full comparison,
including why RQ and Dramatiq also lost:
[ADR-002](adr/002-async-job-queue.md).

**How do you actually know tenant isolation works, versus just designed it
to?**
Dedicated tests assert org A's token cannot read/modify/enumerate org B's
data. More convincingly: it caught a real bug during manual testing — a
queue job-id collision that let one organization's upload silently block
another's, from an under-scoped hash — which is exactly the kind of failure
"designed carefully" doesn't guarantee against and "tested and once broke in
practice" does. See [SECURITY.md#tenant-isolation](SECURITY.md#tenant-isolation).

**Why Postgres over a document database, given you're literally storing
"documents"?**
Most of the schema (organizations, memberships, jobs, reviews, audit logs)
is relational and constraint-heavy — foreign keys with real cascade
semantics, unique constraints that encode business rules like
content-addressed dedup. The document-shaped part (`extractions.data`) is a
JSONB column, not the whole schema. See [ADR-003](adr/003-postgresql.md).

**Why one Docker image for the API and the worker instead of two?**
They're two entrypoints into the same application code, not two
applications — the worker imports and runs the same pipeline the API
enqueues work for. One image is one thing to build, scan, and version. See
[ADR-010](adr/010-single-docker-image.md).

**Why Render and Docker Compose instead of Kubernetes?**
The workload is two process kinds and two managed dependencies (Postgres,
Redis) — exactly Render's service model. Kubernetes would add a control
plane, manifests, and on-call surface for zero capability this deployment
currently needs. Explicit trade-off, not an oversight — see
[ADR-011](adr/011-render-over-kubernetes.md), the most consequential "no" in
this project's infrastructure choices.

**How does a document's processing status actually get from the worker back
to the frontend?**
Polling today (`GET /documents/{id}/status`), not a websocket/SSE push — the
frontend re-polls on an interval while a document is in flight. Simpler to
build and reason about than a push channel, at the cost of not being
instant. Webhooks exist for server-to-server "tell me when it's done"
instead of polling.

### AI / ML

**How does the system decide a document needs human review?**
A policy over multiple signals, not one threshold: overall confidence below
the document type's threshold, OR any field marked `critical` for that type
below a stricter critical-field threshold even if overall confidence looks
fine, OR a required field missing, OR a business rule failing outright.
Every trigger is recorded as a specific reason, not a boolean. See
[AI.md#review-routing](AI.md#review-routing).

**Walk me through the confidence score. Why isn't it just the model's
self-reported confidence?**
It's a weighted combination of five signals — grounding in source text
(40%), model-reported (20%, deliberately unpopulated — see below),
format cleanliness (15%), validation outcome (15%), OCR-vs-native context
(10%) — averaged only over whichever signals are actually present for a
given field, not defaulted to zero when absent. Model-reported confidence is
excluded because it's well-documented as poorly calibrated across providers;
folding an unreliable signal into an otherwise-principled score would make
the whole score worse, not better. Full formula and the real
self-agreement-inflation bug this design caught:
[AI.md#confidence-scoring](AI.md#confidence-scoring).

**How do you defend against prompt injection when the input is an arbitrary
uploaded document?**
Four layers, most-important first: the extraction call has no tool access
and no free-text output channel at all (structural, holds even if every
other layer fails), constrained structured output (the response space *is*
the schema), nonce-delimited untrusted-content framing, and downstream
validation that would catch a manipulated value surviving the first three
(e.g. an injected total that breaks the line-items-sum-to-total check).
Deliberately *not* done: scanning for suspicious phrases — a losing arms
race with real false positives. See
[SECURITY.md#prompt-injection-defense](SECURITY.md#prompt-injection-defense).

**Why structured output instead of asking the model to return JSON in
prose?**
Reliability and security both. Reliability: a schema-constrained response
doesn't need brittle regex/prose-parsing to extract the JSON. Security: it
closes off the model's only possible "action" to a schema-shaped value —
see prompt injection above.

**What happens if the LLM provider is down or rate-limited?**
Depends on the error class, which is a declared property (`retryable`) on
each error type, not inferred at the call site: rate limits and timeouts
retry with exponential backoff up to `max_attempts`, then the job is
dead-lettered and the document marked `failed` with a human-readable reason
— never silently dropped, never retried forever. A bad API key fails
immediately rather than burning the retry budget on a certain failure. This
exact decision tree had zero test coverage until this build's final audit
found the gap and closed it with 9 tests against real job/document rows —
see [PROJECT_STATUS.md](PROJECT_STATUS.md) for that story in full.

**How would you improve extraction accuracy from here, concretely?**
In order: measure real-model accuracy first (can't improve what you haven't
measured), then mine `field_corrections` for systematically-wrong fields per
prompt version (the schema supports this query; nothing automated runs it
yet), then iterate prompts against the eval harness before touching
production traffic.

**What happens with a document type the system has never seen?**
Falls through to a generic schema with looser validation rather than
rejecting the upload outright — the classification cascade has a fallback,
and `document_types` is a database table (not hardcoded), so adding a fifth
type is a data change, not a code change. See
[DATABASE.md#document-configuration](DATABASE.md#document-configuration).

**Why did classification get its own cheap heuristic pass instead of always
asking the LLM?**
Most real documents have unambiguous tells (a receipt says "receipt"), and
a saturating heuristic score (`earned / (earned + HALF_EVIDENCE)`, not
divided by total possible weight) can commit confidently off one strong
signal without needing corroboration. The LLM is only asked when the
heuristic's best score is below threshold — cheaper, and per the evaluation
corpus's 100% classification accuracy, not a meaningful accuracy trade at
this step specifically. See [AI.md#classification-cheap-first](AI.md#classification-cheap-first).

### Evaluation

**What's your model's actual accuracy?**
83.5% normalized field accuracy against Gemini, real model calls, on the 11
of 20 evaluation documents that completed under a free-tier key (9
hard-failed, most likely rate limiting) — measured, not estimated, but on a
sample too small to call final. The rule-based baseline for comparison:
56.0% normalized field accuracy and 5.8% document success on the full
120-document synthetic corpus, failing completely (0%) on line items and
nested customer fields — exactly what a keyword/regex extractor should fail
at, which is the point of having it as a floor. See
[EVALUATION.md](EVALUATION.md).

**Why is the real LLM number only from 20 documents? Isn't that the most
important number?**
Yes — it's explicitly the first item in "what I'd build next" above. The
free-tier Gemini key this was run against caps at 20 requests/day, and 9 of
those 20 hard-failed rather than returning a scored answer, most likely from
that same rate limit. This project's standing rule is to report exactly what
was measured rather than estimate around a limitation, so the number stands
as "83.5% on 11 documents," not rounded up to a production claim. Every
piece of infrastructure needed to produce a larger number — corpus, metrics,
baseline, report format — is already built and already exercised against a
real provider; what's missing is a paid-tier key and a bigger run, not new
engineering.

**How do you know your evaluation corpus isn't just measuring an easy
synthetic dataset?**
It explicitly can't tell you real-world accuracy, and [EVALUATION.md](EVALUATION.md)
says so — synthetic documents can't surprise the system the way real ones
do (unusual vendor layouts, poor scans, handwriting). What it *can* support
honestly: relative comparison (baseline vs. model, prompt v1 vs. v2) and
regression detection, both measured against a fixed, reproducible seed. Any
number from it is an upper bound on real accuracy, not a prediction of it.

**What would you need to trust this in production?**
A real-model accuracy run against the synthetic corpus first (cheap,
available now), then a pilot against one design partner's actual documents
specifically to test the synthetic-to-real accuracy gap, then enough
production volume to see whether confidence calibration (currently only
validated against the fixture) holds for a real model's error patterns.

### Testing & engineering practice

**What's the most significant bug found during this build, and how was it
found?**
A cross-tenant queue-job-id collision: two organizations uploading
byte-identical content collided on `arq`'s global job-id key, and the second
organization's document silently never got a worker job — no error, no
visible symptom beyond "stuck at queued forever." Found by manual browser
testing (uploading the same test file as two different accounts), not by
any automated test, because nothing was exercising that specific cross-org
collision path. Fixed, and given a regression test precisely because it was
found the hard way. See [SECURITY.md#tenant-isolation](SECURITY.md#tenant-isolation).

**How do you test something that depends on a non-deterministic LLM?**
A deterministic fixture provider implements the exact same `LLMProvider`
interface a real model does, so the pipeline, its tests, and CI all run
against genuinely varying-per-document output with zero network calls and
zero flakiness from a third-party service being slow or down. It's a
first-class implementation of the interface, not a mock — see
[ADR-004](adr/004-llm-provider-abstraction.md).

**Walk me through your test isolation strategy.**
Real Postgres, not SQLite or a mocked session — each test runs inside a
transaction rolled back at teardown (SAVEPOINT-based), so 154 unit and 73
integration tests share one database without per-test cleanup or races. See
[DATABASE.md#test-isolation](DATABASE.md#test-isolation).

**What wasn't tested that you wish was, going into the final audit?**
The worker's retry/dead-letter/fail decision logic — the code whose own
module docstring describes the policy ("retry only what a retry can fix")
had zero coverage, because the existing pipeline tests bypass the job/retry
machinery entirely by calling the pipeline directly. Found and closed during
this build's own final self-audit, not left as a documented gap — 9 new
integration tests against real job/document rows, covering every branch of
that decision tree.

**Why real Postgres for integration tests instead of mocking the database?**
A test suite that mocks the database proves the mocks agree with each
other, not that the system works — this codebase's own testing philosophy,
stated in `tests/conftest.py`. Real constraint enforcement (unique
constraints, foreign keys, check constraints) is part of what's actually
being tested.

### Security

**How do you prevent one tenant from seeing another's data?**
Every tenant-scoped repository takes `organization_id` at construction and
injects it into every query itself — there's no code path that queries
tenant data without that filter, because the repository doesn't expose an
unscoped alternative. `organization_id` comes from the authenticated
principal, never from a URL or request body. See
[SECURITY.md#tenant-isolation](SECURITY.md#tenant-isolation).

**Why 404 instead of 403 for cross-tenant access?**
A 403 confirms the resource exists, which is itself a leak in a system
whose entire premise is tenant isolation. Indistinguishable-from-"doesn't
exist" is the more conservative, correct answer.

**What's the biggest unmitigated security risk right now?**
DNS rebinding on webhook URLs — SSRF protection resolves and checks the
hostname at registration time, not at delivery time, so a hostname that
resolves publicly at registration and privately later isn't caught. Recorded
as a known gap in the code itself, not glossed over. See
[SECURITY.md#known-gaps](SECURITY.md#known-gaps).

**How are secrets handled?**
Never in git (`.env` gitignored except `.env.example`, excluded from Docker
build contexts too), never logged (structured logs carry correlation ids,
never document contents or credentials), and production refuses to boot
with an insecure default (dev JWT secret, wildcard CORS, local storage
backend, fixture LLM provider) via an explicit startup check — see
[DEPLOYMENT.md#production-safety-gate](DEPLOYMENT.md#production-safety-gate).

### Scale & operations

**How would this scale to 10x the load?**
API scales horizontally without much thought (stateless, JWT sessions,
Redis-backed rate limiting so the limit is shared across replicas). The
worker is the part to watch — no autoscaling policy exists today, just a
manual instance count, tuned against `docflow_queue_depth` in Prometheus
once there's real backlog to tune against. See
[DEPLOYMENT.md#scaling-notes](DEPLOYMENT.md#scaling-notes).

**What breaks first at real scale?**
Almost certainly the database — one Postgres instance serves every tenant,
mitigated by org-scoped composite indexes rather than physical isolation.
Deliberately not over-built for a scale this product isn't at yet — see
[ADR-006](adr/006-multi-tenancy.md) for exactly when to revisit that.

**How do you deploy a schema change safely?**
Alembic migrations, checked into git, run explicitly (`alembic upgrade
head`) — no ORM auto-migrate in any environment past local dev.
Autogenerate is a starting point that gets reviewed before running,
specifically for anything Alembic can't infer safely (backfills, renames it
sees as drop+add). See [LOCAL_DEVELOPMENT.md#database-migrations](LOCAL_DEVELOPMENT.md#database-migrations).

### Self-critique

**What's the weakest part of this system as it stands?**
The real-LLM accuracy number is measured now, but on 11 completed documents
under a free-tier key — not the confident, large-sample number a production
decision should rest on. Everything downstream of it (cost projections, the
ROI calculator's defaults, the ADR trade-offs that assume "accurate enough")
is still provisional until a bigger run replaces it. Second weakest: the new
Playwright E2E suite (5 tests, critical user flows against the real stack)
isn't wired into CI yet — it exists and passes, but nothing stops it from
silently rotting the way the worker retry tests would have if they'd been
skipped at the time they were needed most.

**What would you do differently if starting over?**
Very little architecturally — the layering (provider abstraction,
repository-enforced tenancy, three-layer validation) held up well under its
own final audit, which is what that layering is for. The one process change:
write the worker retry-path tests *alongside* the retry logic itself,
not as a final-audit catch — it's exactly the kind of gap that's cheap to
close immediately and more expensive to rediscover later.

**If you had one more week, what would you build?**
A larger, paid-tier real-model accuracy run — the current one exists and is
real, but 11 completed documents isn't enough to retire the caveats around
it. Published in [EVALUATION.md](EVALUATION.md) with the same rigor as the
numbers already there, since that document's own standing rule is not to
round a quota-limited sample up into a production claim.
