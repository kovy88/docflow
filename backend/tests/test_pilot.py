import io
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import settings
from app.db import Document, DocumentFile, Extraction, engine, utcnow
from app.main import app
from app.templates import load_templates
from app.xlsx import workbook

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def seed(status="approved", issues=None, reviewed=True, filename="nájem.pdf"):
    template = load_templates()["lease"]
    with Session(engine) as session:
        doc = Document(filename=filename, page_count=1, pages=["Test"])
        session.add(doc)
        session.flush()
        session.add(DocumentFile(document_id=doc.id, data=b"%PDF-test"))
        fields = [
            {"key": f.key, "label": f.label, "type": f.type, "value": None} for f in template.fields
        ]
        for f in fields:
            f["value"] = {
                "tenant": '=HYPERLINK("https://example.com")',
                "rent": 12500.5,
                "start_date": "2026-10-01",
            }.get(f["key"])
        extraction = Extraction(
            document_id=doc.id,
            template_id=template.id,
            template=template.model_dump(),
            model="fake",
            status=status,
            fields=fields,
            issues=issues or [],
            usage={},
            reviewed_at=utcnow() if reviewed else None,
        )
        session.add(extraction)
        session.commit()
        return doc.id, extraction.id


def sheet(content):
    with ZipFile(io.BytesIO(content)) as archive:
        return ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))


def test_batch_export_native_types_and_columns(client):
    _, first = seed()
    _, second = seed(filename="druhá.pdf")
    response = client.post(
        "/exports/xlsx",
        json={
            "extraction_ids": [second, first],
            "columns": ["tenant", "rent", "start_date", "end_date"],
        },
    )
    assert response.status_code == 200
    root = sheet(response.content)
    rows = root.findall("s:sheetData/s:row", NS)
    assert len(rows) == 3
    assert rows[0].findall("s:c/s:is/s:t", NS)[2].text == "Nájemce"
    cells = {c.attrib["r"]: c for c in rows[1]}
    assert cells["A2"].find("s:is/s:t", NS).text == "druhá.pdf"
    assert cells["C2"].attrib["t"] == "inlineStr"
    assert root.find(".//s:f", NS) is None  # model text cannot become a formula
    assert cells["D2"].find("s:v", NS).text == "12500.5"
    assert cells["E2"].attrib["s"] == "2"
    assert "F2" not in cells  # missing date stays blank
    assert cells["B2"].attrib["s"] == "2"


@pytest.mark.parametrize(
    "status,issues,reviewed",
    [
        ("ok", [], False),
        ("needs_review", [], False),
        ("running", [], False),
        ("failed", [], False),
        ("approved", [], False),
        ("approved", [{"severity": "error"}], True),
    ],
)
def test_batch_export_refuses_entire_unreviewed_batch(client, status, issues, reviewed):
    _, good = seed()
    _, bad = seed(status=status, issues=issues, reviewed=reviewed)
    assert client.post("/exports/xlsx", json={"extraction_ids": [good, bad]}).status_code == 409


def test_export_invalid_selection_and_schema(client):
    _, id = seed()
    for body in [
        {"extraction_ids": []},
        {"extraction_ids": [id, id]},
        {"extraction_ids": [id], "columns": ["unknown"]},
        {"extraction_ids": [id], "columns": ["rent", "rent"]},
    ]:
        assert client.post("/exports/xlsx", json=body).status_code == 422
    assert client.post("/exports/xlsx", json={"extraction_ids": [999999]}).status_code == 404
    _, other = seed()
    with Session(engine) as session:
        row = session.get(Extraction, other)
        row.template = {
            **row.template,
            "fields": [{**f, "label": "Jiné"} for f in row.template["fields"]],
        }
        session.add(row)
        session.commit()
    assert client.post("/exports/xlsx", json={"extraction_ids": [id, other]}).status_code == 409


