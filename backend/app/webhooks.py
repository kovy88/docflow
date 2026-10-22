"""Webhooks of the integrator API: when an extraction finishes, its result is POSTed to the
key's webhook URL, signed like Stripe (Docflow-Signature: t=<unix time>,v1=<HMAC-SHA256 of
"<t>.<body>">), so the client does not need to poll. Every event is logged in
WebhookDelivery; failed attempts are retried a few times, then given up."""

import hashlib
import hmac
import json
import secrets
import time
import uuid

import httpx
from sqlmodel import Session

from app.db import ApiKey, ExternalExtraction, WebhookDelivery, engine, utcnow
from app.external import extraction_out
from app.runtime import logger

RETRY_DELAYS_S = (5, 30)  # after the first attempt; runs in a background thread
TIMEOUT_S = 10


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def deliver(extraction_id: str) -> None:
    with Session(engine) as session:
        extraction = session.get(ExternalExtraction, extraction_id)
        key = extraction and session.get(ApiKey, extraction.api_key_id)
        if key is None or not key.webhook_url or not key.webhook_secret:
            return
        event = "extraction.failed" if extraction.status == "failed" else "extraction.completed"
        body = json.dumps(
            {
                "id": str(uuid.uuid4()),
                "event": event,
                "created_at": utcnow().isoformat(),
                "data": extraction_out(extraction).model_dump(mode="json"),
            },
            ensure_ascii=False,
        ).encode()
        delivery = WebhookDelivery(
            api_key_id=key.id, extraction_id=extraction.id, event=event, url=key.webhook_url
        )
        url, secret = key.webhook_url, key.webhook_secret
    for delay in (0, *RETRY_DELAYS_S):
        time.sleep(delay)
        delivery.attempts += 1
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "Docflow-Event": event,
            "Docflow-Signature": sign(secret, timestamp, body),
        }
        try:
            response = httpx.post(url, content=body, headers=headers, timeout=TIMEOUT_S)
            delivery.status_code, delivery.error = response.status_code, None
            if response.is_success:
                delivery.delivered_at = utcnow()
                break
        except httpx.HTTPError as exc:
            delivery.status_code, delivery.error = None, str(exc)[:500]
    else:
        logger.warning("Webhook for extraction %s not delivered to %s", extraction_id, url)
    with Session(engine) as session:
        session.add(delivery)
        session.commit()
