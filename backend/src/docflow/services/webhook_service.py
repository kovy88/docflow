"""Outbound webhooks.

The integration story: a document finishes, an external system hears about it
without polling. That is what makes this a component in a workflow rather than an
island — see `integrations/n8n/` for a worked example.

## Delivery guarantees

At-least-once, with signed payloads and bounded retries. Receivers must be
idempotent, and the `X-Docflow-Delivery-Id` header gives them the key to dedupe on.

## Signing

`X-Docflow-Signature: sha256=<hex>` over `{timestamp}.{body}`, HMAC-SHA256 with the
endpoint's secret. The timestamp is inside the signed material, so a captured
payload cannot be replayed indefinitely — receivers reject anything older than a
few minutes.

## SSRF protection

Webhook URLs are user-supplied and cause the server to make outbound requests: a
textbook SSRF vector. URLs resolving to private, loopback or link-local addresses
are rejected — including cloud metadata endpoints (169.254.169.254), which is how
an attacker turns a webhook feature into cloud credential theft.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.repositories import WebhookRepository
from docflow.domain.enums import DeliveryStatus, WebhookEvent
from docflow.domain.errors import ValidationRequestError

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS = 5
TIMEOUT_SECONDS = 10.0
# Enough for a receiver to identify the document and fetch details; deliberately
# not the full extraction, so a webhook cannot become a data-exfiltration channel
# to a URL someone typed into a settings form.
MAX_PAYLOAD_BYTES = 64 * 1024


class WebhookService:
    def __init__(self, session: AsyncSession, *, organization_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = organization_id
        self.repo = WebhookRepository(session, organization_id)

    # -------------------------------------------------------------- management

    async def register(
        self, *, url: str, description: str | None, events: list[str]
    ) -> tuple[Any, str]:
        validate_webhook_url(url)
        for event in events:
            if event not in {e.value for e in WebhookEvent}:
                raise ValidationRequestError(
                    f"Unknown event {event!r}",
                    detail={"supported": [e.value for e in WebhookEvent]},
                )

        secret = f"whsec_{secrets.token_urlsafe(32)}"
        endpoint = await self.repo.create(
            url=url, description=description, secret=secret, events=events
        )
        return endpoint, secret

    # ---------------------------------------------------------------- dispatch

    async def dispatch(self, event: WebhookEvent, payload: dict[str, Any]) -> int:
        """Queue a delivery for every subscribed endpoint. Returns the count.

        Deliveries are rows, not in-process HTTP calls: the caller (a worker
        finishing a document) must not block on a customer's slow endpoint, and a
        delivery that fails needs somewhere to live between retries.
        """
        endpoints = await self.repo.list_active(event.value)
        if not endpoints:
            return 0

        body = {
            "event": event.value,
            "organization_id": str(self._org_id),
            "occurred_at": dt.datetime.now(dt.UTC).isoformat(),
            "data": payload,
        }
        for endpoint in endpoints:
            delivery = self.repo.queue_delivery(
                endpoint_id=endpoint.id,
                event=event.value,
                payload=body,
                status=DeliveryStatus.PENDING.value,
            )
            await self._session.flush()
            from docflow.worker.queue import enqueue_webhook

            await enqueue_webhook(delivery_id=delivery.id, organization_id=self._org_id)

        return len(endpoints)

    async def deliver(  # noqa: PLR0911 — one return per delivery outcome
        self, delivery_id: uuid.UUID, *, attempt: int = 1
    ) -> dict[str, Any]:
        from docflow.db.models import WebhookDelivery, WebhookEndpoint

        delivery = await self._session.get(WebhookDelivery, delivery_id)
        if delivery is None or delivery.organization_id != self._org_id:
            return {"status": "not_found"}
        if delivery.status == DeliveryStatus.DELIVERED.value:
            return {"status": "already_delivered"}

        endpoint = await self._session.get(WebhookEndpoint, delivery.endpoint_id)
        if endpoint is None or not endpoint.is_active:
            delivery.status = DeliveryStatus.EXHAUSTED.value
            return {"status": "endpoint_inactive"}

        body = json.dumps(delivery.payload, separators=(",", ":"), default=str)
        if len(body) > MAX_PAYLOAD_BYTES:
            delivery.status = DeliveryStatus.EXHAUSTED.value
            delivery.response_body = "payload too large"
            return {"status": "payload_too_large"}

        timestamp = str(int(dt.datetime.now(dt.UTC).timestamp()))
        signature = sign_payload(endpoint.secret, timestamp, body)

        delivery.attempts = attempt
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS,
                # Never follow redirects: a 302 to an internal address would defeat
                # the URL validation done at registration time.
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    endpoint.url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Docflow-Webhooks/1.0",
                        "X-Docflow-Event": delivery.event,
                        "X-Docflow-Delivery-Id": str(delivery.id),
                        "X-Docflow-Timestamp": timestamp,
                        "X-Docflow-Signature": f"sha256={signature}",
                    },
                )
        except Exception as exc:
            return self._record_failure(delivery, endpoint, attempt, str(type(exc).__name__))

        delivery.response_code = response.status_code
        delivery.response_body = response.text[:1000]

        if 200 <= response.status_code < 300:
            delivery.status = DeliveryStatus.DELIVERED.value
            delivery.delivered_at = dt.datetime.now(dt.UTC)
            endpoint.last_success_at = delivery.delivered_at
            endpoint.consecutive_failures = 0
            logger.info("webhook.delivered", delivery_id=str(delivery.id))
            return {"status": "delivered", "code": response.status_code}

        return self._record_failure(delivery, endpoint, attempt, f"http_{response.status_code}")

    def _record_failure(
        self, delivery: Any, endpoint: Any, attempt: int, reason: str
    ) -> dict[str, Any]:
        endpoint.last_failure_at = dt.datetime.now(dt.UTC)
        endpoint.consecutive_failures += 1

        if attempt >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.EXHAUSTED.value
            # Auto-disable a persistently dead endpoint. Without this, one
            # abandoned URL generates retry traffic forever.
            if endpoint.consecutive_failures >= 20:
                endpoint.is_active = False
                logger.warning("webhook.endpoint_disabled", endpoint_id=str(endpoint.id))
            logger.warning("webhook.exhausted", delivery_id=str(delivery.id), reason=reason)
            return {"status": "exhausted", "reason": reason}

        delivery.status = DeliveryStatus.FAILED.value
        from docflow.worker.queue import retry_delay_seconds

        delay = retry_delay_seconds(attempt, base=5.0, cap=600.0)
        delivery.next_retry_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=delay)
        logger.info(
            "webhook.retry_scheduled",
            delivery_id=str(delivery.id),
            attempt=attempt,
            delay_seconds=round(delay, 1),
        )
        return {"status": "retry", "reason": reason, "delay_seconds": delay}


def sign_payload(secret: str, timestamp: str, body: str) -> str:
    """HMAC-SHA256 over `{timestamp}.{body}`.

    Including the timestamp in the signed material is what makes replay detection
    possible: a receiver can reject an old timestamp knowing the attacker cannot
    re-sign a fresh one.
    """
    message = f"{timestamp}.{body}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str, timestamp: str, body: str, signature: str, *, tolerance_seconds: int = 300
) -> bool:
    """Reference verifier — mirrored in the docs so integrators can copy it."""
    try:
        age = abs(int(dt.datetime.now(dt.UTC).timestamp()) - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > tolerance_seconds:
        return False
    expected = sign_payload(secret, timestamp, body)
    provided = signature.removeprefix("sha256=")
    # Constant-time: a naive `==` leaks the correct prefix length through timing.
    return hmac.compare_digest(expected, provided)


_BLOCKED_PORTS = frozenset({22, 23, 25, 3306, 5432, 6379, 9200, 11211, 27017})


def validate_webhook_url(url: str) -> None:
    """Reject URLs that would let a tenant point our server at private infrastructure."""
    parsed = urlparse(url)

    if parsed.scheme not in ("https", "http"):
        raise ValidationRequestError("Webhook URLs must use http or https")
    if not parsed.hostname:
        raise ValidationRequestError("Webhook URL is missing a hostname")
    if parsed.port and parsed.port in _BLOCKED_PORTS:
        raise ValidationRequestError(f"Port {parsed.port} is not allowed for webhooks")

    try:
        # Resolve *all* addresses: a hostname with one public and one private A
        # record would otherwise pass a check that only looked at the first.
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise ValidationRequestError("Webhook hostname could not be resolved") from None

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValidationRequestError(
                "Webhook URLs must point to a public address. Private, loopback and "
                "link-local addresses are not allowed."
            )

    # NOTE: this is a check at registration time, so it is vulnerable to DNS
    # rebinding — a hostname that resolves publicly now and privately at delivery
    # time. The complete fix is to resolve and pin the address at delivery, or to
    # route webhook traffic through an egress proxy with an allowlist. Recorded as
    # a known limitation in docs/SECURITY.md.
