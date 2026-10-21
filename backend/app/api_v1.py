"""Integrator API, mounted at /v1 as its own app: its OpenAPI (/v1/docs) lists only these
endpoints. Every client has its own key (Authorization: Bearer), daily quota and
documents; one client never sees another's documents, nor the workspace."""

import hashlib
import secrets
from datetime import datetime
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import external
from app.config import settings
from app.db import ApiKey, ExternalDocument, ExternalExtraction, utcnow
from app.external import ExtractionOut, aware
from app.extractor import Extractor
from app.pipeline import apply_review
from app.runtime import SessionDep, consume_llm_quota, get_extractor, parse_pdf
from app.templates import Template, load_templates

app = FastAPI(
    title="Docflow API",
    version="1.0.0",
    description=(
        "Vytěžování dat z PDF s citací zdroje, stranou, jistotou a kontrolou pravidel. "
        "Autentizace hlavičkou `Authorization: Bearer <klíč>`. Chyby mají vždy tvar "
        '`{"error": {"code": "...", "message": "..."}}`.'
    ),
)

CODES = {
    400: "bad_request",
    401: "unauthorized",
    404: "not_found",
    409: "conflict",
    413: "file_too_large",
    415: "unsupported_media_type",
    422: "invalid_request",
    429: "limit_exceeded",
    502: "model_error",
    503: "unavailable",
}


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(status, message)
        self.code = code


