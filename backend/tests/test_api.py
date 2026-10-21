from app.extractor import ExtractionOutput, FieldAnswer
from app.main import app, get_extractor


class FakeExtractor:
    """Returns scripted answers so the pipeline can be tested without calling OpenAI."""

    model = "fake"

    def __init__(self, answers: dict[str, tuple]):
        self.answers = answers

    def extract(self, pages, template):
        return ExtractionOutput(
            answers={
                k: FieldAnswer(value=v, page=p, quote=q, confidence=c)
                for k, (v, p, q, c) in self.answers.items()
            },
            usage={"input_tokens": 0, "output_tokens": 0},
        )


BAD_INVOICE = {
    "supplier_name": ("Modrá Kancelář s.r.o.", 1, "Dodavatel: Modrá Kancelář s.r.o.", 0.98),
    "supplier_ico": ("27489613", 1, "IČO: 27489613", 0.97),
    "customer_name": ("Pekárna U Mlýna a.s.", 1, "Odběratel: Pekárna U Mlýna a.s.", 0.95),
    "invoice_number": ("2026-0143", 1, "daňový doklad č. 2026-0143", 0.99),
    "issue_date": ("20. 9. 2026", 1, "Datum vystavení: 20. 9. 2026", 0.95),
    "due_date": ("6. 9. 2026", 1, "Datum splatnosti: 6. 9. 2026", 0.95),
    "net_amount": ("10 000,00", 1, "Základ daně 21 %: 10 000,00 Kč", 0.9),
    "vat_amount": ("2 100,00", 1, "DPH 21 %: 2 100,00 Kč", 0.9),
    "total_amount": ("12 010,00", 1, "Celkem k úhradě: 12 010,00 Kč", 0.9),
    # Hallucinated: value and quote are not in the PDF.
    "currency": ("EUR", 1, "Měna: EUR", 0.4),
    "bank_account": (None, None, None, 0.0),
}


def extract(client, document_id, body):
    """Queue an extraction and read the finished row (TestClient runs background tasks
    before returning the response)."""
    r = client.post(f"/documents/{document_id}/extractions", json=body)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "running"
    return client.get(f"/extractions/{r.json()['id']}").json()


def upload(client, path):
    with path.open("rb") as f:
        return client.post("/documents", files={"file": (path.name, f, "application/pdf")})


def test_templates(client):
    ids = [t["id"] for t in client.get("/templates").json()]
    assert ids == ["contract", "insurance_policy", "invoice", "lease", "transport_order"]


