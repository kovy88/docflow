# Architecture decisions

Load-bearing decisions, recorded as ADRs in [docs/adr/](adr/) so the
*reasoning* survives even when the decision itself looks obvious in
hindsight — the alternatives considered and why they lost are the part that
actually goes stale in someone's memory first.

| ADR | Decision |
|---|---|
| [001](adr/001-fastapi.md) | FastAPI as the web framework |
| [002](adr/002-async-job-queue.md) | `arq` for the async job queue, over Celery/RQ/Dramatiq |
| [003](adr/003-postgresql.md) | PostgreSQL as the database, Supabase as the recommended managed host |
| [004](adr/004-llm-provider-abstraction.md) | A provider abstraction over the LLM, not a direct SDK dependency |
| [005](adr/005-human-in-the-loop.md) | Human review as a first-class pipeline stage, not a bolt-on |
| [006](adr/006-multi-tenancy.md) | Shared schema, repository-enforced multi-tenancy — not DB-per-tenant |
| [007](adr/007-storage-abstraction.md) | Object storage abstraction (local FS / S3), files never in Postgres |
| [008](adr/008-three-layer-validation.md) | Three separate validation layers instead of one |
| [009](adr/009-rule-based-baseline.md) | A rule-based baseline extractor for evaluation comparison |
| [010](adr/010-single-docker-image.md) | One Docker image for API and worker, not two |
| [011](adr/011-render-over-kubernetes.md) | Render + Docker Compose over Kubernetes |

## Format

Each ADR is short: context, decision, alternatives considered (with why they
lost, specifically — "we chose X" without "over Y, because Z" isn't a
decision record, it's an announcement), and consequences, including the ones
that cut against the decision. An ADR that only lists upside isn't honest
about the trade-off it made.
