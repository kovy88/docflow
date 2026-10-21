"""Public trial page (/trial) and integrator API (/v1)."""

import io
import json
import uuid
from datetime import timedelta
from zipfile import ZipFile

import pytest
from sqlmodel import Session, select

from app import api_v1
from app.config import settings
from app.db import ApiKey, ExternalDocument, ExternalExtraction, TrialVisit, engine, utcnow
from app.external import aware, purge_expired
from app.main import app, get_extractor
from tests.test_api import FakeExtractor

CMR = {
    "order_number": ("OP-2026-0815", 1, "Objednávka přepravy č. OP-2026-0815", 0.95),
    "customer_name": ("Ilustrační Spedice s.r.o.", 1, "Objednatel: Ilustrační Spedice s.r.o.", 0.9),
    "carrier_name": (
        "Fiktivní Doprava Novák s.r.o.",
        1,
        "Dopravce: Fiktivní Doprava Novák s.r.o.",
        0.9,
    ),
    "loading_place": ("Na Rovince 3, 720 00 Ostrava", 1, "Na Rovince 3, 720 00 Ostrava", 0.9),
    "loading_date": ("4. 11. 2026", 1, "Datum nakládky: 4. 11. 2026", 0.9),
    "unloading_place": ("Industriezeile 20, 4020 Linz", 1, "Industriezeile 20, 4020 Linz", 0.9),
    "unloading_date": ("5. 11. 2026", 1, "Datum vykládky: 5. 11. 2026", 0.9),
    "goods": (
        "kartony s keramickými obklady, 22 palet EUR",
        1,
        "Zboží: kartony s keramickými obklady, 22 palet EUR",
        0.9,
    ),
    "weight_kg": ("18 400 kg", 1, "Hrubá hmotnost: 18 400 kg", 0.9),
    "license_plate": ("4J7 2815", 1, "SPZ 4J7 2815", 0.9),
    "price": ("1 150,00", 1, "Sjednaná cena přepravy: 1 150,00 EUR", 0.9),
    "currency": ("EUR", 1, "1 150,00 EUR", 0.9),
}
INVOICE = {
    "supplier_name": ("Modrá Kancelář s.r.o.", 1, "Dodavatel: Modrá Kancelář s.r.o.", 0.98),
    "supplier_ico": ("27489612", 1, "IČO: 27489612", 0.97),
    "customer_name": ("Pekárna U Mlýna a.s.", 1, "Odběratel: Pekárna U Mlýna a.s.", 0.95),
    "invoice_number": ("2026-0142", 1, "daňový doklad č. 2026-0142", 0.99),
    "issue_date": ("3. 9. 2026", 1, "Datum vystavení: 3. 9. 2026", 0.95),
    "tax_date": ("3. 9. 2026", 1, "Datum zdanitelného plnění: 3. 9. 2026", 0.95),
    "due_date": ("17. 9. 2026", 1, "Datum splatnosti: 17. 9. 2026", 0.95),
    "net_amount": ("10 000,00", 1, "Základ daně 21 %: 10 000,00 Kč", 0.9),
    "vat_amount": ("2 100,00", 1, "DPH 21 %: 2 100,00 Kč", 0.9),
    "total_amount": ("12 100,00", 1, "Celkem k úhradě: 12 100,00 Kč", 0.9),
    "currency": ("CZK", 1, "Měna: CZK", 0.9),
    "bank_account": ("2801234567/2010", 1, "Bankovní účet: 2801234567/2010", 0.9),
}


class ByTemplate(FakeExtractor):
    """Scripted answers of the template being extracted (both use customer_name)."""

    def __init__(self):
        super().__init__({})

    def extract(self, pages, template):
        self.answers = CMR if template.id == "transport_order" else INVOICE
        return super().extract(pages, template)


@pytest.fixture
def api(client, monkeypatch):
    """Client with a fake model on both apps and its own visitor IP."""
    monkeypatch.setattr(settings, "client_ip_header", "x-test-ip")
    monkeypatch.setattr(settings, "daily_llm_limit", 100_000)
    monkeypatch.setattr(settings, "trial_daily_limit", 100_000)
    client.headers["x-test-ip"] = str(uuid.uuid4())

    def fake():
        return ByTemplate()

    app.dependency_overrides[get_extractor] = fake
    api_v1.app.dependency_overrides[get_extractor] = fake
    yield client
    api_v1.app.dependency_overrides.clear()


def new_key(name="Spedice", quota=100) -> str:
    with Session(engine) as session:
        return api_v1.create_key(session, name, quota)[1]


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def trial(client, path, template_id="transport_order", **form):
    with path.open("rb") as f:
        return client.post(
            "/trial",
            files={"file": (path.name, f, "application/pdf")},
            data={"template_id": template_id, **form},
        )