def test_upload_rejects_non_pdf(client):
    r = client.post("/documents", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_extraction_flags_planted_errors(client, samples):
    doc = upload(client, samples / "faktura_chyby.pdf").json()
    assert doc["has_text"] and doc["page_count"] == 1

    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(BAD_INVOICE)
    result = extract(client, doc["id"], {"template_id": "invoice"})

    assert result["status"] == "needs_review"
    fields = {f["key"]: f for f in result["fields"]}
    assert fields["total_amount"]["value"] == 12010.0
    assert fields["issue_date"]["value"] == "2026-09-20"
    assert fields["supplier_name"]["grounded"] is True
    assert fields["currency"]["grounded"] is False

    codes = {(i["code"], tuple(i["fields"])) for i in result["issues"]}
    assert ("ico_checksum", ("supplier_ico",)) in codes
    assert ("sum_mismatch", ("net_amount", "vat_amount", "total_amount")) in codes
    assert ("date_order", ("issue_date", "due_date")) in codes
    assert ("ungrounded", ("currency",)) in codes
    assert ("low_confidence", ("currency",)) in codes
    assert not any(i["code"] == "missing" for i in result["issues"])  # bank_account is optional

    history = client.get(f"/documents/{doc['id']}/extractions").json()
    assert len(history) == 1

    # Human review: fix the hallucinated currency; model warnings disappear, rules stay.
    r = client.patch(f"/extractions/{result['id']}", json={"values": {"currency": "CZK"}})
    assert r.status_code == 200, r.text
    reviewed = r.json()
    assert reviewed["status"] == "approved" and reviewed["reviewed_at"]
    fields = {f["key"]: f for f in reviewed["fields"]}
    assert fields["currency"]["value"] == "CZK" and fields["currency"]["corrected"] is True
    assert "corrected" not in fields["total_amount"]
    codes = {i["code"] for i in reviewed["issues"]}
    assert codes == {"ico_checksum", "sum_mismatch", "date_order"}

    r = client.patch(f"/extractions/{result['id']}", json={"values": {"nope": "1"}})
    assert r.status_code == 422


def test_extraction_requires_api_key(client, samples):
    doc = upload(client, samples / "faktura_ok.pdf").json()
    r = client.post(f"/documents/{doc['id']}/extractions", json={"template_id": "invoice"})
    assert r.status_code == 503


def test_inline_template(client, samples):
    doc = upload(client, samples / "pojistka.pdf").json()
    template = {
        "id": "custom",
        "name": "Vlastní",
        "fields": [{"key": "premium", "label": "Pojistné", "type": "number"}],
    }
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(
        {"premium": ("48 600", 1, "Roční pojistné: 48 600 Kč", 0.9)}
    )
    result = extract(client, doc["id"], {"template": template})
    assert result["status"] == "ok"
    assert result["fields"][0]["value"] == 48600.0


def test_demo_documents(client):
    demo = client.get("/demo").json()
    assert len(demo) >= 26 and demo[0]["file"] == "invoice_01.pdf"
    r = client.post("/demo/invoice_01.pdf")
    assert r.status_code == 201 and r.json()["has_text"]
    assert client.post("/demo/..%2Fpyproject.toml").status_code == 404


def test_export(client, samples):
    doc = upload(client, samples / "pojistka.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(
        {"annual_premium": ("48 600 Kč", 1, "Roční pojistné: 48 600 Kč", 0.9)}
    )
    extraction = extract(client, doc["id"], {"template_id": "insurance_policy"})
    data = client.get(f"/extractions/{extraction['id']}/export").json()
    assert data["values"]["annual_premium"] == 48600.0
    csv_text = client.get(f"/extractions/{extraction['id']}/export?format=csv").text
    assert csv_text.startswith("\ufeffkey,label") and "Roční pojistné" in csv_text
    assert client.get(f"/extractions/{extraction['id']}/export?format=xml").status_code == 422


class BrokenExtractor:
    model = "broken"

    def extract(self, pages, template):
        raise RuntimeError("model went away")


def test_failed_extraction_is_stored(client, samples):
    doc = upload(client, samples / "faktura_ok.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: BrokenExtractor()
    result = extract(client, doc["id"], {"template_id": "invoice"})
    assert result["status"] == "failed"
    assert result["issues"][0]["code"] == "extraction_failed"
    assert "model went away" in result["issues"][0]["message"]
    assert client.patch(f"/extractions/{result['id']}", json={"values": {}}).status_code == 409


DELIVERY_NOTE = {
    "id": "delivery_note",
    "name": "Dodací list",
    "fields": [
        {"key": "number", "label": "Číslo"},
        {"key": "supplier_ico", "label": "IČO dodavatele"},
        {"key": "shipped", "label": "Odesláno", "type": "date"},
        {"key": "delivered", "label": "Doručeno", "type": "date"},
    ],
    "rules": [
        {"type": "ico", "field": "supplier_ico"},
        {"type": "date_order", "earlier": "shipped", "later": "delivered"},
    ],
}


def test_custom_template_crud(client, samples):
    assert client.post("/templates", json=DELIVERY_NOTE).status_code == 201
    assert client.post("/templates", json=DELIVERY_NOTE).status_code == 409
    templates = {t["id"]: t for t in client.get("/templates").json()}
    assert templates["delivery_note"]["builtin"] is False
    assert templates["invoice"]["builtin"] is True

    changed = DELIVERY_NOTE | {"name": "Dodací list v2"}
    assert client.put("/templates/delivery_note", json=changed).json()["name"] == "Dodací list v2"
    assert client.put("/templates/invoice", json=changed).status_code == 409

    # Saved templates can be used for extraction by id.
    doc = upload(client, samples / "faktura_ok.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(
        {"number": ("2026-0142", 1, "daňový doklad č. 2026-0142", 0.9)}
    )
    result = extract(client, doc["id"], {"template_id": "delivery_note"})
    assert result["template_id"] == "delivery_note"
    assert [i["code"] for i in result["issues"]] == ["missing"] * 3

    assert client.delete("/templates/delivery_note").status_code == 204
    assert client.get("/templates/delivery_note").status_code == 404
    assert client.delete("/templates/invoice").status_code == 409


def test_template_validation(client):
    bad_rule = DELIVERY_NOTE | {"id": "x", "rules": [{"type": "ico", "field": "nope"}]}
    assert client.post("/templates", json=bad_rule).status_code == 422
    wrong_type = DELIVERY_NOTE | {
        "id": "y",
        "rules": [{"type": "sum", "fields": ["number"], "equals": "number"}],
    }
    assert client.post("/templates", json=wrong_type).status_code == 422
    dup = DELIVERY_NOTE | {"id": "z", "fields": DELIVERY_NOTE["fields"] * 2, "rules": []}
    assert client.post("/templates", json=dup).status_code == 422


class SuggestingExtractor(FakeExtractor):
    def structured(self, system, user, schema_model):
        draft = {
            "name": "Faktura",
            "description": "Doklad.",
            "fields": [
                {
                    "key": "total",
                    "label": "Celkem",
                    "type": "number",
                    "description": "",
                    "required": True,
                }
            ],
        }
        return schema_model.model_validate(draft), {}


def test_template_suggestion_gets_unique_id(client, samples):
    doc = upload(client, samples / "faktura_ok.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: SuggestingExtractor({})
    r = client.post(f"/documents/{doc['id']}/template-suggestion")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "faktura" and r.json()["fields"][0]["key"] == "total"


def test_mistyped_quote_falls_back_to_value(client, samples):
    doc = upload(client, samples / "faktura_ok.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(
        {"customer_name": ("Pekárna U Mlýna a.s.", 1, "ODBĚRATEEL: Pekárna U Mlýna a.s. XYZ", 0.95)}
    )
    result = extract(client, doc["id"], {"template_id": "invoice"})
    field = next(f for f in result["fields"] if f["key"] == "customer_name")
    assert field["grounded"] is True and field["quote"] == "Pekárna U Mlýna a.s."
    assert not any(i["code"] == "ungrounded" for i in result["issues"])


def test_access_code_guards_documents_but_not_public_pages(client, samples, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "access_code", "tajne-heslo")
    assert client.get("/templates").status_code == 401  # custom templates can contain client data
    assert client.get("/demo").status_code == 200
    assert client.get("/documents").status_code == 401
    assert client.get("/access", headers={"X-Access-Code": "spatne"}).status_code == 401
    ok = client.get("/access", headers={"X-Access-Code": "tajne-heslo"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    with (samples / "faktura_ok.pdf").open("rb") as f:
        files = {"file": ("f.pdf", f, "application/pdf")}
        assert client.post("/documents", files=files).status_code == 401


def test_daily_llm_limit(client, samples, monkeypatch):
    from sqlmodel import Session, delete

    from app.config import settings
    from app.db import UsageEvent, engine

    with Session(engine) as session:
        session.exec(delete(UsageEvent))
        session.commit()
    monkeypatch.setattr(settings, "daily_llm_limit", 1)
    doc = upload(client, samples / "faktura_ok.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor({})
    first = client.post(f"/documents/{doc['id']}/extractions", json={"template_id": "invoice"})
    assert first.status_code == 202
    second = client.post(f"/documents/{doc['id']}/extractions", json={"template_id": "invoice"})
    assert second.status_code == 429


def test_pdf_is_served_from_the_database(client, samples):
    doc = upload(client, samples / "faktura_ok.pdf").json()
    r = client.get(f"/documents/{doc['id']}/file")
    assert r.status_code == 200 and r.content == (samples / "faktura_ok.pdf").read_bytes()
