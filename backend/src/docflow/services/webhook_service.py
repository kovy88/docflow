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

The same check runs twice: once at registration (`validate_webhook_url`, so an
obviously-bad URL never reaches the queue) and once again, pinned, immediately
before every delivery attempt (`deliver`). The second check is what closes the
DNS-rebinding gap a registration-only check would leave open — a hostname that
resolves publicly at registration and privately by the time a delivery (or a
retry, possibly hours later) actually runs. `deliver` resolves the hostname,
validates the result, and connects directly to that exact validated IP address
— not to the hostname — using httpx's `sni_hostname`/`Host`-header extensions so
TLS verification and virtual-hosting still see the real hostname. No second,
unpinned DNS lookup happens between validating an address and connecting to it,
which is what a TOCTOU rebind needs in order to work. A delivery blocked this way
is not treated as an ordinary transient failure: it is exhausted immediately (no
retries — an attacker doing real rebinding wants exactly the retries a routine
failure would get) and the endpoint is disabled, since this is materially
stronger evidence of an attack than a slow or unreachable receiver.
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
from urllib.parse import urlparse, urlunparse

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

        parsed_url = urlparse(endpoint.url)
        hostname = parsed_url.hostname
        try:
            if hostname is None:
                # Can't happen for a URL that passed `validate_webhook_url` at
                # registration — guarded anyway so this is a typed impossibility,
                # not an assumed one.
                raise ValidationRequestError("Webhook URL is missing a hostname")
            # Re-validate and pin *now*, not at registration time — see the
            # module docstring's "SSRF protection" section for why this closes
            # the DNS-rebinding gap a registration-only check leaves open.
            pinned_address = _resolve_and_validate(hostname)[0]
        except ValidationRequestError as exc:
            logger.warning(
                "webhook.delivery_blocked_ssrf",
                delivery_id=str(delivery.id),
                endpoint_id=str(endpoint.id),
                hostname=hostname,
                reason=str(exc),
            )
            delivery.status = DeliveryStatus.EXHAUSTED.value
            delivery.response_body = (
                "blocked: endpoint resolved to a disallowed address at delivery time"
            )
            endpoint.is_active = False
            endpoint.last_failure_at = dt.datetime.now(dt.UTC)
            return {"status": "blocked_ssrf"}

        delivery.attempts = attempt
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS,
                # Never follow redirects: a 302 to an internal address would defeat
                # both the registration-time check and the pinned address below.
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    _pin_url_to_address(endpoint.url, pinned_address),
                    content=body,
                    headers={
                        # Explicit, not derived from the (now IP-literal) request
                        # URL — the receiver, and any virtual-hosting in front of
                        # it, still needs the real hostname.
                        "Host": hostname,
                        "Content-Type": "application/json",
                        "User-Agent": "Docflow-Webhooks/1.0",
                        "X-Docflow-Event": delivery.event,
                        "X-Docflow-Delivery-Id": str(delivery.id),
                        "X-Docflow-Timestamp": timestamp,
                        "X-Docflow-Signature": f"sha256={signature}",
                    },
                    # TLS verification is checked against this, not the literal IP
                    # in the URL — same idea as the Host header above, for the
                    # certificate instead of the application-layer routing.
                    extensions={"sni_hostname": hostname},
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


def _resolve_and_validate(hostname: str) -> list[str]:
    """Resolve every address a hostname points to; reject the lot if any one of
    them is private, loopback, link-local, reserved, multicast or unspecified.

    Shared by registration-time validation (`validate_webhook_url`) and
    delivery-time re-validation (`WebhookService.deliver`) so the two checks
    cannot drift apart. All-or-nothing rather than "just check the address
    we'll actually connect to": `socket.getaddrinfo` result order is not
    guaranteed stable across calls, and a hostname with one public and one
    private record is itself a signal worth rejecting outright, not routing
    around by getting lucky on which address comes back first.

    Returns every validated address as a string (IPv4 or IPv6). Registration
    only cares that the call didn't raise; delivery uses the first entry as
    the address to pin the connection to.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValidationRequestError("Webhook hostname could not be resolved") from None

    addresses: list[str] = []
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
        addresses.append(str(address))
    return addresses


def _pin_url_to_address(url: str, address: str) -> str:
    """Rewrite a URL's host to a literal IP address, preserving scheme, port,
    path, query, fragment and any userinfo unchanged.

    Used to connect to an address that has already been validated, without a
    second, unpinned DNS lookup happening in between — see the module
    docstring's "SSRF protection" section.
    """
    parsed = urlparse(url)
    netloc = f"[{address}]" if ":" in address else address
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username:
        userinfo = (
            parsed.username if not parsed.password else f"{parsed.username}:{parsed.password}"
        )
        netloc = f"{userinfo}@{netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def validate_webhook_url(url: str) -> None:
    """Reject URLs that would let a tenant point our server at private infrastructure.

    Registration-time only — see `_resolve_and_validate`'s docstring and the
    module docstring for why delivery re-checks this, pinned, instead of
    trusting this one call to still be true whenever a delivery eventually
    happens.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("https", "http"):
        raise ValidationRequestError("Webhook URLs must use http or https")
    if not parsed.hostname:
        raise ValidationRequestError("Webhook URL is missing a hostname")
    if parsed.port and parsed.port in _BLOCKED_PORTS:
        raise ValidationRequestError(f"Port {parsed.port} is not allowed for webhooks")

    _resolve_and_validate(parsed.hostname)
