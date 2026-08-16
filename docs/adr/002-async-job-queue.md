# ADR-002: `arq` for the async job queue

## Context

Document processing (OCR, one or more LLM calls, validation) takes
seconds, sometimes many seconds — too long to hold an HTTP request open
for (see [ARCHITECTURE.md](../ARCHITECTURE.md#why-async-processing-not-synchronous-in-request)).
Upload needs to enqueue work and return immediately; a separate worker
process needs to pick it up, with retries for transient failures and no
retries for deterministic ones.

## Decision

`arq` — an asyncio-native job queue backed by Redis.

## Alternatives considered

- **Celery.** The default choice for Python task queues, and reasonable —
  but it's fundamentally a sync-worker-with-thread/event-loop-bolted-on
  design (or eventlet/gevent monkey-patching for async). Every collaborator
  the pipeline touches (SQLAlchemy's async engine, `httpx.AsyncClient` for
  LLM calls, async storage backends) is already async; running them under
  Celery means either sync wrappers around async code or monkey-patching,
  both of which are complexity this project doesn't need to accept for a
  queue with one workload.
- **RQ (Redis Queue).** Simple and Redis-backed like `arq`, but sync-only —
  same mismatch as Celery, without Celery's broader ecosystem to justify it.
- **Dramatiq.** Reasonable async support via a `gevent`/asyncio bridge
  depending on version, smaller ecosystem than Celery, no clear advantage
  over `arq` for a project that's already all-in on asyncio and already
  running Redis for rate limiting.
- **A hand-rolled Postgres-backed queue** (`SELECT ... FOR UPDATE SKIP
  LOCKED`). Avoids a second infrastructure dependency, and it's a legitimate
  pattern — but Redis is already required for rate limiting
  ([SECURITY.md](../SECURITY.md#rate-limiting)), so the "avoid a dependency"
  argument doesn't hold, and a hand-rolled queue means building and testing
  retry/backoff/dead-lettering that `arq` already provides.

## Consequences

- One more infrastructure dependency (Redis) — already justified
  independently by rate limiting, so this doesn't add one on its own.
- `arq`'s job-id uniqueness is a **global** Redis key, not scoped to
  anything. Getting this wrong is exactly what caused the cross-tenant queue
  collision bug documented in [SECURITY.md](../SECURITY.md#tenant-isolation)
  — the fix (hash the organization id into the job id) is a direct
  consequence of this choice, not something a per-tenant-namespaced queue
  technology would have required thinking about.
- Retry policy is applied at the application layer
  (`DocflowError.retryable`), not inferred from exception type inside
  `arq` — `arq`'s own `max_tries` is a backstop one higher than the
  application's own limit, for the case where a worker dies mid-decision.
  This split is a direct consequence of wanting "is this retryable" to be a
  property declared once per error class rather than judgment scattered
  across handlers, and `arq` supports that split cleanly (raise to retry,
  return to not).
- No separate message broker beyond Redis — webhook delivery is queued
  through the same `arq` instance as document processing, since it's the
  same shape of workload (background job, needs retry) rather than a
  different category needing its own bus.