def error_body(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = getattr(exc, "code", None) or CODES.get(exc.status_code, "error")
    return JSONResponse(error_body(code, str(exc.detail)), exc.status_code, exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(error_body("invalid_request", "Neplatný požadavek.", details=details), 422)


def hash_key(key: str) -> str:
    # Keys are 256 random bits, so a plain SHA-256 is enough (no password stretching).
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(session: Session, client_name: str, daily_quota: int) -> tuple[ApiKey, str]:
    """New key for a client; the plain key is returned only here and never stored."""
    plain = "dfk_" + secrets.token_urlsafe(32)
    key = ApiKey(
        client_name=client_name,
        prefix=plain[:12],
        key_hash=hash_key(plain),
        daily_quota=daily_quota,
    )
    session.add(key)
    session.commit()
    session.refresh(key)
    return key, plain


bearer = HTTPBearer(auto_error=False, description="API klíč od Docflow (dfk_…)")


def api_client(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> ApiKey:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "missing_key", "Chybí hlavička Authorization: Bearer <klíč>.")
    key = session.exec(
        select(ApiKey).where(ApiKey.key_hash == hash_key(credentials.credentials.strip()))
    ).first()
    if key is None:
        raise ApiError(401, "invalid_key", "Neplatný API klíč.")
    if key.revoked_at is not None:
        raise ApiError(401, "revoked_key", "API klíč byl odvolán.")
    return key


Client = Annotated[ApiKey, Depends(api_client)]


class DocumentOut(BaseModel):
    id: str
    filename: str
    page_count: int
    ocr_pages: list[int]  # 1-based pages read by OCR (scans)
    sha256: str
    created_at: datetime


class ExtractRequest(BaseModel):
    template_id: str


class ReviewRequest(BaseModel):
    values: dict[str, str | None] = {}  # corrected raw values by field key


def document_out(doc: ExternalDocument) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        filename=doc.filename,
        page_count=doc.page_count,
        ocr_pages=doc.ocr_pages or [],
        sha256=doc.sha256,
        created_at=aware(doc.created_at),
    )


def owned_document(session: Session, client: ApiKey, document_id: str) -> ExternalDocument:
    doc = session.get(ExternalDocument, document_id)
    if doc is None or doc.source != "api" or doc.api_key_id != client.id:
        raise ApiError(404, "not_found", "Dokument neexistuje.")
    return doc


def owned_extraction(session: Session, client: ApiKey, extraction_id: str) -> ExternalExtraction:
    extraction = session.get(ExternalExtraction, extraction_id)
    if extraction is None or extraction.api_key_id != client.id:
        raise ApiError(404, "not_found", "Extrakce neexistuje.")
    return extraction


def by_idempotency_key(session: Session, client: ApiKey, key: str) -> ExternalDocument | None:
    query = select(ExternalDocument).where(
        ExternalDocument.api_key_id == client.id, ExternalDocument.idempotency_key == key
    )
    return session.exec(query).first()


@app.get("/templates", summary="Seznam šablon")
def list_templates(_: Client) -> list[Template]:
    """Typy dokumentů, které lze vytěžit (`template_id`), s poli a pravidly."""
    return list(load_templates().values())


@app.post(
    "/documents",
    status_code=201,
    summary="Nahrát PDF",
    responses={200: {"description": "Opakované nahrání se stejným Idempotency-Key"}},
)
async def upload_document(
    file: UploadFile,
    session: SessionDep,
    client: Client,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> DocumentOut:
    """Nahraje PDF (nejvýš `API_MAX_MB` MB a `API_MAX_PAGES` stran), přečte text, u skenů
    OCR. Se stejným `Idempotency-Key` a stejným souborem vrátí původní dokument (200)."""
    data = await external.read_upload(file, settings.api_max_mb)
    if idempotency_key and (doc := by_idempotency_key(session, client, idempotency_key)):
        if doc.sha256 != hashlib.sha256(data).hexdigest():
            raise ApiError(
                409, "idempotency_conflict", "Idempotency-Key už byl použit pro jiný soubor."
            )
        response.status_code = 200
        return document_out(doc)
    pdf = await run_in_threadpool(parse_pdf, data, settings.api_max_mb, settings.api_max_pages)
    try:
        doc = external.store_document(
            session,
            data,
            pdf,
            file.filename or "document.pdf",
            source="api",
            api_key_id=client.id,
            idempotency_key=idempotency_key,
        )
    except IntegrityError:  # the same key uploaded concurrently
        session.rollback()
        doc = by_idempotency_key(session, client, idempotency_key)
        response.status_code = 200
    return document_out(doc)


@app.get("/documents/{document_id}", summary="Detail dokumentu")
def read_document(document_id: str, session: SessionDep, client: Client) -> DocumentOut:
    return document_out(owned_document(session, client, document_id))


@app.delete("/documents/{document_id}", status_code=204, summary="Smazat dokument")
def delete_document(document_id: str, session: SessionDep, client: Client) -> None:
    """Smaže PDF, jeho text i všechny extrakce."""
    doc = owned_document(session, client, document_id)
    running = select(ExternalExtraction).where(
        ExternalExtraction.document_id == doc.id, ExternalExtraction.status == "running"
    )
    if session.exec(running).first():
        raise ApiError(409, "extraction_running", "Počkejte na dokončení extrakce.")
    external.delete_document(session, doc.id)


@app.post("/documents/{document_id}/extract", status_code=202, summary="Spustit vytěžení")
def extract_document(
    document_id: str,
    body: ExtractRequest,
    session: SessionDep,
    client: Client,
    background: BackgroundTasks,
    llm: Annotated[Extractor, Depends(get_extractor)],
) -> ExtractionOut:
    """Zařadí vytěžení podle šablony. Běží na pozadí: dotazujte
    `GET /v1/extractions/{id}`, dokud `status` není jiný než `running`."""
    doc = owned_document(session, client, document_id)
    template = load_templates().get(body.template_id)
    if template is None:
        raise ApiError(422, "unknown_template", f"Neznámá šablona {body.template_id!r}.")
    if not any(doc.pages):
        raise ApiError(422, "no_text", "V PDF se nepodařilo najít žádný text.")
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    used = session.exec(
        select(func.count())
        .select_from(ExternalExtraction)
        .where(
            ExternalExtraction.api_key_id == client.id, ExternalExtraction.created_at >= midnight
        )
    ).one()
    if used >= client.daily_quota:
        raise ApiError(
            429, "quota_exceeded", f"Denní kvóta {client.daily_quota} extrakcí je vyčerpaná (UTC)."
        )
    consume_llm_quota(session, "api_extraction")
    return external.extraction_out(
        external.queue_extraction(session, doc, template, llm, background)
    )


@app.get("/extractions/{extraction_id}", summary="Stav a výsledek extrakce")
def read_extraction(extraction_id: str, session: SessionDep, client: Client) -> ExtractionOut:
    """Pole s hodnotou, citací, stranou a jistotou, nálezy (`issues`) a výsledky pravidel."""
    return external.extraction_out(owned_extraction(session, client, extraction_id))


@app.patch("/extractions/{extraction_id}", summary="Schválit (s opravami)")
def review_extraction(
    extraction_id: str, body: ReviewRequest, session: SessionDep, client: Client
) -> ExtractionOut:
    """Lidská kontrola: uloží opravené hodnoty, znovu ověří pravidla a označí výsledek
    jako schválený (`approved`). Bez `values` schválí hodnoty tak, jak jsou."""
    extraction = owned_extraction(session, client, extraction_id)
    if extraction.status in ("running", "failed"):
        raise ApiError(409, "not_reviewable", f"Extrakce je ve stavu {extraction.status}.")
    try:
        result = apply_review(
            Template.model_validate(extraction.template), extraction.fields, body.values
        )
    except ValueError as exc:
        raise ApiError(422, "unknown_field", str(exc)) from exc
    extraction.status, extraction.fields, extraction.issues = (
        result["status"],
        result["fields"],
        result["issues"],
    )
    extraction.reviewed_at = utcnow()
    session.add(extraction)
    session.commit()
    session.refresh(extraction)
    return external.extraction_out(extraction)


@app.get(
    "/extractions/{extraction_id}/export",
    summary="Export",
    response_class=Response,
    responses={200: {"description": "Soubor xlsx, json nebo XML pro POHODU / Money S3"}},
)
def export_extraction(
    extraction_id: str,
    session: SessionDep,
    client: Client,
    format: Literal["xlsx", "json", "pohoda", "money"] = "json",
):
    """`xlsx` a `json` pro každý dokončený výsledek. `pohoda` a `money` (XML import přijaté
    faktury) jen pro šablonu `invoice`, schválenou přes PATCH a bez chybových nálezů."""
    extraction = owned_extraction(session, client, extraction_id)
    doc = session.get(ExternalDocument, extraction.document_id)
    return external.export_response(extraction, doc, format)
