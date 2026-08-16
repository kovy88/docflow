# ADR-006: Shared schema, repository-enforced multi-tenancy

## Context

Docflow is multi-tenant from the first customer: every organization's
documents, extractions, and settings must be inaccessible to every other
organization, with no acceptable failure mode where a bug leaks one tenant's
data to another. That property has to hold under normal development
velocity — routes and services get added over time, by people (or an agent)
who won't re-derive the tenancy model from first principles on every change.

## Decision

One shared schema (every tenant's rows live in the same tables, see
[DATABASE.md](../DATABASE.md)), with isolation enforced structurally: every
repository touching tenant data is constructed with an `organization_id` and
injects it into every query itself
(`OrgScopedRepository` — see [SECURITY.md](../SECURITY.md#tenant-isolation)).
A route handler cannot forget the filter, because the repository methods it
has access to don't expose an unscoped alternative.

## Alternatives considered

- **Database-per-tenant.** The strongest isolation guarantee available —
  a bug literally cannot cross a connection boundary. Rejected at this
  stage because it multiplies migration and connection-pool operational
  complexity by tenant count, which doesn't pay for itself before there's
  a compliance requirement (a specific customer needing physically separate
  storage) forcing the trade. Worth revisiting if that requirement appears;
  not worth building preemptively.
- **Schema-per-tenant** (one Postgres schema per organization, shared
  database). A middle ground with a real cost: running one Alembic
  migration means running it against every tenant schema, and connection
  pooling has to route by tenant. Rejected for the same reason as
  database-per-tenant — the operational cost is paid immediately, for an
  isolation improvement over the repository-enforced approach that isn't
  needed yet.
- **Rely on discipline** — every query includes `organization_id` because
  developers remember to add it, with no structural enforcement. Rejected
  outright: "remember to" is not a security control. This is precisely the
  failure mode `OrgScopedRepository` exists to make impossible rather than
  merely discouraged.

## Consequences

- Every repository class touching tenant data must extend
  `OrgScopedRepository` and take `organization_id` at construction — a
  convention that has to be followed for every new repository, though the
  base class makes deviating from it *more* work than following it, not
  less (there's no unscoped query method to reach for by accident).
- A single Postgres instance serves every tenant — a query-plan or
  performance problem for one large tenant is a shared-infrastructure
  concern for all tenants, unlike database-per-tenant's natural isolation
  there too. Mitigated by organization-scoped composite indexes (e.g.
  `ix_documents_org_status_created`) rather than architectural separation.
- Migrating to a stronger isolation model later (schema- or
  database-per-tenant) for a specific customer's compliance need is
  possible without an application-code rewrite, because the *application*
  already treats `organization_id` as the isolation boundary — only the
  connection/schema routing underneath `OrgScopedRepository` would need to
  change, not every call site.
- Cross-tenant access failures return `404`, not `403` (see
  [SECURITY.md](../SECURITY.md#tenant-isolation)) — a direct consequence of
  taking isolation seriously enough that even confirming another tenant's
  resource *exists* is treated as a leak.
