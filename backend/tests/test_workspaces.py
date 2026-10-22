import json

from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from app import db
from app.config import settings
from app.db import Document, Workspace, WorkspaceTemplate, engine, init_db
from app.main import app, get_extractor
from app.templates import load_templates
from app.workspaces import create_workspace
from tests.test_api import BAD_INVOICE, FakeExtractor


def new_workspace(name, quota=None):
    with Session(engine) as session:
        workspace, code = create_workspace(session, name, quota)
        return workspace.id, {"X-Access-Code": code}


def upload(client, samples, headers):
    with (samples / "faktura_chyby.pdf").open("rb") as f:
        files = {"file": ("faktura.pdf", f, "application/pdf")}
        r = client.post("/documents", files=files, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def extract(client, document_id, headers):
    body = {"template_id": "invoice"}
    return client.post(f"/documents/{document_id}/extractions", json=body, headers=headers)


def test_workspaces_do_not_see_each_others_data(client, samples):
    _, a = new_workspace("Firma A")
    _, b = new_workspace("Firma B")
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(BAD_INVOICE)
    doc = upload(client, samples, a)
    extraction = extract(client, doc, a).json()["id"]
    fixes = {
        "values": {
            "due_date": "6. 10. 2026",
            "total_amount": "12 100,00",
            "currency": "CZK",
            "supplier_ico": "27082440",
        }
    }
    approved = client.patch(f"/extractions/{extraction}", json=fixes, headers=a).json()
    assert approved["status"] == "approved", approved["issues"]

    assert client.get("/access", headers=a).json()["workspace"] == "Firma A"
    assert [d["id"] for d in client.get("/documents", headers=a).json()] == [doc]
    assert client.get("/documents", headers=b).json() == []
    assert doc not in [d["id"] for d in client.get("/documents").json()]  # default workspace
    for path in (
        f"/documents/{doc}",
        f"/documents/{doc}/file",
        f"/documents/{doc}/text",
        f"/documents/{doc}/extractions",
        f"/extractions/{extraction}",
        f"/extractions/{extraction}/export",
    ):
        assert client.get(path, headers=b).status_code == 404, path
    assert extract(client, doc, b).status_code == 404
    assert client.patch(f"/extractions/{extraction}", json={}, headers=b).status_code == 404
    export = {"extraction_ids": [extraction]}
    assert client.post("/exports/xlsx", json=export, headers=b).status_code == 404
    assert client.post("/exports/xlsx", json=export, headers=a).status_code == 200
    assert client.get("/reports/review", headers=b).json()["summary"]["documents"] == 0
    assert client.get("/reports/review", headers=a).json()["summary"]["documents"] == 1
    assert client.delete(f"/documents/{doc}", headers=b).status_code == 404


def test_templates_belong_to_one_workspace(client):
    _, a = new_workspace("Šablony A")
    _, b = new_workspace("Šablony B")
    template = load_templates()["lease"].model_dump() | {"id": "najem_firmy", "name": "Nájem"}
    assert client.post("/templates", json=template, headers=a).status_code == 201
    assert client.get("/templates/najem_firmy", headers=b).status_code == 404
    assert "najem_firmy" not in [t["id"] for t in client.get("/templates", headers=b).json()]
    # The same id in another firm is a different template, not a conflict.
    assert client.post("/templates", json=template | {"name": "B"}, headers=b).status_code == 201
    assert client.delete("/templates/najem_firmy", headers=b).status_code == 204
    assert client.get("/templates/najem_firmy", headers=a).json()["name"] == "Nájem"


def test_access_code_and_workspace_codes_together(client, monkeypatch):
    monkeypatch.setattr(settings, "access_code", "spolecny-kod")
    workspace_id, headers = new_workspace("Pilot")
    assert client.get("/documents").status_code == 401
    assert client.get("/documents", headers={"X-Access-Code": "spatne"}).status_code == 401
    default = client.get("/access", headers={"X-Access-Code": "spolecny-kod"}).json()
    assert default["workspace"] == "Výchozí"
    assert client.get("/access", headers=headers).json()["workspace"] == "Pilot"

    with Session(engine) as session:
        workspace = session.get(Workspace, workspace_id)
        workspace.revoked_at = db.utcnow()
        session.add(workspace)
        session.commit()
    r = client.get("/documents", headers=headers)
    assert r.status_code == 401 and "ukončen" in r.json()["detail"]


def test_workspace_daily_quota(client, samples, monkeypatch):
    monkeypatch.setattr(settings, "daily_llm_limit", 100_000)
    _, headers = new_workspace("Malý tarif", quota=1)
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(BAD_INVOICE)
    doc = upload(client, samples, headers)
    assert client.get("/access", headers=headers).json()["llm_calls_left_today"] == 1
    assert extract(client, doc, headers).status_code == 202
    assert extract(client, doc, headers).status_code == 429
    batch = {"document_ids": [doc], "template_id": "invoice"}
    assert client.post("/extractions/batch", json=batch, headers=headers).status_code == 429
    assert client.get("/access", headers=headers).json()["llm_calls_left_today"] == 0
    other = new_workspace("Jiná firma")[1]  # the quota is per workspace
    assert extract(client, upload(client, samples, other), other).status_code == 202


def test_existing_database_moves_into_default_workspace(tmp_path, monkeypatch):
    """A database from before workspaces: documents without workspace_id and templates in
    the single-tenant customtemplate table."""
    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    template = load_templates()["lease"].model_dump() | {"id": "stary_najem"}
    with legacy.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE document (id INTEGER PRIMARY KEY, filename VARCHAR NOT NULL, "
                "page_count INTEGER NOT NULL, pages JSON, ocr_pages JSON, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO document VALUES "
                "(1, 'stara.pdf', 1, '[\"text\"]', '[]', '2026-10-01 08:00:00')"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE usageevent (id INTEGER PRIMARY KEY, kind VARCHAR NOT NULL, "
                "created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE customtemplate (id VARCHAR PRIMARY KEY, data JSON, "
                "created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO customtemplate VALUES "
                "('stary_najem', :data, '2026-10-01 08:00:00', '2026-10-02 08:00:00')"
            ),
            {"data": json.dumps(template)},
        )
    monkeypatch.setattr(db, "engine", legacy)
    init_db()
    init_db()  # idempotent: a restart copies nothing twice
    with Session(legacy) as session:
        default = db.default_workspace(session)
        assert session.get(Document, 1).workspace_id == default.id
        rows = session.exec(select(WorkspaceTemplate)).all()
        assert [(r.workspace_id, r.template_id) for r in rows] == [(default.id, "stary_najem")]
        assert rows[0].data["id"] == "stary_najem"
