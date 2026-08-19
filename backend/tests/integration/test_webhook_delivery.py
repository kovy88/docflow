"""`WebhookService.deliver()` — delivery-time SSRF re-validation.

Registration-time validation (`validate_webhook_url`, exercised in
`TestWebhookRegistration` in test_api.py) cannot catch DNS rebinding: a
hostname that resolves publicly when a webhook is registered and privately by
the time a delivery — or a retry, possibly hours later — actually runs. These
tests exercise `deliver()` directly against a real database row, mocking only
the two things that must never happen for real in a test: outbound DNS
resolution and the outbound HTTP call itself.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.models import Organization
from docflow.domain.enums import DeliveryStatus, WebhookEvent
from docflow.services.webhook_service import WebhookService


async def _endpoint_and_delivery(session: AsyncSession, organization: Organization, *, url: str):
    service = WebhookService(session, organization_id=organization.id)
    endpoint = await service.repo.create(
        url=url,
        description=None,
        secret="whsec_test",
        events=[WebhookEvent.DOCUMENT_PROCESSED.value],
    )
    delivery = service.repo.queue_delivery(
        endpoint_id=endpoint.id,
        event=WebhookEvent.DOCUMENT_PROCESSED.value,
        payload={"event": "document.processed", "data": {}},
        status=DeliveryStatus.PENDING.value,
    )
    await session.flush()
    return service, endpoint, delivery


class TestWebhookDeliverySSRF:
    async def test_delivery_blocked_when_hostname_resolves_privately(
        self, session: AsyncSession, organization: Organization
    ):
        """The core regression test: a hostname that resolves to a private
        address *at delivery time* must be blocked, exhausted immediately (no
        retry), and its endpoint disabled — regardless of what it resolved to
        when it was registered. `repo.create` is used directly, bypassing
        `validate_webhook_url`, specifically so this test is about delivery-time
        behavior alone, not a proxy for registration-time behavior already
        covered in test_api.py."""
        service, endpoint, delivery = await _endpoint_and_delivery(
            session, organization, url="https://rebind-target.example.com/hook"
        )

        with patch(
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("169.254.169.254", 0))],
        ):
            result = await service.deliver(delivery.id)

        assert result["status"] == "blocked_ssrf"
        assert delivery.status == DeliveryStatus.EXHAUSTED.value
        assert endpoint.is_active is False
        assert delivery.next_retry_at is None

    async def test_delivery_succeeds_and_pins_the_validated_address(
        self, session: AsyncSession, organization: Organization
    ):
        """Happy path: the actual outbound request must go to the resolved,
        validated IP address — not the hostname a second, unpinned lookup could
        answer differently for — while still presenting the real hostname to
        the receiver via the Host header and to TLS via sni_hostname."""
        service, endpoint, delivery = await _endpoint_and_delivery(
            session, organization, url="https://good.example.com/hook"
        )

        mock_post = AsyncMock(return_value=httpx.Response(200, text="ok"))
        with (
            patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]),
            patch.object(httpx.AsyncClient, "post", mock_post),
        ):
            result = await service.deliver(delivery.id)

        assert result["status"] == "delivered"
        assert delivery.status == DeliveryStatus.DELIVERED.value
        assert endpoint.is_active is True

        mock_post.assert_awaited_once()
        called_url = mock_post.call_args.args[0]
        called_kwargs = mock_post.call_args.kwargs
        assert called_url == "https://93.184.216.34/hook"
        assert called_kwargs["headers"]["Host"] == "good.example.com"
        assert called_kwargs["extensions"] == {"sni_hostname": "good.example.com"}

    async def test_delivery_not_found_does_not_touch_dns(
        self, session: AsyncSession, organization: Organization
    ):
        """Guards the early-return ordering: a delivery id that doesn't exist
        (or belongs to another tenant) must return before any DNS resolution
        happens at all."""
        service = WebhookService(session, organization_id=organization.id)
        with patch("socket.getaddrinfo") as mock_resolve:
            result = await service.deliver(uuid.uuid4())
        assert result["status"] == "not_found"
        mock_resolve.assert_not_called()
