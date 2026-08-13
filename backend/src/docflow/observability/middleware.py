"""HTTP middleware: correlation, access logging, security headers, rate limiting."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from docflow.config import SecuritySettings
from docflow.observability.logging import bind_context, clear_context
from docflow.observability.metrics import record_request

logger = structlog.get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind logging context, emit one access log line."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Honour an inbound request id so a trace survives across services (the
        # frontend, a reverse proxy, n8n), and generate one otherwise.
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        clear_context()
        bind_context(request_id=request_id, path=request.url.path, method=request.method)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception("http.request_failed", duration_ms=duration_ms)
            record_request(request.method, _route_of(request), 500, duration_ms / 1000)
            raise
        finally:
            clear_context()

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[REQUEST_ID_HEADER] = request_id

        route = _route_of(request)
        record_request(request.method, route, response.status_code, duration_ms / 1000)

        # Health probes fire every few seconds; logging them buries real traffic.
        if not request.url.path.startswith(("/health", "/readiness", "/metrics")):
            logger.info(
                "http.request",
                status=response.status_code,
                duration_ms=duration_ms,
                route=route,
                organization_id=_org_of(request),
            )
        return response


def _route_of(request: Request) -> str:
    """The route *template*, not the concrete path.

    `/documents/{document_id}` rather than `/documents/9f3c…`. Using the concrete
    path would create one metric series per document — unbounded cardinality that
    will take down a Prometheus server.
    """
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


def _org_of(request: Request) -> str | None:
    principal = getattr(request.state, "principal", None)
    return str(principal.organization_id) if principal else None


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline security headers.

    The API serves JSON, not HTML, so the CSP is maximally restrictive: this origin
    should never load a script, frame anything, or be framed. That neutralises the
    class of attacks where an API error page reflects input into a browser.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        # Only meaningful over HTTPS; harmless otherwise, and prevents a downgrade
        # once a deployment is behind TLS.
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window rate limiting, keyed per organization where known.

    Backed by Redis so the limit is shared across API replicas. A per-process
    counter would multiply the effective limit by the replica count, which makes
    the limit meaningless exactly when it matters.

    Fixed window (not sliding) is a deliberate simplification: it allows a burst of
    up to 2× the limit across a window boundary. That is acceptable here because
    the limit exists to stop runaway clients and accidental loops, not to meter
    billing — quotas do that, in the database, transactionally.

    **Fails open.** If Redis is unavailable, requests are allowed. Rate limiting is
    a protection, not a correctness requirement; taking the whole API down because
    the limiter is unreachable trades a small problem for a large one.
    """

    def __init__(self, app: object, settings: SecuritySettings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self._settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path.startswith(("/health", "/readiness", "/metrics", "/docs", "/openapi")):
            return await call_next(request)

        is_upload = request.method == "POST" and request.url.path.endswith("/documents")
        limit = (
            self._settings.rate_limit_upload_per_minute
            if is_upload
            else self._settings.rate_limit_default_per_minute
        )

        identity = self._identity(request)
        allowed, remaining, reset_in = await self._check(identity, limit, is_upload)

        if not allowed:
            logger.warning("http.rate_limited", identity=identity, limit=limit)
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "category": "user",
                        "message": f"Rate limit of {limit} requests per minute exceeded",
                        "detail": {"retry_after_seconds": reset_in},
                    },
                    "request_id": getattr(request.state, "request_id", ""),
                },
                headers={
                    "Retry-After": str(reset_in),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        return response

    def _identity(self, request: Request) -> str:
        """Prefer the auth credential over the client IP.

        Many SMB customers sit behind one NAT address; limiting by IP would make
        one busy user throttle their colleagues. The credential is also harder to
        rotate than an IP.
        """
        auth = request.headers.get("authorization", "")
        if auth:
            import hashlib

            return "auth:" + hashlib.sha256(auth.encode()).hexdigest()[:24]
        client = request.client
        return f"ip:{client.host}" if client else "ip:unknown"

    async def _check(self, identity: str, limit: int, is_upload: bool) -> tuple[bool, int, int]:
        from docflow.worker.queue import get_pool

        window = int(time.time() // 60)
        key = f"ratelimit:{'upload' if is_upload else 'default'}:{identity}:{window}"
        try:
            redis = await get_pool()
            count = await redis.incr(key)
            if count == 1:
                # Expire slightly after the window so a clock skew between
                # replicas cannot resurrect a stale counter.
                await redis.expire(key, 70)
            reset_in = 60 - int(time.time() % 60)
            return count <= limit, limit - int(count), reset_in
        except Exception:
            logger.warning("ratelimit.backend_unavailable", identity=identity)
            return True, limit, 60
