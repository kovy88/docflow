# ADR-007: Object storage abstraction, files never in Postgres

## Context

Uploaded documents are binary files up to 20MB (`DOCFLOW_UPLOAD_MAX_BYTES`),
and need to be readable by both the API (for downloads/previews) and the
worker (for text extraction/OCR), potentially running as separate,
horizontally-scaled processes that don't share a filesystem in production.
Local development and CI shouldn't require real cloud credentials just to
run the upload path.

## Decision

A `StorageBackend` interface (`put`, `get`, presigned/time-limited URLs)
with two implementations: local filesystem (development, single-server
Compose deployments) and S3-compatible (production — works unmodified
against AWS S3, Cloudflare R2, or Supabase Storage's S3 endpoint, since they
share the same API surface). Selected by `DOCFLOW_STORAGE_BACKEND`. Files
are written to storage *before* the database row referencing them is
inserted — see [ARCHITECTURE.md](../ARCHITECTURE.md#request-flow-upload-to-result)
for why that order, specifically.

## Alternatives considered

- **Store file bytes as a database column (`BYTEA`).** The simplest possible
  implementation — no second system to configure. Rejected: every backup,
  every unrelated query's shared cache, and every replication stream would
  carry megabytes of binary data that has nothing to do with the rows being
  queried 99% of the time. Postgres holds metadata and extracted results,
  which are the things actually queried, joined, and validated
  ([DATABASE.md](../DATABASE.md)) — the file itself is retrieved whole or
  not at all, which is exactly what object storage is for.
- **A single hardcoded S3 dependency, no local option.** Would force every
  contributor and CI run to have real cloud credentials just to test an
  upload, and forecloses the "run this with one command, no account
  required" demo experience the project's [README](../../README.md) leads
  with.
- **A network filesystem (NFS/EFS) shared between API and worker
  containers.** Works, but is infrastructure this project doesn't otherwise
  need — S3-compatible storage is a standard, well-understood primitive on
  every target deployment platform (Render, AWS, Supabase), whereas a shared
  network filesystem is its own operational surface for no capability gain
  over S3 at this scale.

## Consequences

- Local development and CI need zero cloud credentials for the full upload
  → process → download path — `docker compose up` uses the local backend
  by default.
- Production (`Settings.validate_for_environment`) explicitly **rejects**
  the local backend — see [DEPLOYMENT.md](../DEPLOYMENT.md#production-safety-gate)
  — because local storage isn't durable or shared across replicas; this is
  enforced by a startup check, not just documentation.
- Presigned URLs (time-limited downloads,
  `GET /documents/{id}/download`) have to be part of the interface both
  implementations satisfy, even though "presigning" is a meaningfully
  different operation for a local filesystem than for S3 — the local
  backend fakes it with its own time-limited token rather than exposing raw
  file paths.
- The API and worker never need direct filesystem access to each other's
  writes in production — they're stateless with respect to the local disk,
  which is what makes them independently horizontally scalable.
