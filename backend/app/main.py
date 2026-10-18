from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import APIError
from pydantic import BaseModel, model_validator
from sqlmodel import Session, select

from app.config import settings
from app.db import Document, Extraction, get_session, init_db, utcnow
from app.extractor import Extractor, OpenAIExtractor, ToolCallExtractor
from app.pdf import InvalidPdf, extract_pages
from app.pipeline import apply_review, run_extraction
from app.templates import Template, load_templates


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(title="Docflow", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SessionDep = Annotated[Session, Depends(get_session)]


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


@app.get("/templates")
def list_templates() -> list[Template]:
    return list(load_templates().values())


@app.get("/templates/{template_id}")
def get_template(template_id: str) -> Template:
    if template_id not in load_templates():
        raise HTTPException(404, "Template not found")
    return load_templates()[template_id]


@app.post("/documents", status_code=201)
async def upload_document(file: UploadFile, session: SessionDep) -> DocumentOut:
    data = await file.read()
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
    doc = Document(
        filename=file.filename or path.name, path=str(path), page_count=len(pages), pages=pages
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return to_out(doc)


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


@app.post("/documents/{document_id}/extractions", status_code=201)
def create_extraction(
    document_id: int,
    body: ExtractionRequest,
    session: SessionDep,
    extractor: Annotated[Extractor, Depends(get_extractor)],
) -> Extraction:
    doc = get_document(session, document_id)
    template = body.template or load_templates().get(body.template_id)
    if template is None:
        raise HTTPException(404, "Template not found")

    try:
        result = run_extraction(doc.pages, template, extractor)
    except (RuntimeError, APIError) as exc:
        raise HTTPException(502, f"LLM extraction failed: {exc}") from exc
    extraction = Extraction(
        document_id=doc.id,
        template_id=template.id,
        template=template.model_dump(),
        model=extractor.model,
        **result,
    )
    session.add(extraction)
    session.commit()
    session.refresh(extraction)
    return extraction


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
    if extraction.status == "failed":
        raise HTTPException(409, "Failed extraction cannot be reviewed")
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
