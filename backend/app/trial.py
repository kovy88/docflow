"""Public trial page (/vyzkouset in the frontend): own anonymised PDF without an access
code. Limited per IP and per day, stored apart from the workspace, deleted after 24 h and
never used for evaluation or training."""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlmodel import Session, func, select

from app import external
from app.config import settings
from app.db import ExternalDocument, ExternalExtraction, TrialVisit, utcnow
from app.external import ExtractionOut, aware
from app.extractor import Extractor
from app.runtime import SessionDep, consume_llm_quota, get_extractor, parse_pdf
from app.templates import load_templates

router = APIRouter(prefix="/trial", tags=["trial"], include_in_schema=False)

TRIAL_TEMPLATES = ("invoice", "lease", "transport_order")
REF = re.compile(r"[a-z0-9_-]{1,40}")


class TrialOut(ExtractionOut):
    filename: str
    page_count: int
    expires_at: datetime


def client_ip(request: Request) -> str:
    if settings.client_ip_header:
        forwarded = request.headers.get(settings.client_ip_header)
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def ip_hash(ip: str) -> str:
    return hashlib.sha256(f"{settings.ip_hash_salt}:{ip}".encode()).hexdigest()


def clean_ref(ref: str | None) -> str | None:
    ref = (ref or "").strip().lower()
    return ref if REF.fullmatch(ref) else None


def check_limits(session: Session, ip: str) -> None:
    since = utcnow() - timedelta(hours=24)
    recent = (
        select(func.count())
        .select_from(ExternalDocument)
        .where(ExternalDocument.source == "trial", ExternalDocument.created_at >= since)
    )
    if session.exec(recent.where(ExternalDocument.ip_hash == ip)).one() >= settings.trial_per_ip:
        raise HTTPException(
            429,
            f"Z této adresy jste za posledních 24 hodin vyzkoušeli už {settings.trial_per_ip} "
            "dokumenty. Pro další mi napište, rád vám zpřístupním plnou verzi.",
        )
    if session.exec(recent).one() >= settings.trial_daily_limit:
        raise HTTPException(429, "Zkušební verze má pro dnešek vyčerpaný limit, zkuste to zítra.")


def trial_out(extraction: ExternalExtraction, doc: ExternalDocument) -> TrialOut:
    return TrialOut(
        **external.extraction_out(extraction).model_dump(),
        filename=doc.filename,
        page_count=doc.page_count,
        expires_at=aware(doc.expires_at),
    )


def get_trial(session: Session, extraction_id: str) -> tuple[ExternalExtraction, ExternalDocument]:
    """Only trial results are readable here; API documents need their key."""
    extraction = session.get(ExternalExtraction, extraction_id)
    doc = extraction and session.get(ExternalDocument, extraction.document_id)
    if doc is None or doc.source != "trial" or aware(doc.expires_at) <= utcnow():
        raise HTTPException(404, "Výsledek neexistuje nebo už byl smazán.")
    return extraction, doc


@router.post("", status_code=202)
async def create_trial(
    request: Request,
    file: UploadFile,
    session: SessionDep,
    background: BackgroundTasks,
    llm: Annotated[Extractor, Depends(get_extractor)],
    template_id: Annotated[str, Form()],
    ref: Annotated[str | None, Form()] = None,
) -> TrialOut:
    if not settings.trial_enabled:
        raise HTTPException(404, "Zkušební verze je vypnutá.")
    if template_id not in TRIAL_TEMPLATES:
        raise HTTPException(422, f"Typ dokumentu musí být jeden z: {', '.join(TRIAL_TEMPLATES)}.")
    external.purge_expired(session)
    visitor = ip_hash(client_ip(request))
    check_limits(session, visitor)
    data = await external.read_upload(file, settings.trial_max_mb)
    pdf = await run_in_threadpool(parse_pdf, data, settings.trial_max_mb, settings.trial_max_pages)
    if not any(pdf.pages):
        raise HTTPException(422, "V PDF se nepodařilo najít žádný text.")
    consume_llm_quota(session, "trial_extraction")
    doc = external.store_document(
        session,
        data,
        pdf,
        file.filename or "dokument.pdf",
        source="trial",
        ip_hash=visitor,
        ref=clean_ref(ref),
        expires_at=utcnow() + timedelta(hours=settings.trial_retention_h),
    )
    session.add(TrialVisit(ref=doc.ref, template_id=template_id, page_count=doc.page_count))
    template = load_templates()[template_id]
    extraction = external.queue_extraction(session, doc, template, llm, background)
    return trial_out(extraction, doc)


@router.get("/{extraction_id}")
def read_trial(extraction_id: str, session: SessionDep) -> TrialOut:
    return trial_out(*get_trial(session, extraction_id))


@router.get("/{extraction_id}/export")
def export_trial(extraction_id: str, session: SessionDep, format: str = "json"):
    if format not in ("json", "xlsx"):
        raise HTTPException(422, "format musí být json nebo xlsx.")
    return external.export_response(*get_trial(session, extraction_id), format)


@router.delete("/{extraction_id}", status_code=204)
def delete_trial(extraction_id: str, session: SessionDep) -> None:
    """The visitor can delete the document right away instead of waiting 24 h."""
    _, doc = get_trial(session, extraction_id)
    external.delete_document(session, doc.id)
