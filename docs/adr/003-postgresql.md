# ADR-003: PostgreSQL, with Supabase as the recommended managed host

## Context

The data model ([DATABASE.md](../DATABASE.md)) is relational and
constraint-heavy by design: unique constraints carry real business rules
(content-addressed document dedup, one idempotency key per organization,
one extraction per document revision), foreign keys with `ondelete`
behavior matter (deleting an organization should cascade, not orphan rows),
and several tables store JSON payloads (`data`, `raw_model_output`) inside
otherwise strongly-typed, relationally-joined rows.

## Decision

PostgreSQL, accessed through SQLAlchemy 2.0's async engine. Supabase is the
recommended managed host for production, but nothing in the application code
is Supabase-specific — any standard `postgresql+asyncpg://` connection
string works, including Render's own managed Postgres.

## Alternatives considered

- **MySQL.** Workable, but weaker native JSON querying and no equivalent to
  Postgres's partial/expression indexes, which this schema doesn't currently
  use but constrains less if needed later. No advantage here that
  outweighs picking the less capable of two equally available options.
- **MongoDB / a document store.** Document-shaped data (the extraction
  `data` blob) is a fraction of what's stored — most of the schema
  (organizations, memberships, jobs, reviews, audit logs) is exactly the
  relational, foreign-keyed, constraint-enforced data a document store
  handles worse, not better. Postgres's `JSON`/`JSONB` columns already give
  the document-shaped fields a home without giving up relational integrity
  everywhere else — see `extractions.data` and `raw_model_output` in
  [DATABASE.md](../DATABASE.md).
- **SQLite.** Fine for tests in isolation, but no real concurrent-write
  story for a multi-tenant service with an API and a worker writing
  simultaneously, and integration tests specifically need to exercise real
  Postgres behavior (transaction isolation, constraint enforcement) rather
  than SQLite's looser semantics — see
  [LOCAL_DEVELOPMENT.md](../LOCAL_DEVELOPMENT.md#tests) for why integration
  tests run against a real Postgres, not SQLite, on principle.

## Consequences

- Every unique constraint and check constraint in [DATABASE.md](../DATABASE.md)
  is enforced by the database, not just application code — a bug in a
  service method can't silently create two documents with the same checksum
  in one organization, because the database itself refuses the second
  insert.
- Alembic migrations are the only supported schema-change path; there's no
  ORM "just create the tables" shortcut in any environment past local dev.
- Choosing Supabase specifically (over Render's own Postgres) is a hosting
  preference, not an architectural one — it's called out here so the
  distinction is explicit: this ADR is about the database engine, and a
  separate, smaller decision (recorded in
  [DEPLOYMENT.md](../DEPLOYMENT.md#backend-render)) is about who manages it
  in production.