def api_upload(client, key, path, **headers):
    with path.open("rb") as f:
        return client.post(
            "/v1/documents",
            files={"file": (path.name, f, "application/pdf")},
            headers=auth(key) | headers,
        )


def api_extract(client, key, path, template_id="invoice") -> dict:
    doc = api_upload(client, key, path).json()
    r = client.post(
        f"/v1/documents/{doc['id']}/extract", json={"template_id": template_id}, headers=auth(key)
    )
    assert r.status_code == 202, r.text
    return client.get(f"/v1/extractions/{r.json()['id']}", headers=auth(key)).json()


# Trial page


def test_trial_extracts_cmr_with_sources_and_rules(api, samples):
    r = trial(api, samples / "objednavka_prepravy.pdf", ref="CBSped")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "running" and len(r.json()["id"]) >= 20
    result = api.get(f"/trial/{r.json()['id']}").json()

    assert result["status"] == "ok"
    fields = {f["key"]: f for f in result["fields"]}
    assert fields["weight_kg"]["value"] == 18400.0
    assert fields["loading_date"]["value"] == "2026-11-04"
    assert fields["carrier_name"]["grounded"] and fields["carrier_name"]["page"] == 1
    assert result["rules"] == [
        {
            "type": "date_order",
            "description": "Datum nakládky není po Datum vykládky",
            "fields": ["loading_date", "unloading_date"],
            "result": "passed",
        }
    ]
    with Session(engine) as session:
        doc = session.get(ExternalDocument, result["document_id"])
        assert doc.source == "trial" and doc.ref == "cbsped"
        assert doc.ip_hash != api.headers["x-test-ip"]  # stored only hashed
        assert timedelta(hours=23) < doc.expires_at - doc.created_at <= timedelta(hours=24)
        visit = session.exec(select(TrialVisit).order_by(TrialVisit.id.desc())).first()
        assert (visit.ref, visit.template_id) == ("cbsped", "transport_order")


def test_trial_ignores_malformed_ref(api, samples):
    r = trial(api, samples / "faktura_ok.pdf", "invoice", ref="<script>")
    with Session(engine) as session:
        assert session.get(ExternalDocument, r.json()["document_id"]).ref is None


def test_trial_is_not_in_workspace(api, samples):
    before = api.get("/documents").json()
    trial(api, samples / "faktura_ok.pdf", "invoice")
    assert api.get("/documents").json() == before


def test_trial_limit_per_ip(api, samples, monkeypatch):
    monkeypatch.setattr(settings, "trial_per_ip", 3)
    for _ in range(3):
        assert trial(api, samples / "faktura_ok.pdf", "invoice").status_code == 202
    r = trial(api, samples / "faktura_ok.pdf", "invoice")
    assert r.status_code == 429 and "24 hodin" in r.json()["detail"]
    api.headers["x-test-ip"] = str(uuid.uuid4())  # another visitor is not affected
    assert trial(api, samples / "faktura_ok.pdf", "invoice").status_code == 202


def test_trial_daily_limit_and_global_model_limit(api, samples, monkeypatch):
    with Session(engine) as session:
        trials = session.exec(select(ExternalDocument).where(ExternalDocument.source == "trial"))
        monkeypatch.setattr(settings, "trial_daily_limit", len(trials.all()))
    assert trial(api, samples / "faktura_ok.pdf", "invoice").status_code == 429

    monkeypatch.setattr(settings, "trial_daily_limit", 100_000)
    monkeypatch.setattr(settings, "daily_llm_limit", 0)
    r = trial(api, samples / "faktura_ok.pdf", "invoice")
    assert r.status_code == 429 and "Denní limit" in r.json()["detail"]


def test_trial_rejects_large_long_non_pdf_and_other_templates(api, samples, monkeypatch):
    r = api.post(
        "/trial", files={"file": ("a.txt", b"hello", "text/plain")}, data={"template_id": "invoice"}
    )
    assert r.status_code == 415
    assert trial(api, samples / "faktura_ok.pdf", "contract").status_code == 422
    monkeypatch.setattr(settings, "trial_max_pages", 0)
    assert trial(api, samples / "faktura_ok.pdf", "invoice").status_code == 413
    monkeypatch.setattr(settings, "trial_max_mb", 0)
    r = trial(api, samples / "faktura_ok.pdf", "invoice")
    assert r.status_code == 413 and "MB" in r.json()["detail"]


