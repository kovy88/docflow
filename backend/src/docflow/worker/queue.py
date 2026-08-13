"""Queue interface (arq).

## Why arq — ADR-002 in short

The pipeline is `async` end to end: the LLM call, storage and the database all use
asyncio. arq is asyncio-native, so a worker running eight documents concurrently
spends its time waiting on eight sockets in one event loop instead of holding eight
threads or processes.

The alternatives, and why not:

* **Celery** is the default answer and the wrong one here. It is thread/process
  based, its asyncio support is bolted on, and it brings a large surface area
  (result backends, routing, chords) that this workload has no use for. Its
  strength — mature, battle-tested, huge ecosystem — is a real argument, and if the
  team already ran Celery it would win on familiarity alone.
* **RQ** is simple but synchronous, so every concurrent document costs a process.
* **Dramatiq** is good, but async support is secondary and it wants its own broker
  abstraction.
* **A database queue** (`SELECT ... FOR UPDATE SKIP LOCKED`) would remove Redis
  entirely and is genuinely tempting at this scale. Rejected because Redis is
  already present for rate limiting and caching, and arq gives retries, scheduling
  and dead-lettering that would otherwise be hand-written.

arq's cost is a smaller ecosystem and less operational literature. Accepted, and
mitigated by the fact that the pipeline itself is queue-agnostic — swapping the
queue means reimplementing this module and nothing else.

## Idempotency at the queue layer

`_job_id` is derived from the document's idempotency key. arq refuses to enqueue a
job whose id already exists within the result-retention window, so a duplicated
enqueue is dropped by the queue itself rather than deduplicated by us later.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from docflow.config import get_settings

logger = structlog.get_logger(__name__)

PROCESS_DOCUMENT_TASK = "process_document"
DELIVER_WEBHOOK_TASK = "deliver_webhook"

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(str(settings.redis.url))


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
    _pool = None


async def enqueue_processing(
    *,
    job_id: uuid.UUID,
    organization_id: uuid.UUID,
    job_key: str,
    request_id: str | None = None,
) -> str | None:
    """Enqueue document processing. Returns the queue job id, or None if deduped."""
    pool = await get_pool()
    job = await pool.enqueue_job(
        PROCESS_DOCUMENT_TASK,
        str(job_id),
        str(organization_id),
        request_id,
        _job_id=job_key,
    )
    if job is None:
        logger.info("queue.duplicate_enqueue_ignored", job_id=str(job_id), job_key=job_key)
        return None
    logger.info("queue.enqueued", job_id=str(job_id), queue_job_id=job.job_id)
    return job.job_id


async def enqueue_webhook(
    *, delivery_id: uuid.UUID, organization_id: uuid.UUID, defer_seconds: int = 0
) -> str | None:
    pool = await get_pool()
    job = await pool.enqueue_job(
        DELIVER_WEBHOOK_TASK,
        str(delivery_id),
        str(organization_id),
        _job_id=f"webhook:{delivery_id}",
        _defer_by=defer_seconds or None,
    )
    return job.job_id if job else None


async def queue_health() -> dict[str, Any]:
    """Depth and worker liveness, for `/readiness` and the metrics endpoint."""
    try:
        pool = await get_pool()
        queued = await pool.zcard("arq:queue")
        return {"healthy": True, "queued": int(queued)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue.health_check_failed", error=type(exc).__name__)
        return {"healthy": False, "queued": None}


def retry_delay_seconds(attempt: int, *, base: float = 2.0, cap: float = 60.0) -> float:
    """Exponential backoff with full jitter.

    Jitter is not cosmetic. A provider rate limit typically trips many jobs at
    once; without jitter they all retry at exactly `base * 2^n` and re-trip the
    limit together, forever. Full jitter (`uniform(0, computed)`) spreads them out.
    """
    import random

    computed = min(cap, base * (2 ** max(0, attempt - 1)))
    return random.uniform(0, computed)  # noqa: S311 — jitter, not cryptography