def test_delete_removes_pdf_text_and_all_extractions(client):
    doc, id = seed()
    assert client.delete(f"/documents/{doc}").status_code == 204
    for path in [
        f"/documents/{doc}",
        f"/documents/{doc}/file",
        f"/documents/{doc}/text",
        f"/extractions/{id}",
    ]:
        assert client.get(path).status_code == 404
    with Session(engine) as session:
        assert session.get(DocumentFile, doc) is None
    assert client.delete(f"/documents/{doc}").status_code == 404


def test_running_cannot_be_deleted(client):
    doc, _ = seed(status="running", reviewed=False)
    assert client.delete(f"/documents/{doc}").status_code == 409


def test_new_routes_require_access(client, monkeypatch):
    doc, id = seed()
    monkeypatch.setattr(settings, "access_code", "pilot-secret")
    assert client.post("/exports/xlsx", json={"extraction_ids": [id]}).status_code == 401
    assert client.delete(f"/documents/{doc}").status_code == 401
    assert client.get("/templates/lease").status_code == 401
    assert (
        client.post(
            "/exports/xlsx",
            json={"extraction_ids": [id]},
            headers={"X-Access-Code": "pilot-secret"},
        ).status_code
        == 200
    )


def test_workbook_preserves_unicode_and_strips_xml_controls():
    root = sheet(workbook(["Nájemce", "Datum"], [["Žluťoučký <&>\x01", date(2026, 10, 19)]]))
    assert root.find("s:sheetData/s:row[@r='2']/s:c/s:is/s:t", NS).text == "Žluťoučký <&>"


def test_restart_marks_pending_work_failed(client):
    _, id = seed(status="running", reviewed=False)
    with TestClient(app) as restarted:
        result = restarted.get(f"/extractions/{id}").json()
        assert result["status"] == "failed"
        assert result["issues"][0]["code"] == "interrupted"


def test_pilot_cannot_start_without_access_code():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="PILOT_MODE requires"):
        Settings(pilot_mode=True, access_code=" ", _env_file=None)


def test_lease_demo_is_available_but_separate_from_evaluation(client):
    import json

    root = Path(__file__).parents[1]
    labels = json.loads((root / "dataset/labels.json").read_text())
    assert not any(label["template_id"] == "lease" for label in labels)
    demo = client.get("/demo").json()
    assert any(d["file"] == "najem_pilot.pdf" and d["template_id"] == "lease" for d in demo)
    response = client.post("/demo/najem_pilot.pdf")
    assert response.status_code == 201
    assert (
        client.get(f"/documents/{response.json()['id']}/file").content
        == (root / "samples/najem_pilot.pdf").read_bytes()
    )


def test_lease_upload_review_export_flow(client):
    from app.main import get_extractor
    from tests.test_api import FakeExtractor, extract, upload

    path = Path(__file__).parents[1] / "samples/najem_pilot.pdf"
    doc = upload(client, path).json()
    answers = {
        "landlord": "Ukázkový Pronajímatel s.r.o.",
        "tenant": "Ukázkový Nájemce s.r.o.",
        "property_address": "Ukázková 123, 602 00 Brno",
        "start_date": "1. 11. 2026",
        "rent": "15 000 Kč",
        "currency": "CZK",
        "payment_period": "měsíčně",
        "service_advance": "3 000 Kč",
        "deposit": "30 000 Kč",
        "notice_period": "3 měsíce",
    }
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(
        {key: (value, 1, value, 0.9) for key, value in answers.items()}
    )
    result = extract(client, doc["id"], {"template_id": "lease"})
    assert result["status"] == "ok", result["issues"]
    assert next(f for f in result["fields"] if f["key"] == "end_date")["value"] is None
    assert client.post("/exports/xlsx", json={"extraction_ids": [result["id"]]}).status_code == 409
    reviewed = client.patch(f"/extractions/{result['id']}", json={"values": {"rent": "15 500 Kč"}})
    assert reviewed.status_code == 200 and reviewed.json()["issues"] == []
    response = client.post("/exports/xlsx", json={"extraction_ids": [result["id"]]})
    assert response.status_code == 200
    assert sheet(response.content).find(".//s:c[@r='H2']/s:v", NS).text == "15500.0"