def test_trial_is_deleted_after_24_hours(api, samples):
    extraction_id = trial(api, samples / "faktura_ok.pdf", "invoice").json()["id"]
    with Session(engine) as session:
        doc_id = session.get(ExternalExtraction, extraction_id).document_id
        doc = session.get(ExternalDocument, doc_id)
        assert aware(doc.expires_at) > utcnow() + timedelta(hours=23)
        doc.expires_at = utcnow() - timedelta(seconds=1)
        session.add(doc)
        session.commit()
    assert api.get(f"/trial/{extraction_id}").status_code == 404  # hidden before the purge
    assert purge_expired() >= 1
    with Session(engine) as session:  # file, text and results are gone
        assert session.get(ExternalDocument, doc_id) is None
        assert session.get(ExternalExtraction, extraction_id) is None


def test_trial_delete_now(api, samples):
    extraction_id = trial(api, samples / "faktura_ok.pdf", "invoice").json()["id"]
    assert api.delete(f"/trial/{extraction_id}").status_code == 204
    assert api.get(f"/trial/{extraction_id}").status_code == 404


def test_trial_exports(api, samples):
    extraction_id = trial(api, samples / "objednavka_prepravy.pdf").json()["id"]
    data = api.get(f"/trial/{extraction_id}/export?format=json").json()
    assert data["fields"][0]["quote"] and data["rules"][0]["result"] == "passed"
    xlsx = api.get(f"/trial/{extraction_id}/export?format=xlsx")
    sheet = ZipFile(io.BytesIO(xlsx.content)).read("xl/worksheets/sheet1.xml").decode()
    assert "Fiktivní Doprava Novák s.r.o." in sheet and "Hmotnost (kg)" in sheet
    assert api.get(f"/trial/{extraction_id}/export?format=pohoda").status_code == 422


def test_trial_and_api_ids_do_not_cross(api, samples):
    key = new_key()
    api_result = api_extract(api, key, samples / "faktura_ok.pdf")
    assert api.get(f"/trial/{api_result['id']}").status_code == 404
    trial_id = trial(api, samples / "faktura_ok.pdf", "invoice").json()["id"]
    assert api.get(f"/v1/extractions/{trial_id}", headers=auth(key)).status_code == 404


# Integrator API


