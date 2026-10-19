import csv
import io
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete
from sqlmodel import Session, func, select

from app.checks import issue
from app.config import settings
from app.db import (
    CustomTemplate,
    Document,
    DocumentFile,
    Extraction,
    UsageEvent,
    engine,
    get_session,
    init_db,
    utcnow,
)
from app.demo import demo_path, load_demo_labels
from app.extractor import (
    Extractor,
    OpenAIExtractor,
    OpenRouterExtractor,
    ToolCallExtractor,
    suggest_template,
)
from app.pdf import InvalidPdf, read_pdf
from app.pipeline import apply_review, run_extraction
from app.templates import Template, load_templates
from app.xlsx import workbook


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with Session(engine) as session:  # background work does not survive a restart
        for stale in session.exec(select(Extraction).where(Extraction.status == "running")):
            stale.status = "failed"
            stale.issues = [issue("error", "interrupted", "Extrakce byla přerušena restartem.")]
            session.add(stale)
        session.commit()
    yield


app = FastAPI(title="Docflow", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("docflow")
SessionDep = Annotated[Session, Depends(get_session)]
EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"


def get_extractor() -> Extractor:
    if settings.llm_provider == "ollama":
        return ToolCallExtractor(
            settings.ollama_base_url, settings.ollama_model, settings.llm_timeout_s
        )
    if settings.llm_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise HTTPException(503, "OPENROUTER_API_KEY is not configured")
        return OpenRouterExtractor(
            settings.openrouter_api_key, settings.openrouter_model, settings.llm_timeout_s
        )
    if not settings.openai_api_key:
        raise HTTPException(503, "OPENAI_API_KEY is not configured")
    return OpenAIExtractor(settings.openai_api_key, settings.openai_model, settings.llm_timeout_s)


class DocumentOut(BaseModel):
    id: int
    filename: str
    page_count: int
    has_text: bool
    ocr_pages: list[int]


class ReviewRequest(BaseModel):
    values: dict[str, str | None] = {}


class ExtractionRequest(BaseModel):
    template_id: str | None = None
    template: Template | None = None

    @model_validator(mode="after")
    def one_of(self):
        if (self.template_id is None) == (self.template is None):
            raise ValueError("Provide exactly one of template_id or template")
        return self


def to_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        filename=doc.filename,
        page_count=doc.page_count,
        has_text=any(doc.pages),
        ocr_pages=doc.ocr_pages or [],
    )


def get_document(session: Session, document_id: int) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


def require_access(x_access_code: Annotated[str | None, Header()] = None) -> None:
    """Production guard: everything that touches documents or calls the model needs the
    access code (header X-Access-Code). Without ACCESS_CODE configured the API is open."""
    if settings.access_code and not secrets.compare_digest(
        (x_access_code or "").encode(), settings.access_code.encode()
    ):
        raise HTTPException(401, "Neplatný přístupový kód")


Access = Depends(require_access)


def llm_calls_today(session: Session) -> int:
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    query = select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at >= midnight)
    return session.exec(query).one()


def consume_llm_quota(session: Session, kind: str) -> None:
    """Global daily cap on paid model calls, so a public URL cannot drain the API budget."""
    if llm_calls_today(session) >= settings.daily_llm_limit:
        raise HTTPException(429, "Denní limit volání modelu je vyčerpaný, zkus to zítra.")
    session.add(UsageEvent(kind=kind))
    session.commit()


@app.get("/access", dependencies=[Access])
def check_access(session: SessionDep) -> dict:
    return {"ok": True, "llm_calls_left_today": settings.daily_llm_limit - llm_calls_today(session)}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "model": {
            "ollama": settings.ollama_model,
            "openai": settings.openai_model,
            "openrouter": settings.openrouter_model,
        }[settings.llm_provider],
    }


class TemplateOut(Template):
    builtin: bool


def all_templates(session: Session) -> dict[str, TemplateOut]:
    builtin = {k: TemplateOut(**t.model_dump(), builtin=True) for k, t in load_templates().items()}
    custom = {
        row.id: TemplateOut(**row.data, builtin=False)
        for row in session.exec(select(CustomTemplate).order_by(CustomTemplate.id))
    }
    return builtin | custom


def get_template_or_404(session: Session, template_id: str) -> TemplateOut:
    template = all_templates(session).get(template_id)
    if template is None:
        raise HTTPException(404, "Template not found")
    return template


