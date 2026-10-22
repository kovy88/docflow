"""Integrator API keys of a firm's workspace and webhooks of finished extractions."""

import hashlib
import hmac
import json

import httpx
from sqlmodel import Session, select

from app import webhooks
from app.db import ApiKey, WebhookDelivery, Workspace, engine, utcnow
from app.templates import load_templates
from app.workspaces import create_workspace
from tests.test_external import api, api_extract, api_upload, auth, new_key  # noqa: F401


def firm(name, quota=None):
    with Session(engine) as session:
        workspace, code = create_workspace(session, name, quota)
        return workspace.id, {"X-Access-Code": code}


def firm_key(workspace_id, **fields) -> tuple[int, str]:
    plain = new_key("Integrace firmy")
    with Session(engine) as session:
        key = session.exec(select(ApiKey).order_by(ApiKey.id.desc())).first()
        key.workspace_id = workspace_id
        for name, value in fields.items():
            setattr(key, name, value)
        session.add(key)
        session.commit()
        return key.id, plain


def test_firm_key_uses_firm_templates_and_quota(api, samples):  # noqa: F811
    workspace_id, code = firm("Spedice s vlastní šablonou", quota=1)
    template = load_templates()["invoice"].model_dump() | {"id": "faktura_firmy"}
    assert api.post("/templates", json=template, headers=code).status_code == 201
    _, key = firm_key(workspace_id)
    other = new_key("Jiný klient")

    assert "faktura_firmy" in [t["id"] for t in api.get("/v1/templates", headers=auth(key)).json()]
    assert "faktura_firmy" not in [
        t["id"] for t in api.get("/v1/templates", headers=auth(other)).json()
    ]
    result = api_extract(api, key, samples / "faktura_ok.pdf", "faktura_firmy")
    assert result["template_id"] == "faktura_firmy" and result["status"] != "failed"
    doc = api_upload(api, other, samples / "faktura_ok.pdf").json()
    r = api.post(
        f"/v1/documents/{doc['id']}/extract",
        json={"template_id": "faktura_firmy"},
        headers=auth(other),
    )
    assert r.json()["error"]["code"] == "unknown_template"

    # The extraction counted towards the firm's quota of 1, in the API and the web app.
    assert api.get("/access", headers=code).json()["llm_calls_left_today"] == 0
    doc = api_upload(api, key, samples / "faktura_ok.pdf").json()
    r = api.post(
        f"/v1/documents/{doc['id']}/extract", json={"template_id": "invoice"}, headers=auth(key)
    )
    assert r.status_code == 429

    with Session(engine) as session:
        workspace = session.get(Workspace, workspace_id)
        workspace.revoked_at = utcnow()
        session.add(workspace)
        session.commit()
    r = api.get("/v1/templates", headers=auth(key))
    assert r.status_code == 401 and r.json()["error"]["code"] == "revoked_key"


class Receiver:
    """Stands in for the client's server: records requests, answers with given codes."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, url, content, headers, timeout):
        self.requests.append((url, content, headers))
        answer = self.answers.pop(0) if self.answers else 200
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer)


def deliveries(key_id):
    with Session(engine) as session:
        query = select(WebhookDelivery).where(WebhookDelivery.api_key_id == key_id)
        return session.exec(query).all()


def test_webhook_is_signed_and_carries_the_result(api, samples, monkeypatch):  # noqa: F811
    receiver = Receiver(200)
    monkeypatch.setattr(webhooks.httpx, "post", receiver)
    secret = webhooks.new_secret()
    key_id, key = firm_key(None, webhook_url="https://erp.example.cz/hook", webhook_secret=secret)

    result = api_extract(api, key, samples / "faktura_ok.pdf")
    [(url, body, headers)] = receiver.requests
    assert url == "https://erp.example.cz/hook"
    assert headers["Docflow-Event"] == "extraction.completed"
    timestamp, signature = (
        part.split("=", 1)[1] for part in headers["Docflow-Signature"].split(",")
    )
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    assert hmac.compare_digest(signature, expected.hexdigest())
    payload = json.loads(body)
    assert payload["data"] == result  # the same as GET /v1/extractions/{id}
    [delivery] = deliveries(key_id)
    assert delivery.delivered_at is not None and delivery.attempts == 1


def test_webhook_retries_then_gives_up(api, samples, monkeypatch):  # noqa: F811
    receiver = Receiver(500, httpx.ConnectError("refused"), 503)
    monkeypatch.setattr(webhooks.httpx, "post", receiver)
    monkeypatch.setattr(webhooks, "RETRY_DELAYS_S", (0, 0))
    key_id, key = firm_key(
        None, webhook_url="https://erp.example.cz/hook", webhook_secret=webhooks.new_secret()
    )
    api_extract(api, key, samples / "faktura_ok.pdf")
    assert len(receiver.requests) == 3
    [delivery] = deliveries(key_id)
    assert delivery.delivered_at is None and delivery.attempts == 3
    assert delivery.status_code == 503


def test_no_webhook_without_url(api, samples, monkeypatch):  # noqa: F811
    receiver = Receiver()
    monkeypatch.setattr(webhooks.httpx, "post", receiver)
    key_id, key = firm_key(None)
    api_extract(api, key, samples / "faktura_ok.pdf")
    assert receiver.requests == [] and deliveries(key_id) == []
