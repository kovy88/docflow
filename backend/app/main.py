import csv
import io
import json
import logging
import time
from contextlib import asynccontextmanager
from functools import cache
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, model_validator
from sqlmodel import Session, select

from app.checks import issue
from app.config import settings
from app.db import CustomTemplate, Document, Extraction, engine, get_session, init_db, utcnow
from app.extractor import Extractor, OpenAIExtractor, ToolCallExtractor, suggest_template
from app.pdf import InvalidPdf, extract_pages
from app.pipeline import apply_review, run_extraction
from app.templates import Template, load_templates


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
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
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("docflow")
SessionDep = Annotated[Session, Depends(get_session)]
DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"


@cache
def load_labels() -> list[dict]:
    path = DATASET_DIR / "labels.json"
    return json.loads(path.read_text()) if path.exists() else []


def get_extractor() -> Extractor:
    if settings.llm_provider == "ollama":
        return ToolCallExtractor(
            settings.ollama_base_url, settings.ollama_model, settings.llm_timeout_s
        )
    if not settings.openai_api_key:
        raise HTTPException(503, "OPENAI_API_KEY is not configured")
    return OpenAIExtractor(settings.openai_api_key, settings.openai_model, settings.llm_timeout_s)


class DocumentOut(BaseModel):
    id: int
    filename: str
    page_count: int
    has_text: bool


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
        id=doc.id, filename=doc.filename, page_count=doc.page_count, has_text=any(doc.pages)
    )


def get_document(session: Session, document_id: int) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


@app.get("/health")
def health():
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "model": settings.ollama_model
        if settings.llm_provider == "ollama"
        else settings.openai_model,
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


@app.get("/templates")
def list_templates(session: SessionDep) -> list[TemplateOut]:
    return list(all_templates(session).values())


@app.get("/templates/{template_id}")
def get_template(template_id: str, session: SessionDep) -> TemplateOut:
    return get_template_or_404(session, template_id)


@app.post("/templates", status_code=201)
def create_template(template: Template, session: SessionDep) -> TemplateOut:
    if template.id in all_templates(session):
        raise HTTPException(409, f"Template {template.id!r} already exists")
    session.add(CustomTemplate(id=template.id, data=template.model_dump()))
    session.commit()
    return TemplateOut(**template.model_dump(), builtin=False)


@app.put("/templates/{template_id}")
def update_template(template_id: str, template: Template, session: SessionDep) -> TemplateOut:
    row = get_custom_or_409(session, template_id)
    if template.id != template_id:
        raise HTTPException(422, "Template id cannot be changed")
    row.data, row.updated_at = template.model_dump(), utcnow()
    session.add(row)
    session.commit()
    return TemplateOut(**template.model_dump(), builtin=False)


@app.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: str, session: SessionDep) -> None:
    session.delete(get_custom_or_409(session, template_id))
    session.commit()


@app.post("/documents/{document_id}/template-suggestion")
def suggest_document_template(
    document_id: int, session: SessionDep, llm: Annotated[Extractor, Depends(get_extractor)]
) -> Template:
    """Let the model propose a template for an unknown document type (not saved)."""
    doc = get_document(session, document_id)
    if not any(doc.pages):
        raise HTTPException(422, "PDF has no text layer")
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
        pages = extract_pages(data)
    except InvalidPdf as exc:
        raise HTTPException(422, f"Cannot read PDF: {exc}") from exc

    path = settings.storage_dir / f"{uuid4().hex}.pdf"
    path.write_bytes(data)
    doc = Document(filename=filename, path=str(path), page_count=len(pages), pages=pages)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


@app.post("/documents", status_code=201)
async def upload_document(file: UploadFile, session: SessionDep) -> DocumentOut:
    data = await file.read()
    return to_out(store_pdf(session, data, file.filename or "document.pdf"))


@app.get("/demo")
def list_demo_documents() -> list[dict]:
    """Fictional sample documents from the evaluation set, for trying the app quickly."""
    return [
        {"file": x["file"], "template_id": x["template_id"], "description": x["description"]}
        for x in load_labels()
    ]


@app.post("/demo/{file}", status_code=201)
def create_demo_document(file: str, session: SessionDep) -> DocumentOut:
    if file not in {x["file"] for x in load_labels()}:  # also prevents path traversal
        raise HTTPException(404, "Demo document not found")
    return to_out(store_pdf(session, (DATASET_DIR / file).read_bytes(), file))


@app.get("/documents")
def list_documents(session: SessionDep) -> list[DocumentOut]:
    docs = session.exec(select(Document).order_by(Document.id.desc())).all()
    return [to_out(d) for d in docs]


@app.get("/documents/{document_id}")
def read_document(document_id: int, session: SessionDep) -> DocumentOut:
    return to_out(get_document(session, document_id))


@app.get("/documents/{document_id}/text")
def read_document_text(document_id: int, session: SessionDep) -> list[str]:
    return get_document(session, document_id).pages


@app.get("/documents/{document_id}/file")
def read_document_file(document_id: int, session: SessionDep):
    doc = get_document(session, document_id)
    return FileResponse(doc.path, media_type="application/pdf", filename=doc.filename)


@app.post("/documents/{document_id}/extractions", status_code=202)
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
    background.add_task(process_extraction, extraction.id, doc.pages, template, extractor)
    return extraction


def process_extraction(
    extraction_id: int, pages: list[str], template: Template, extractor: Extractor
) -> None:
    started = time.perf_counter()
    try:
        result = run_extraction(pages, template, extractor)
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


@app.get("/documents/{document_id}/extractions")
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


@app.get("/extractions/{extraction_id}")
def read_extraction(extraction_id: int, session: SessionDep) -> Extraction:
    return get_extraction_or_404(session, extraction_id)


@app.patch("/extractions/{extraction_id}")
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


@app.get("/extractions/{extraction_id}/export")
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
            runs.append({"id": path.stem} | {k: run.get(k) for k in keep})
    return runs
