"""arq worker entrypoint.

Run with: `arq docflow.worker.main.WorkerSettings`
"""

from __future__ import annotations

from typing import Any

import structlog

from docflow.config import get_settings
from docflow.observability.logging import configure_logging
from docflow.worker.queue import redis_settings
from docflow.worker.tasks import deliver_webhook, process_document

logger = structlog.get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.observability)
    problems = settings.validate_for_environment()
    if problems:
        raise RuntimeError("Refusing to start worker: " + "; ".join(problems))
    logger.info(
        "worker.started",
        provider=settings.llm.provider,
        model=settings.llm.model,
        concurrency=settings.processing.worker_concurrency,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    from docflow.db.session import dispose_engine
    from docflow.llm.registry import close_provider

    await close_provider()
    await dispose_engine()
    logger.info("worker.stopped")


_settings = get_settings()


class WorkerSettings:
    """arq configuration.

    `max_tries` is arq's redelivery budget and is deliberately one higher than the
    application's `max_attempts`: the application decides when to stop retrying
    (based on whether the error is retryable at all), and arq's limit is a backstop
    for the case where the worker dies before it can make that decision.

    `job_timeout` bounds a single document. Without it, one pathological PDF can
    occupy a worker slot indefinitely.
    """

    functions = [process_document, deliver_webhook]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()

    max_jobs = _settings.processing.worker_concurrency
    job_timeout = _settings.processing.job_timeout_seconds
    max_tries = _settings.processing.max_attempts + 1

    # Keep completed job results briefly so a duplicate enqueue within the window
    # is deduplicated by arq rather than starting a second run.
    keep_result = 3600
    # Retry delay is computed by the application (`queue.retry_delay_seconds`);
    # this is arq's own floor between redeliveries.
    retry_jobs = True
    health_check_interval = 30
