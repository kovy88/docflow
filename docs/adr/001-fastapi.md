# ADR-001: FastAPI as the web framework

## Context

The API needs async I/O throughout (every request that touches the
database, storage, or an LLM provider is I/O-bound), request/response
validation against typed schemas, and OpenAPI documentation that stays
accurate without manual upkeep — this project's [API.md](../API.md)
explicitly defers to the generated `/docs` as the source of truth rather
than hand-maintaining a field list, which only works if the framework
generates that schema from the same code that serves the request.

## Decision

FastAPI, with Pydantic v2 models for every request and response body.

## Alternatives considered

- **Django + DRF.** Mature, batteries-included, but async support is
  bolted onto a sync-first ORM and request lifecycle; fighting the
  framework's sync defaults for an I/O-bound service built around async
  SQLAlchemy would cost more than it saves. Django's admin and batteries
  are aimed at a different shape of app than a headless API with a
  separate frontend.
- **Flask.** No native async request handling, no built-in validation or
  OpenAPI generation — all three would need to be assembled from
  extensions, arriving at something FastAPI already is.
- **Starlette directly** (what FastAPI is built on). Would mean hand-writing
  the validation and OpenAPI-generation layer FastAPI already provides.
  There's no scenario in this project where bypassing FastAPI's routing and
  validation to talk to Starlette directly would be worth losing them.

## Consequences

- Every request/response schema is a Pydantic model, which is also what
  they'd need to be for internal validation anyway — no separate
  serialization layer.
- Interactive docs (`/docs`) and the OpenAPI schema (`/openapi.json`) are
  generated, not maintained — they can't drift from the routes the way a
  hand-written API doc can.
- FastAPI's dependency-injection system (`Depends`) is what makes
  `SessionDep`, `CurrentPrincipal`, `SettingsDep` etc. composable rather than
  re-derived in every route — this is a real ergonomic win, not just
  boilerplate reduction, because it's the same mechanism that keeps
  `organization_id` scoping consistent (see
  [ADR-006](006-multi-tenancy.md)).
- Ties the project to Python's async ecosystem throughout — the LLM
  providers, the storage backends, and SQLAlchemy's async engine all have to
  be async-native rather than wrapped, which they are.
