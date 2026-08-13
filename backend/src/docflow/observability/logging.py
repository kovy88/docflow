"""Structured logging.

Every log line is JSON with a stable field set, and every line emitted while
handling a request or job carries `request_id`, `job_id`, `document_id` and
`organization_id` without the call site passing them. That correlation is what
turns "a customer says document X failed" into one query.

Correlation uses `structlog.contextvars`, which is task-local under asyncio — two
documents processed concurrently in the same worker cannot mix up their context.

## What is never logged

Document text, extracted field values, passwords, tokens, API keys. A processing
log that includes an invoice total is a log aggregator holding customer financial
data, with a different retention policy and access model than the database. The
`redact` processor enforces this for a known key list; the real discipline is that
call sites log counts, ids and durations rather than values.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from docflow.config import ObservabilitySettings

# Keys whose values are replaced with a placeholder wherever they appear.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "hashed_password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "jwt_secret",
        "authorization",
        "document_text",
        "text",
        "raw_text",
        "content",
        "extracted_data",
        "anthropic_api_key",
        "openai_api_key",
        "secret_access_key",
        "value",
    }
)
REDACTED = "[redacted]"


def redact_sensitive(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def drop_none(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove null values so log lines stay readable and cheap to index."""
    return {k: v for k, v in event_dict.items() if v is not None}


def configure_logging(settings: ObservabilitySettings) -> None:
    """Route structlog and stdlib logging through one formatter.

    Uses `ProcessorFormatter` rather than structlog's standalone `PrintLogger`, so
    that uvicorn, SQLAlchemy, arq and boto3 — none of which know about structlog —
    land in the same JSON stream with the same fields. A deployment with two log
    formats in one stream is a deployment whose logs cannot be queried.

    (`add_logger_name` also requires a stdlib-backed logger; with `PrintLogger` it
    raises `AttributeError` on the first log line, which is how this was found.)
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Run on every record, whether it originated in structlog or stdlib.
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        redact_sensitive,
        drop_none,
    ]

    structlog.configure(
        processors=[
            *shared,
            # Hands off to the stdlib formatter below instead of rendering here.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied only to records from stdlib loggers, which arrive as plain
        # strings and need the structlog field set added.
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "arq", "sqlalchemy.engine"):
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers = []
        stdlib_logger.propagate = True

    # Our middleware emits access logs with correlation ids attached; uvicorn's
    # version would be a duplicate line without them.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)


def bind_context(**values: Any) -> None:
    bind_contextvars(**{k: v for k, v in values.items() if v is not None})


def clear_context() -> None:
    clear_contextvars()


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
