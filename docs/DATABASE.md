# Database

PostgreSQL, accessed through SQLAlchemy 2.0's async ORM. One baseline Alembic
migration (`backend/alembic/versions/5bedce49434f_initial_schema.py`) creates
all 18 tables — this project didn't accumulate incremental migration history
because the schema was designed up front rather than evolved through the
build; see [ADR-003](adr/003-postgresql.md) for why Postgres itself, and
[DECISIONS.md](DECISIONS.md) for the ADR index.

## Multi-tenancy: shared schema, enforced at the repository layer

Every tenant-owned table carries an `organization_id` foreign key
(`ondelete="CASCADE"` — delete an organization, its data goes with it, no
orphaned rows). That column is never taken from a request path or body. It's
injected by `OrgScopedRepository`, which every repository touching tenant
data inherits from and which adds `WHERE organization_id = :org_id` to every
query at construction time, not as something callers remember to pass. A
route handler cannot forget the filter, because it never has the option to
supply one — see [ADR-006](adr/006-multi-tenancy.md) and
[SECURITY.md](SECURITY.md) for the full argument, including why cross-tenant
lookups return `404` rather than `403`.

## Tables

### Identity & access
- `organizations` — tenant root. `plan`, `monthly_document_quota`.
- `users` — email/password (Argon2id), independent of organization; a user
  can belong to more than one org via `memberships`.
- `memberships` — user × organization × role. `uq_memberships_user_org`
  prevents duplicate membership rows; `ix_memberships_org_role` supports
  "who are the admins of org X" without a table scan.
- `api_keys` — machine access. Only a SHA-256 digest is stored (`hash_api_key`);
  the plaintext key is shown once, at creation, and cannot be recovered.

### Document configuration
- `document_types` — the schema registry, in the database rather than only in
  code: `key` + `version`, JSON Schema, validation rule references. Lets an
  organization eventually customize or extend a document type without a
  deploy. `uq_document_types_org_key_ver` allows the same `key` (e.g.
  `invoice`) to exist as a global default (`organization_id IS NULL`) and as
  a per-org override.
- `prompt_versions` — every prompt template that's ever been used to extract,
  keyed by `(key, version)`, immutable once created. An extraction records
  which one produced it (below) — the point is reproducibility, not just
  history.

### Documents & processing
- `documents` — one row per uploaded file. `checksum_sha256` +
  `uq_documents_org_checksum` is the content-addressed dedup: the same bytes
  cannot become two documents in one organization no matter how the client
  behaves. `storage_key` points into object storage; the file itself is
  never in this table. `ix_documents_org_status_created` supports the
  dashboard's "queued/processing/needs_review documents for this org, newest
  first" query directly from an index.
- `processing_jobs` — one row per processing attempt-group.
  `uq_jobs_org_idempotency` backs the `Idempotency-Key` header contract: a
  retried request with the same key returns the existing job instead of
  starting a second one.
- `processing_steps` — one row per pipeline stage per job (11 stages × 1 job,
  typically), each with its own status/timing/error. This is what the
  frontend's processing timeline renders — not a synthetic progress bar, the
  actual stage outcomes.

### Results
- `extractions` — one row per processing *attempt's result*, append-only:
  reprocessing creates a new row with an incremented `revision` and flips the
  previous row's `is_current` to false rather than overwriting it
  (`uq_extractions_document_revision`, `ix_extractions_doc_current`). A
  dedicated **reproducibility block** on every row —
  `extractor, provider, model, model_version, prompt_key, prompt_version,
  document_type_key, schema_version, pipeline_version` — means any past
  result can be traced to exactly what produced it: which model, which
  prompt, which schema version, which code. `data` holds the current field
  values (including human corrections, applied in place); `cost_usd` is
  `NUMERIC(12,6)`, not `FLOAT`, because it's money summed across millions of
  rows and float drift there is a real bug, not a rounding curiosity.
- `extraction_fields` — per-field metadata the review UI needs: value,
  `confidence`, `confidence_band`, whether it was corrected, why it needs
  review. `uq_fields_extraction_path` — one row per field path per
  extraction.
- `validation_issues` — one row per rule failure/warning from the
  semantic/business validation layers, with severity and the rule id that
  raised it.

### Human review
- `reviews` — one row per approve/reject decision, linking the extraction,
  the document, and the reviewing user.
- `field_corrections` — one row per field a human actually changed.
  `ix_corrections_prompt` (indexed on `prompt_version, field_path`) exists
  specifically so a future prompt-improvement feedback loop can ask "which
  fields does prompt v3 get wrong most often" directly from the database —
  built, not yet consumed by anything automated.

### Operational
- `usage_records` — per-document cost/token accounting, the source for
  billing and the dashboard's cost widgets.
- `audit_logs` — actor, action, resource, metadata for every state-changing
  operation (uploads, approvals, key creation, webhook registration).
  Immutable by convention (nothing in the codebase updates or deletes an
  audit row).
- `webhook_endpoints` / `webhook_deliveries` — registered customer endpoints
  and the delivery attempts against them (status, response code, retry
  count) — see [API.md](API.md#webhooks) for the delivery contract.

## Why append-only for extractions instead of updating in place

A `reprocess` on a document that already has a result doesn't overwrite it.
It inserts a new `extractions` row and marks the old one `is_current = false`.
Three reasons this is worth the extra rows:

1. **A correction shouldn't disappear if someone reprocesses.** Human edits
   live in `data` on the extraction row they were made against; if
   reprocessing overwrote that row, a correction could be silently lost to a
   retried pipeline run.
2. **The reproducibility block means old results stay interpretable.** "Why
   did this look different last week" has an answer — a different
   `prompt_version` or `schema_version` — instead of being unanswerable
   because the old data is gone.
3. **It's what the evaluation and future feedback-loop work actually need.**
   `field_corrections` joined against the extraction's `prompt_version` is
   how "is the new prompt better" gets measured — that join is only possible
   because history isn't destroyed.

## Test isolation

Integration tests run against a real Postgres (via `docker-compose.yml`'s
`postgres` service, or CI's Postgres service container) — not a mocked
session and not SQLite. Each test runs inside a transaction that's rolled
back at teardown (`SAVEPOINT`-based), so 73 integration tests share one
database without cleaning up after each other or racing on shared state.