def get_custom_or_409(session: Session, template_id: str) -> CustomTemplate:
    if template_id in load_templates():
        raise HTTPException(409, "Built-in templates are read-only")
    row = session.get(CustomTemplate, template_id)
    if row is None:
        raise HTTPException(404, "Template not found")
    return row


@app.get("/templates", dependencies=[Access])
def list_templates(session: SessionDep) -> list[TemplateOut]:
    return list(all_templates(session).values())


@app.get("/templates/{template_id}", dependencies=[Access])
def get_template(template_id: str, session: SessionDep) -> TemplateOut:
    return get_template_or_404(session, template_id)


@app.post("/templates", status_code=201, dependencies=[Access])
def create_template(template: Template, session: SessionDep) -> TemplateOut:
    if template.id in all_templates(session):
        raise HTTPException(409, f"Template {template.id!r} already exists")
    session.add(CustomTemplate(id=template.id, data=template.model_dump()))
    session.commit()
    return TemplateOut(**template.model_dump(), builtin=False)


@app.put("/templates/{template_id}", dependencies=[Access])
def update_template(template_id: str, template: Template, session: SessionDep) -> TemplateOut:
    row = get_custom_or_409(session, template_id)
    if template.id != template_id:
        raise HTTPException(422, "Template id cannot be changed")
    row.data, row.updated_at = template.model_dump(), utcnow()
    session.add(row)
    session.commit()
    return TemplateOut(**template.model_dump(), builtin=False)


@app.delete("/templates/{template_id}", status_code=204, dependencies=[Access])
def delete_template(template_id: str, session: SessionDep) -> None:
    session.delete(get_custom_or_409(session, template_id))
    session.commit()


@app.post("/documents/{document_id}/template-suggestion", dependencies=[Access])
def suggest_document_template(
    document_id: int, session: SessionDep, llm: Annotated[Extractor, Depends(get_extractor)]
) -> Template:
    """Let the model propose a template for an unknown document type (not saved)."""
    doc = get_document(session, document_id)
    if not any(doc.pages):
        raise HTTPException(422, "PDF has no text layer")
    consume_llm_quota(session, "template_suggestion")
    try:
        template, _ = suggest_template(llm, doc.pages)
    except Exception as exc:
        raise HTTPException(502, f"Template suggestion failed: {exc}") from exc
    existing = all_templates(session)
    base, n = template.id, 2
    while template.id in existing:
        template.id, n = f"{base}_{n}", n + 1
    return template


def store_pdf(session: Session, data: bytes, filename: str) -> Document:
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File larger than {settings.max_upload_mb} MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "Only PDF files are supported")
    try:
        pdf = read_pdf(data)
    except InvalidPdf as exc:
        raise HTTPException(422, f"Cannot read PDF: {exc}") from exc

    doc = Document(
        filename=filename,
        page_count=len(pdf.pages),
        pages=pdf.pages,
        ocr_pages=pdf.ocr_pages,
    )
    session.add(doc)
    session.flush()
    # The PDF lives in the database: hosting disks (Render) are wiped on every deploy.
    session.add(DocumentFile(document_id=doc.id, data=data))
    session.commit()
    session.refresh(doc)
    return doc


@app.post("/documents", status_code=201, dependencies=[Access])
async def upload_document(file: UploadFile, session: SessionDep) -> DocumentOut:
    data = await file.read()
    # OCR of a scan takes seconds per page; keep it off the event loop.
    doc = await run_in_threadpool(store_pdf, session, data, file.filename or "document.pdf")
    return to_out(doc)


@app.get("/demo")
def list_demo_documents() -> list[dict]:
    """Fictional sample documents from the evaluation set, for trying the app quickly."""
    return [
        {"file": x["file"], "template_id": x["template_id"], "description": x["description"]}
        for x in load_demo_labels()
    ]


@app.post("/demo/{file}", status_code=201, dependencies=[Access])
def create_demo_document(file: str, session: SessionDep) -> DocumentOut:
    if file not in {x["file"] for x in load_demo_labels()}:  # also prevents path traversal
        raise HTTPException(404, "Demo document not found")
    data = demo_path(file).read_bytes()
    # Reopening a sample returns the same document with its extractions, not a fresh copy.
    for doc in session.exec(select(Document).where(Document.filename == file)):
        stored = session.get(DocumentFile, doc.id)
        if stored is not None and stored.data == data:
            return to_out(doc)
    return to_out(store_pdf(session, data, file))


