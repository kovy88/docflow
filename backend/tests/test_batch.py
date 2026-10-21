import pytest
from sqlmodel import Session, delete, func, select

from app.config import settings
from app.db import Extraction, UsageEvent, engine
from app.main import MEASUREMENT_COLUMNS, app, get_extractor
from tests.test_api import FakeExtractor, upload

PREMIUM = {"annual_premium": ("48 600 Kč", 1, "Roční pojistné: 48 600 Kč", 0.9)}


@pytest.fixture(autouse=True)
def fresh_quota(client):
    with Session(engine) as session:
        session.exec(delete(UsageEvent))
        session.commit()


def usage_events() -> int:
    with Session(engine) as session:
        return session.exec(select(func.count()).select_from(UsageEvent)).one()


def test_batch_extraction_and_document_status(client, samples):
    ids = [upload(client, samples / "pojistka.pdf").json()["id"] for _ in range(2)]
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(PREMIUM)
    r = client.post(
        "/extractions/batch", json={"document_ids": ids, "template_id": "insurance_policy"}
    )
    assert r.status_code == 202, r.text
    assert [e["document_id"] for e in r.json()] == ids
    assert usage_events() == 2
    listed = {d["id"]: d for d in client.get("/documents").json()}
    for id, extraction in zip(ids, r.json(), strict=True):
        latest = listed[id]["latest_extraction"]
        assert latest["id"] == extraction["id"]
        assert latest["template_id"] == "insurance_policy"
        assert latest["status"] in ("ok", "needs_review")
        assert latest["reviewed_at"] is None


def test_batch_is_all_or_nothing(client, samples, monkeypatch):
    doc = upload(client, samples / "pojistka.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(PREMIUM)
    with Session(engine) as session:
        before = session.exec(select(func.count()).select_from(Extraction)).one()

    def batch(ids, template_id="insurance_policy"):
        return client.post(
            "/extractions/batch", json={"document_ids": ids, "template_id": template_id}
        )

    assert batch([doc["id"], 999_999]).status_code == 404
    assert batch([doc["id"]], "nope").status_code == 404
    assert batch([doc["id"], doc["id"]]).status_code == 422
    assert batch(list(range(1, 102))).status_code == 422
    assert batch([]).status_code == 422
    monkeypatch.setattr(settings, "daily_llm_limit", 1)
    other = upload(client, samples / "pojistka.pdf").json()
    r = batch([doc["id"], other["id"]])
    assert r.status_code == 429 and "1 dokumentů" in r.json()["detail"]
    assert usage_events() == 0
    with Session(engine) as session:
        assert session.exec(select(func.count()).select_from(Extraction)).one() == before


def test_review_report(client, samples):
    doc = upload(client, samples / "pojistka.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(PREMIUM)
    [queued] = client.post(
        "/extractions/batch", json={"document_ids": [doc["id"]], "template_id": "insurance_policy"}
    ).json()
    id = queued["id"]

    def report():
        rows = client.get("/reports/review?template_id=insurance_policy").json()["rows"]
        return next((r for r in rows if r["extraction_id"] == id), None)

    assert report() is None  # not reviewed yet
    r = client.patch(
        f"/extractions/{id}", json={"values": {"annual_premium": "49 000 Kč"}, "review_seconds": 40}
    )
    assert r.status_code == 200 and r.json()["usage"]["review_s"] == 40
    # A second pass adds to the time; the correction from the first pass stays counted.
    r = client.patch(f"/extractions/{id}", json={"values": {}, "review_seconds": 5.04})
    assert r.json()["usage"]["review_s"] == 45
    row = report()
    assert row["fields_corrected"] == 1 and row["fields_total"] == len(queued["template"]["fields"])
    assert row["review_s"] == 45
    invoices = client.get("/reports/review?template_id=invoice").json()["rows"]
    assert all(r["template_id"] == "invoice" for r in invoices)
    summary = client.get("/reports/review").json()["summary"]
    assert summary["documents"] >= 1 and 0 <= summary["accuracy_before_review"] <= 1

    csv_text = client.get("/reports/review?format=csv").text
    lines = csv_text.lstrip("﻿").splitlines()
    assert lines[0] == ",".join(MEASUREMENT_COLUMNS)
    mine = next(line for line in lines if line.startswith(f"doc-{doc['id']},"))
    values = dict(zip(MEASUREMENT_COLUMNS, mine.split(","), strict=True))
    assert values["review_and_fix_seconds"] == "45.0"
    assert values["fields_wrong_before_review"] == "1"
    assert values["manual_seconds"] == ""
    assert client.get("/reports/review?format=xml").status_code == 422


def test_review_seconds_are_bounded(client, samples):
    doc = upload(client, samples / "pojistka.pdf").json()
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(PREMIUM)
    [queued] = client.post(
        "/extractions/batch", json={"document_ids": [doc["id"]], "template_id": "insurance_policy"}
    ).json()
    for bad in (-1, 4 * 3600 + 1):
        r = client.patch(f"/extractions/{queued['id']}", json={"review_seconds": bad})
        assert r.status_code == 422


def test_batch_progress_is_visible_while_model_runs(client, samples):
    ids = [upload(client, samples / "pojistka.pdf").json()["id"] for _ in range(2)]
    observations = []

    class InspectingExtractor(FakeExtractor):
        def extract(self, pages, template):
            with Session(engine) as session:
                rows = session.exec(
                    select(Extraction)
                    .where(Extraction.document_id.in_(ids))
                    .order_by(Extraction.id)
                ).all()
                observations.append([row.usage["phase"] for row in rows])
                active = next(row for row in rows if row.usage["phase"] == "processing")
                assert active.usage["started_at"]
                assert active.usage["queue_wait_s"] >= 0
            return super().extract(pages, template)

    app.dependency_overrides[get_extractor] = lambda: InspectingExtractor(PREMIUM)
    response = client.post(
        "/extractions/batch", json={"document_ids": ids, "template_id": "insurance_policy"}
    )
    assert response.status_code == 202
    assert all(row["usage"]["phase"] == "queued" for row in response.json())
    assert observations == [["processing", "queued"], ["completed", "processing"]]
    for row in response.json():
        finished = client.get(f"/extractions/{row['id']}").json()
        assert finished["usage"]["phase"] == "completed"
        assert finished["usage"]["started_at"]
        assert finished["usage"]["latency_s"] >= 0


def test_failed_model_keeps_progress_timing(client, samples):
    doc = upload(client, samples / "pojistka.pdf").json()

    class FailingExtractor(FakeExtractor):
        def extract(self, pages, template):
            raise RuntimeError("Model unavailable")

    app.dependency_overrides[get_extractor] = lambda: FailingExtractor({})
    queued = client.post(
        f"/documents/{doc['id']}/extractions", json={"template_id": "insurance_policy"}
    ).json()
    result = client.get(f"/extractions/{queued['id']}").json()
    assert result["status"] == "failed"
    assert result["usage"]["phase"] == "failed"
    assert result["usage"]["started_at"]
    assert result["usage"]["queue_wait_s"] >= 0
