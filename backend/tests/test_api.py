import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def upload(client, path):
    with path.open("rb") as f:
        return client.post("/documents", files={"file": (path.name, f, "application/pdf")})


def test_templates(client):
    ids = [t["id"] for t in client.get("/templates").json()]
    assert ids == ["contract", "insurance_policy", "invoice"]


def test_upload_rejects_non_pdf(client):
    r = client.post("/documents", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_extraction_flags_planted_errors(client, samples):
    doc = upload(client, samples / "faktura_chyby.pdf").json()
    assert doc["has_text"] and doc["page_count"] == 1

    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(BAD_INVOICE)
    r = client.post(f"/documents/{doc['id']}/extractions", json={"template_id": "invoice"})
    assert r.status_code == 201, r.text
    result = r.json()

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
    r = client.post(f"/documents/{doc['id']}/extractions", json={"template": template})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["fields"][0]["value"] == 48600.0
