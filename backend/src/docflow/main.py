"""FastAPI application factory.

## Why FastAPI (ADR-001)

Three reasons that actually apply to this workload:

1. **Async-native.** Document processing is I/O bound — provider calls, storage,
   database. One process handles many concurrent documents without a thread per
   request.
2. **Pydantic is already the domain layer.** Extraction schemas, validation and API
   contracts share one model system. In Django or Flask the same shapes would be
   defined twice — once for the API, once for the extraction schema — and would
   drift.
3. **OpenAPI for free, and accurate.** The generated document *is* the type
   definitions, so it cannot go stale. That matters for a product whose API is part
   of the value proposition.

The trade-off, stated honestly: FastAPI gives no admin, no ORM, no auth, no
migrations. Django would have supplied all four. For a service whose core is a
processing pipeline rather than CRUD-over-models, the admin is worth little and the
async story is worth a lot.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from docflow.api.errors import register_exception_handlers
from docflow.api.routes import analytics, auth, documents, health, reviews, settings_routes
from docflow.config import Settings, get_settings
from docflow.observability.logging import configure_logging
from docflow.observability.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

logger = structlog.get_logger(__name__)

DESCRIPTION = """\
Turn unstructured business documents into validated structured data.

**How it works.** Upload a document and the API returns `202 Accepted` immediately;
processing happens asynchronously through an explicit pipeline — text extraction
(with OCR fallback), classification, LLM extraction against a configured schema,
three layers of validation, and per-field confidence scoring. Documents that fail
validation or score below the confidence threshold are routed to human review
rather than silently returned as correct.

**Authentication.** Send either a JWT access token from `/auth/login` or an API key
(`dfk_…`) as `Authorization: Bearer <credential>`.

**Idempotency.** Send an `Idempotency-Key` header on upload. Identical content is
deduplicated by content hash regardless, so a retry never creates a second billable
extraction.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.observability)

    problems = settings.validate_for_environment()
    if problems:
        # Refusing to start beats starting insecurely. A service running in
        # production with a default signing key is worse than one that is down,
        # because nobody notices.
        for problem in problems:
            logger.error("startup.invalid_configuration", problem=problem)
        raise RuntimeError(
            f"Refusing to start: {len(problems)} configuration problem(s). " + "; ".join(problems)
        )

    logger.info(
        "startup",
        environment=settings.environment,
        llm_provider=settings.llm.provider,
        llm_model=settings.llm.model,
        storage_backend=settings.storage.backend,
    )
    yield

    from docflow.db.session import dispose_engine
    from docflow.llm.registry import close_provider
    from docflow.worker.queue import close_pool

    await close_provider()
    await close_pool()
    await dispose_engine()
    logger.info("shutdown.complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Docflow API",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs are a feature for an API-first product, and they expose
        # no data — every endpoint still requires authentication.
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "Docflow", "url": "https://github.com/"},
        license_info={"name": "MIT"},
    )

    # Middleware runs in reverse registration order, so the last registered is the
    # outermost. Request context must be outermost: everything else needs the
    # correlation id, including the rate limiter's rejection response.
    app.add_middleware(RateLimitMiddleware, settings=settings.security)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        # An explicit allowlist, never `*`. With credentials enabled, a wildcard
        # origin would let any site read authenticated responses.
        allow_origins=settings.security.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Organization-Id"],
        expose_headers=["X-Request-Id", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
        max_age=600,
    )

    register_exception_handlers(app)

    app.include_router(health.router)

    prefix = settings.api_v1_prefix
    app.include_router(auth.router, prefix=prefix)
    app.include_router(documents.router, prefix=prefix)
    app.include_router(reviews.router, prefix=prefix)
    app.include_router(analytics.router, prefix=prefix)
    app.include_router(settings_routes.router, prefix=prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": "Docflow API",
                "version": "0.1.0",
                "docs": "/docs",
                "health": "/health",
            }
        )

    return app


app = create_app()


def main() -> None:  # pragma: no cover
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "docflow.main:app",
        host="0.0.0.0",  # noqa: S104 — containers bind all interfaces by design
        port=8000,
        reload=settings.environment == "local",
        log_config=None,  # structlog owns logging
    )


if __name__ == "__main__":  # pragma: no cover
    main()