@app.get("/documents", dependencies=[Access])
def list_documents(session: SessionDep) -> list[DocumentOut]:
    docs = session.exec(select(Document).order_by(Document.id.desc())).all()
    return [to_out(d) for d in docs]


@app.get("/documents/{document_id}", dependencies=[Access])
def read_document(document_id: int, session: SessionDep) -> DocumentOut:
    return to_out(get_document(session, document_id))


@app.delete("/documents/{document_id}", status_code=204, dependencies=[Access])
def delete_document(document_id: int, session: SessionDep) -> None:
    doc = get_document(session, document_id)
    running = session.exec(
        select(Extraction).where(
            Extraction.document_id == document_id, Extraction.status == "running"
        )
    ).first()
    if running:
        raise HTTPException(409, "Počkej na dokončení extrakce a zkus smazání znovu.")
    session.execute(delete(Extraction).where(Extraction.document_id == document_id))
    session.execute(delete(DocumentFile).where(DocumentFile.document_id == document_id))
    session.delete(doc)
    session.commit()


@app.get("/documents/{document_id}/text", dependencies=[Access])
def read_document_text(document_id: int, session: SessionDep) -> list[str]:
    return get_document(session, document_id).pages


@app.get("/documents/{document_id}/file", dependencies=[Access])
def read_document_file(document_id: int, session: SessionDep):
    doc = get_document(session, document_id)
    file = session.get(DocumentFile, document_id)
    if file is None:
        raise HTTPException(404, "File not found")
    return Response(
        file.data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


@app.post("/documents/{document_id}/extractions", status_code=202, dependencies=[Access])
def create_extraction(
    document_id: int,
    body: ExtractionRequest,
    session: SessionDep,
    background: BackgroundTasks,
    extractor: Annotated[Extractor, Depends(get_extractor)],
) -> Extraction:
    """Queue an extraction. A local model needs minutes per document, so the work runs in
    the background and clients poll GET /extractions/{id} until status != "running"."""
    doc = get_document(session, document_id)
    template = body.template or Template.model_validate(
        get_template_or_404(session, body.template_id).model_dump(exclude={"builtin"})
    )

    consume_llm_quota(session, "extraction")
    extraction = Extraction(
        document_id=doc.id,
        template_id=template.id,
        template=template.model_dump(),
        model=extractor.model,
        status="running",
        fields=[],
        issues=[],
        usage={},
    )
    session.add(extraction)
    session.commit()
    session.refresh(extraction)
    background.add_task(
        process_extraction, extraction.id, doc.pages, doc.ocr_pages or [], template, extractor
    )
    return extraction


def process_extraction(
    extraction_id: int,
    pages: list[str],
    ocr_pages: list[int],
    template: Template,
    extractor: Extractor,
) -> None:
    started = time.perf_counter()
    try:
        result = run_extraction(pages, template, extractor, ocr_pages)
    except Exception as exc:  # stored on the row so the UI can show it
        logger.exception("Extraction %s failed", extraction_id)
        result = {
            "status": "failed",
            "fields": [],
            "issues": [issue("error", "extraction_failed", f"Extrakce selhala: {exc}")],
            "usage": {},
        }
    result["usage"] = result["usage"] | {"latency_s": round(time.perf_counter() - started, 1)}
    with Session(engine) as session:
        extraction = session.get(Extraction, extraction_id)
        if extraction is None:
            return
        for key, value in result.items():
            setattr(extraction, key, value)
        session.add(extraction)
        session.commit()


@app.get("/documents/{document_id}/extractions", dependencies=[Access])
def list_extractions(document_id: int, session: SessionDep) -> list[Extraction]:
    get_document(session, document_id)
    query = (
        select(Extraction)
        .where(Extraction.document_id == document_id)
        .order_by(Extraction.id.desc())
    )
    return list(session.exec(query).all())


def get_extraction_or_404(session: Session, extraction_id: int) -> Extraction:
    extraction = session.get(Extraction, extraction_id)
    if extraction is None:
        raise HTTPException(404, "Extraction not found")
    return extraction


@app.get("/extractions/{extraction_id}", dependencies=[Access])
def read_extraction(extraction_id: int, session: SessionDep) -> Extraction:
    return get_extraction_or_404(session, extraction_id)


@app.patch("/extractions/{extraction_id}", dependencies=[Access])
def review_extraction(extraction_id: int, body: ReviewRequest, session: SessionDep) -> Extraction:
    """Human review: save corrected values and mark the extraction approved."""
    extraction = get_extraction_or_404(session, extraction_id)
    if extraction.status in ("failed", "running"):
        raise HTTPException(409, f"Extraction is {extraction.status} and cannot be reviewed")
    template = Template.model_validate(extraction.template)
    try:
        result = apply_review(template, extraction.fields, body.values)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    extraction.status = result["status"]
    extraction.fields = result["fields"]
    extraction.issues = result["issues"]
    extraction.reviewed_at = utcnow()
    session.add(extraction)
    session.commit()
    session.refresh(extraction)
    return extraction


EXPORT_COLUMNS = ["key", "label", "type", "raw", "value", "page", "confidence", "grounded", "quote"]


class BatchExportRequest(BaseModel):
    extraction_ids: list[int] = Field(min_length=1, max_length=100)
    # Optional ordered subset of template keys; labels come from the reviewed snapshot.
    columns: list[str] | None = Field(default=None, min_length=1)


@app.post("/exports/xlsx", dependencies=[Access])
def export_batch(body: BatchExportRequest, session: SessionDep):
    if len(set(body.extraction_ids)) != len(body.extraction_ids):
        raise HTTPException(422, "Vyber každou extrakci pouze jednou.")
    extractions = [get_extraction_or_404(session, id) for id in body.extraction_ids]
    if len({e.document_id for e in extractions}) != len(extractions):
        raise HTTPException(422, "Pro každý dokument vyber pouze jednu extrakci.")
    for extraction in extractions:
        if (
            extraction.status != "approved"
            or extraction.reviewed_at is None
            or any(i["severity"] == "error" for i in extraction.issues)
        ):
            raise HTTPException(
                409, f"Extrakce #{extraction.id} musí být lidsky schválená a bez chybových nálezů."
            )
    template = Template.model_validate(extractions[0].template)
    signature = [(f.key, f.label, f.type) for f in template.fields]
    if any(
        e.template_id != template.id
        or [(f["key"], f["label"], f.get("type", "string")) for f in e.template["fields"]]
        != signature
        for e in extractions
    ):
        raise HTTPException(409, "Vyber dokumenty se stejnou šablonou a stejnými sloupci.")
    fields = {f.key: f for f in template.fields}
    keys = body.columns if body.columns is not None else list(fields)
    if len(set(keys)) != len(keys) or any(key not in fields for key in keys):
        raise HTTPException(422, "Sloupce musí být unikátní klíče z použité šablony.")
    headers = ["Soubor", "Datum kontroly (UTC)", *[fields[key].label for key in keys]]
    rows = []
    for extraction in extractions:
        doc = get_document(session, extraction.document_id)
        values = {f["key"]: f["value"] for f in extraction.fields}
        row = [doc.filename, extraction.reviewed_at.date()]
        for key in keys:
            value = values.get(key)
            if value is not None and fields[key].type == "date":
                value = date.fromisoformat(value)
            row.append(value)
        rows.append(row)
    return Response(
        workbook(headers, rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="docflow-evidence.xlsx"'},
    )


@app.get("/extractions/{extraction_id}/export", dependencies=[Access])
def export_extraction(extraction_id: int, session: SessionDep, format: str = "json"):
    extraction = get_extraction_or_404(session, extraction_id)
    name = f"extraction-{extraction.id}-{extraction.template_id}"
    if format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(extraction.fields)
        return Response(
            "\ufeff" + buffer.getvalue(),  # BOM so Excel opens UTF-8 (Czech) correctly
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    if format != "json":
        raise HTTPException(422, "format must be json or csv")
    payload = {
        "document_id": extraction.document_id,
        "template_id": extraction.template_id,
        "status": extraction.status,
        "values": {f["key"]: f["value"] for f in extraction.fields},
        "issues": extraction.issues,
    }
    return JSONResponse(
        payload, headers={"Content-Disposition": f'attachment; filename="{name}.json"'}
    )


@app.get("/evals")
def list_evals() -> list[dict]:
    """Finished evaluation runs (scripts/evaluate.py), newest first, without per-doc detail."""
    runs = []
    for path in sorted(EVALS_DIR.glob("*.json"), reverse=True):
        run = json.loads(path.read_text())
        if "summary" in run:
            keep = ("model", "provider", "started", "finished", "summary")
            meta = {"id": path.stem, "prompt_version": run.get("prompt_version", 1)}
            runs.append(meta | {k: run.get(k) for k in keep})
    return runs
