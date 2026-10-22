from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.accounting import POHODA
from app.db import Document, Extraction, default_workspace, engine, utcnow
from app.main import app
from app.templates import load_templates

VALUES = {
    "supplier_name": "Alfa Servis s.r.o.",
    "supplier_ico": "27074358",
    "customer_name": "Beta a.s.",
    "invoice_number": "2026001234",
    "issue_date": "2026-10-01",
    "tax_date": "2026-09-30",
    "due_date": "2026-10-15",
    "net_amount": 10000.0,
    "vat_amount": 2100.0,
    "total_amount": 12100.0,
    "currency": "CZK",
    "bank_account": None,
}


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def seed(template_id="invoice", status="approved", **overrides):
    template = load_templates()[template_id]
    values = VALUES | overrides
    with Session(engine) as session:
        doc = Document(
            workspace_id=default_workspace(session).id,
            filename="faktura.pdf",
            page_count=1,
            pages=["Test"],
        )
        session.add(doc)
        session.flush()
        extraction = Extraction(
            document_id=doc.id,
            template_id=template.id,
            template=template.model_dump(),
            model="fake",
            status=status,
            fields=[{"key": f.key, "value": values.get(f.key)} for f in template.fields],
            issues=[],
            usage={},
            reviewed_at=utcnow(),
        )
        session.add(extraction)
        session.commit()
        return extraction.id


def export(client, ids, format):
    return client.post("/exports/accounting", json={"extraction_ids": ids, "format": format})


def test_pohoda_received_invoice(client):
    response = export(client, [seed(), seed(vat_amount=0.0, total_amount=10000.0)], "pohoda")
    assert response.status_code == 200
    ns = POHODA
    root = ET.fromstring(response.content)
    items = root.findall("dat:dataPackItem/inv:invoice", ns)
    assert len(items) == 2
    header = items[0].find("inv:invoiceHeader", ns)
    assert header.findtext("inv:invoiceType", namespaces=ns) == "receivedInvoice"
    assert header.findtext("inv:symVar", namespaces=ns) == "2026001234"
    assert header.findtext("inv:originalDocument", namespaces=ns) == "2026001234"
    assert header.findtext("inv:dateDue", namespaces=ns) == "2026-10-15"
    assert header.findtext("inv:dateTax", namespaces=ns) == "2026-09-30"
    assert header.findtext("inv:dateAccounting", namespaces=ns) == "2026-09-30"
    assert header.findtext("inv:partnerIdentity/typ:address/typ:ico", namespaces=ns) == "27074358"
    home = items[0].find("inv:invoiceSummary/inv:homeCurrency", ns)
    assert home.findtext("typ:priceHigh", namespaces=ns) == "10000.00"
    assert home.findtext("typ:priceHighVAT", namespaces=ns) == "2100.00"
    zero = items[1].find("inv:invoiceSummary/inv:homeCurrency", ns)
    assert [c.tag.split("}")[1] for c in zero] == ["priceNone"]


def test_money_s3_reduced_rate_and_text_symbol(client):
    id = seed(invoice_number="FV-2026/77", vat_amount=1200.0, total_amount=11200.0, tax_date=None)
    response = export(client, [id], "money")
    assert response.status_code == 200
    invoice = ET.fromstring(response.content).find("SeznamFaktPrij/FaktPrij")
    assert invoice.find("VarSymbol") is None  # not numeric, kept only as the supplier's number
    assert invoice.findtext("PrijatDokl") == "FV-2026/77"
    assert invoice.findtext("PlnenoDPH") == "2026-10-01"  # no DUZP: issue date
    assert invoice.findtext("DatUcPr") == "2026-10-01"
    assert invoice.findtext("SouhrnDPH/Zaklad5") == "10000.00"
    assert invoice.findtext("SouhrnDPH/DPH5") == "1200.00"
    assert invoice.findtext("SouhrnDPH/Zaklad22") == "0.00"
    assert invoice.findtext("Celkem") == "11200.00"
    assert invoice.findtext("DodOdb/ICO") == "27074358"


@pytest.mark.parametrize(
    "overrides",
    [
        {"currency": "EUR"},
        {"vat_amount": 1500.0, "total_amount": 11500.0},  # mixed rates
        {"total_amount": 13000.0},
        {"invoice_number": None},
    ],
)
def test_refuses_what_cannot_be_imported_safely(client, overrides):
    response = export(client, [seed(**overrides)], "pohoda")
    assert response.status_code == 409
    assert "Zaúčtujte ručně" in response.json()["detail"]


def test_only_approved_invoices(client):
    assert export(client, [seed(status="needs_review")], "money").status_code == 409
    assert export(client, [seed(template_id="lease")], "money").status_code == 409
    assert export(client, [seed()], "csv").status_code == 422
