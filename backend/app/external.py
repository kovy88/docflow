"""Shared by the public trial page and the integrator API: documents that live outside the
workspace (ExternalDocument), their results with sources and rule outcomes, exports and
the 24 h deletion of trial data."""

import hashlib
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete
from sqlmodel import Session, select

from app import accounting
from app.accounting import AccountingError
from app.db import ExternalDocument, ExternalExtraction, engine, utcnow
from app.runtime import process_extraction
from app.templates import DateOrderRule, IcoRule, SumRule, Template
from app.xlsx import workbook

STATUS_LABELS = {
    "running": "Zpracovává se",
    "ok": "Ověřeno",
    "needs_review": "Ke kontrole",
    "approved": "Schváleno",
    "failed": "Chyba",
}


class FieldOut(BaseModel):
    key: str
    label: str
    type: str
    value: str | float | None  # normalised: numbers as numbers, dates as YYYY-MM-DD
    raw: str | None  # as written in the document
    page: int | None  # 1-based page of the source
    quote: str | None  # verbatim text from the document that contains the value
    confidence: float
    grounded: bool  # the quote was found in the PDF and contains the value
    corrected: bool = False


class IssueOut(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    fields: list[str]


class RuleOut(BaseModel):
    type: Literal["ico", "sum", "date_order"]
    description: str
    fields: list[str]
    result: Literal["passed", "failed", "skipped"]  # skipped: a value is missing


class ExtractionOut(BaseModel):
    id: str
    document_id: str
    template_id: str
    status: Literal["running", "ok", "needs_review", "approved", "failed"]
    model: str
    created_at: datetime
    reviewed_at: datetime | None
    latency_s: float | None
    fields: list[FieldOut]
    issues: list[IssueOut]
    rules: list[RuleOut]


def aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything here is UTC."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


RULE_CODES = {"ico": "ico_checksum", "sum": "sum_mismatch", "date_order": "date_order"}


def rule_results(template: Template, fields: list[dict], issues: list[dict]) -> list[RuleOut]:
    labels = {f.key: f.label for f in template.fields}
    values = {f["key"]: f["value"] for f in fields}
    results = []
    for rule in template.rules:
        if isinstance(rule, IcoRule):
            keys = [rule.field]
            text = f"Kontrolní součet: {labels[rule.field]}"
        elif isinstance(rule, SumRule):
            keys = [*rule.fields, rule.equals]
            text = " + ".join(labels[k] for k in rule.fields) + f" = {labels[rule.equals]}"
        elif isinstance(rule, DateOrderRule):
            keys = [rule.earlier, rule.later]
            text = f"{labels[rule.earlier]} není po {labels[rule.later]}"
        failed = any(i["code"] == RULE_CODES[rule.type] and i["fields"] == keys for i in issues)
        if failed:
            result = "failed"
        elif any(values.get(k) is None for k in keys):
            result = "skipped"
        else:
            result = "passed"
        results.append(RuleOut(type=rule.type, description=text, fields=keys, result=result))
    return results


def extraction_out(extraction: ExternalExtraction) -> ExtractionOut:
    template = Template.model_validate(extraction.template)
    finished = extraction.status not in ("running", "failed")
    return ExtractionOut(
        id=extraction.id,
        document_id=extraction.document_id,
        template_id=extraction.template_id,
        status=extraction.status,
        model=extraction.model,
        created_at=aware(extraction.created_at),
        reviewed_at=aware(extraction.reviewed_at),
        latency_s=extraction.usage.get("latency_s"),
        fields=[FieldOut.model_validate(f) for f in extraction.fields],
        issues=[IssueOut.model_validate(i) for i in extraction.issues],
        rules=rule_results(template, extraction.fields, extraction.issues) if finished else [],
    )


def export_response(extraction: ExternalExtraction, doc: ExternalDocument, format: str) -> Response:
    """json and xlsx for any finished result; pohoda and money only for an approved
    invoice without errors (nothing goes into accounting without a human check)."""
    if extraction.status == "running":
        raise HTTPException(409, "Extrakce ještě běží.")
    if extraction.status == "failed":
        raise HTTPException(409, "Extrakce selhala, není co exportovat.")
    name = f"docflow-{extraction.template_id}-{extraction.id[:8]}"
    if format == "json":
        payload = extraction_out(extraction).model_dump(mode="json")
        return JSONResponse(
            payload, headers={"Content-Disposition": f'attachment; filename="{name}.json"'}
        )
    if format == "xlsx":
        template = Template.model_validate(extraction.template)
        values = {f["key"]: f["value"] for f in extraction.fields}
        row: list[Any] = [doc.filename, STATUS_LABELS[extraction.status]]
        for spec in template.fields:
            value = values.get(spec.key)
            if value is not None and spec.type == "date":
                value = date.fromisoformat(value)
            row.append(value)
        headers = ["Soubor", "Stav", *[f.label for f in template.fields]]
        return Response(
            workbook(headers, [row]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    if format not in ("pohoda", "money"):
        raise HTTPException(422, "format musí být xlsx, json, pohoda nebo money.")
    if extraction.template_id != "invoice":
        raise HTTPException(409, "Do účetnictví lze exportovat jen šablonu invoice (faktura).")
    if extraction.status != "approved" or any(i["severity"] == "error" for i in extraction.issues):
        raise HTTPException(
            409, "Export do účetnictví vyžaduje schválenou extrakci bez chybových nálezů."
        )
    try:
        invoice = accounting.invoice({f["key"]: f["value"] for f in extraction.fields})
    except (AccountingError, ValueError) as exc:
        raise HTTPException(409, f"{exc}. Zaúčtujte ručně.") from exc
    if format == "pohoda":
        content = accounting.pohoda([invoice], f"docflow-{utcnow():%Y%m%d%H%M%S}")
    else:
        content = accounting.money_s3([invoice])
    return Response(
        content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{name}-{format}.xml"'},
    )


def delete_document(session: Session, doc_id: str) -> None:
    session.execute(delete(ExternalExtraction).where(ExternalExtraction.document_id == doc_id))
    session.execute(delete(ExternalDocument).where(ExternalDocument.id == doc_id))
    session.commit()


def purge_expired(session: Session | None = None) -> int:
    """Delete expired documents with their text and results. Returns how many."""
    if session is None:
        with Session(engine) as own:
            return purge_expired(own)
    expired = select(ExternalDocument.id).where(
        ExternalDocument.expires_at.is_not(None), ExternalDocument.expires_at <= utcnow()
    )
    ids = list(session.exec(expired).all())
    if ids:
        session.execute(delete(ExternalExtraction).where(ExternalExtraction.document_id.in_(ids)))
        session.execute(delete(ExternalDocument).where(ExternalDocument.id.in_(ids)))
        session.commit()
    return len(ids)


async def read_upload(file, max_mb: int) -> bytes:
    """Read at most one byte over the limit, so an oversized upload is not held in memory."""
    return await file.read(max_mb * 1024 * 1024 + 1)


def store_document(session: Session, data: bytes, pdf, filename: str, **owner) -> ExternalDocument:
    doc = ExternalDocument(
        filename=filename[:200],
        page_count=len(pdf.pages),
        pages=pdf.pages,
        ocr_pages=pdf.ocr_pages,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        **owner,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def queue_extraction(
    session: Session, doc: ExternalDocument, template: Template, extractor, background
) -> ExternalExtraction:
    extraction = ExternalExtraction(
        document_id=doc.id,
        api_key_id=doc.api_key_id,
        template_id=template.id,
        template=template.model_dump(),
        model=extractor.model,
        status="running",
        fields=[],
        issues=[],
        usage={"phase": "queued"},
    )
    session.add(extraction)
    session.commit()
    session.refresh(extraction)
    background.add_task(
        process_extraction,
        extraction.id,
        doc.pages,
        doc.ocr_pages or [],
        template,
        extractor,
        ExternalExtraction,
    )
    return extraction