def test_api_full_flow(api, samples):
    key = new_key()
    result = api_extract(api, key, samples / "objednavka_prepravy.pdf", "transport_order")
    assert result["status"] == "ok"
    price = next(f for f in result["fields"] if f["key"] == "price")
    assert price["value"] == 1150.0 and price["page"] == 1 and price["confidence"] == 0.9
    assert price["quote"] and price["grounded"]
    assert result["rules"][0]["result"] == "passed"
    templates = api.get("/v1/templates", headers=auth(key)).json()
    assert "transport_order" in {t["id"] for t in templates}


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "missing_key"),
        ({"Authorization": "Bearer dfk_wrong"}, "invalid_key"),
        ({"Authorization": "Basic abc"}, "missing_key"),
    ],
)
def test_api_rejects_missing_and_invalid_keys(api, headers, code):
    r = api.get("/v1/templates", headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": {"code": code, "message": r.json()["error"]["message"]}}


def test_api_revoked_key(api):
    key = new_key()
    assert api.get("/v1/templates", headers=auth(key)).status_code == 200
    with Session(engine) as session:
        row = session.exec(select(ApiKey).where(ApiKey.key_hash == api_v1.hash_key(key))).one()
        assert key not in (row.key_hash, row.prefix)  # only the hash is stored
        row.revoked_at = utcnow()
        session.add(row)
        session.commit()
    r = api.get("/v1/templates", headers=auth(key))
    assert r.status_code == 401 and r.json()["error"]["code"] == "revoked_key"


def test_api_daily_quota(api, samples):
    key = new_key(quota=1)
    api_extract(api, key, samples / "faktura_ok.pdf")
    doc = api_upload(api, key, samples / "faktura_ok.pdf").json()
    r = api.post(
        f"/v1/documents/{doc['id']}/extract", json={"template_id": "invoice"}, headers=auth(key)
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
    other = new_key(quota=1)  # quotas are per client
    assert api_extract(api, other, samples / "faktura_ok.pdf")["status"] == "ok"


def test_api_global_model_limit(api, samples, monkeypatch):
    key = new_key()
    doc = api_upload(api, key, samples / "faktura_ok.pdf").json()
    monkeypatch.setattr(settings, "daily_llm_limit", 0)
    r = api.post(
        f"/v1/documents/{doc['id']}/extract", json={"template_id": "invoice"}, headers=auth(key)
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "limit_exceeded"


def test_api_clients_are_isolated(api, samples):
    alice, bob = new_key("Alice"), new_key("Bob")
    result = api_extract(api, alice, samples / "faktura_ok.pdf")
    for path in (
        f"/v1/documents/{result['document_id']}",
        f"/v1/extractions/{result['id']}",
        f"/v1/extractions/{result['id']}/export?format=json",
    ):
        r = api.get(path, headers=auth(bob))
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert (
        api.delete(f"/v1/documents/{result['document_id']}", headers=auth(bob)).status_code == 404
    )
    assert api.get(f"/v1/extractions/{result['id']}", headers=auth(alice)).status_code == 200


def test_api_idempotency_key(api, samples):
    key = new_key()
    idem = {"Idempotency-Key": str(uuid.uuid4())}
    first = api_upload(api, key, samples / "faktura_ok.pdf", **idem)
    again = api_upload(api, key, samples / "faktura_ok.pdf", **idem)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    r = api_upload(api, key, samples / "faktura_chyby.pdf", **idem)
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"
    other = api_upload(api, new_key(), samples / "faktura_ok.pdf", **idem)  # keys are per client
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]


def test_api_upload_limits_and_error_format(api, samples, monkeypatch):
    key = new_key()
    r = api.post("/v1/documents", files={"file": ("a.txt", b"x", "text/plain")}, headers=auth(key))
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media_type"
    monkeypatch.setattr(settings, "api_max_pages", 0)
    r = api_upload(api, key, samples / "faktura_ok.pdf")
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"
    monkeypatch.setattr(settings, "api_max_pages", 50)
    monkeypatch.setattr(settings, "api_max_mb", 0)
    r = api_upload(api, key, samples / "faktura_ok.pdf")
    assert r.status_code == 413 and "MB" in r.json()["error"]["message"]
    r = api.post("/v1/documents", headers=auth(key))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    r = api.get("/v1/nothing", headers=auth(key))
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_api_unknown_template(api, samples):
    key = new_key()
    doc = api_upload(api, key, samples / "faktura_ok.pdf").json()
    r = api.post(f"/v1/documents/{doc['id']}/extract", json={"template_id": "x"}, headers=auth(key))
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_template"


def test_api_exports(api, samples):
    key = new_key()
    result = api_extract(api, key, samples / "faktura_ok.pdf")
    url = f"/v1/extractions/{result['id']}/export"
    assert api.get(f"{url}?format=json", headers=auth(key)).json()["id"] == result["id"]
    xlsx = api.get(f"{url}?format=xlsx", headers=auth(key))
    assert xlsx.status_code == 200 and xlsx.content.startswith(b"PK")
    # Accounting needs a human approval first.
    r = api.get(f"{url}?format=pohoda", headers=auth(key))
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    r = api.patch(f"/v1/extractions/{result['id']}", json={"values": {}}, headers=auth(key))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    pohoda = api.get(f"{url}?format=pohoda", headers=auth(key))
    assert pohoda.status_code == 200 and b"2026-0142" in pohoda.content
    money = api.get(f"{url}?format=money", headers=auth(key))
    assert money.status_code == 200 and b"2026-0142" in money.content
    r = api.get(f"{url}?format=pdf", headers=auth(key))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_api_review_rejects_unknown_field(api, samples):
    key = new_key()
    result = api_extract(api, key, samples / "faktura_ok.pdf")
    r = api.patch(
        f"/v1/extractions/{result['id']}", json={"values": {"nope": "1"}}, headers=auth(key)
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_field"


def test_api_delete_document(api, samples):
    key = new_key()
    result = api_extract(api, key, samples / "faktura_ok.pdf")
    assert (
        api.delete(f"/v1/documents/{result['document_id']}", headers=auth(key)).status_code == 204
    )
    assert api.get(f"/v1/extractions/{result['id']}", headers=auth(key)).status_code == 404


def test_api_openapi_lists_only_v1(api):
    spec = api.get("/v1/openapi.json").json()
    assert "/documents/{document_id}/extract" in spec["paths"]
    assert (
        "/trial" not in json.dumps(spec["paths"])
        and "/templates/{template_id}" not in spec["paths"]
    )


def test_workspace_access_code_unchanged(api, monkeypatch):
    monkeypatch.setattr(settings, "access_code", "tajne")
    assert api.get("/documents").status_code == 401
    assert api.get("/documents", headers={"X-Access-Code": "tajne"}).status_code == 200
    key = new_key()  # an API key does not open the workspace
    assert api.get("/documents", headers=auth(key)).status_code == 401
