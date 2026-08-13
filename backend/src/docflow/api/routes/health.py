"""Health and readiness.

The distinction matters operationally and is frequently got wrong:

* **`/health` is liveness.** "Is this process alive?" It checks nothing external
  and never fails because a dependency is down. A liveness probe that checks the
  database restarts every API pod during a database blip — turning a recoverable
  incident into an outage.

* **`/readiness` is traffic-worthiness.** "Should this instance receive requests?"
  It genuinely checks the database, Redis, storage and the LLM provider. A failing
  readiness check removes the instance from the load balancer without killing it.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Response, status

from docflow.api.deps import SettingsDep
from docflow.api.schemas import HealthResponse, ReadinessResponse
from docflow.observability import metrics

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["system"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok", version=VERSION, environment=settings.environment
    )


@router.get("/readiness", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(settings: SettingsDep, response: Response) -> ReadinessResponse:
    checks: dict[str, bool] = {}
    detail: dict[str, str] = {}

    checks["database"] = await _check_database(detail)
    checks["redis"] = await _check_redis(detail)
    checks["storage"] = await _check_storage(detail)
    # The provider is checked but is not gating: a provider outage should stop new
    # documents from being *processed*, not stop the API from serving the dashboard,
    # the review queue, or existing results.
    checks["llm_provider"] = await _check_llm(detail)

    gating = ("database", "redis", "storage")
    ready = all(checks[name] for name in gating)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning("readiness.failed", checks=checks)

    return ReadinessResponse(
        status="ready" if ready else "not_ready", checks=checks, detail=detail
    )


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def prometheus_metrics(settings: SettingsDep) -> Response:
    if not settings.observability.metrics_enabled:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    await _refresh_queue_depth()
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)


async def _check_database(detail: dict[str, str]) -> bool:
    from sqlalchemy import text

    from docflow.db.session import get_sessionmaker

    try:
        factory = get_sessionmaker()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        detail["database"] = type(exc).__name__
        return False
    return True


async def _check_redis(detail: dict[str, str]) -> bool:
    from docflow.worker.queue import get_pool

    try:
        pool = await get_pool()
        await pool.ping()
    except Exception as exc:  # noqa: BLE001
        detail["redis"] = type(exc).__name__
        return False
    return True


async def _check_storage(detail: dict[str, str]) -> bool:
    from docflow.storage import get_storage

    try:
        return await get_storage().health_check()
    except Exception as exc:  # noqa: BLE001
        detail["storage"] = type(exc).__name__
        return False


async def _check_llm(detail: dict[str, str]) -> bool:
    from docflow.llm.registry import get_provider

    try:
        return await get_provider().health_check()
    except Exception as exc:  # noqa: BLE001
        detail["llm_provider"] = type(exc).__name__
        return False


async def _refresh_queue_depth() -> None:
    from docflow.worker.queue import queue_health

    health_info = await queue_health()
    if health_info.get("queued") is not None:
        metrics.queue_depth.set(health_info["queued"])
