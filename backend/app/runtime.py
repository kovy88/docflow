"""Pipeline pieces shared by the workspace API (main.py), the trial page and the
integrator API: model client, daily model quota, PDF intake and background extraction."""

import logging
import time
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlmodel import Session, func, select

from app.checks import issue
from app.config import settings
from app.db import (
    ExternalExtraction,
    Extraction,
    UsageEvent,
    Workspace,
    engine,
    get_session,
    utcnow,
)
from app.extractor import Extractor, OpenAIExtractor, OpenRouterExtractor, ToolCallExtractor
from app.pdf import InvalidPdf, PdfText, count_pages, read_pdf
from app.pipeline import run_extraction
from app.templates import Template

logger = logging.getLogger("docflow")
SessionDep = Annotated[Session, Depends(get_session)]


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


def llm_calls_today(session: Session, workspace_id: int | None = None) -> int:
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    query = select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at >= midnight)
    if workspace_id is not None:
        query = query.where(UsageEvent.workspace_id == workspace_id)
    return session.exec(query).one()


def llm_calls_left(session: Session, workspace: Workspace | None = None) -> int:
    """Model calls left today: the global cap, and a workspace's own quota if it has one."""
    left = settings.daily_llm_limit - llm_calls_today(session)
    if workspace is not None and workspace.daily_quota is not None:
        left = min(left, workspace.daily_quota - llm_calls_today(session, workspace.id))
    return max(left, 0)


def consume_llm_quota(session: Session, kind: str, workspace: Workspace | None = None) -> None:
    """Global daily cap on paid model calls, so a public URL cannot drain the API budget."""
    if llm_calls_left(session, workspace) <= 0:
        raise HTTPException(429, "Denní limit volání modelu je vyčerpaný, zkus to zítra.")
    session.add(UsageEvent(kind=kind, workspace_id=workspace and workspace.id))
    session.commit()


def parse_pdf(data: bytes, max_mb: int, max_pages: int | None = None) -> PdfText:
    """Validate an upload and read its text. Size and page count are checked before OCR,
    which takes seconds per page."""
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(413, f"Soubor je větší než {max_mb} MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "Podporované jsou jen soubory PDF.")
    try:
        if max_pages is not None and count_pages(data) > max_pages:
            raise HTTPException(413, f"PDF má víc než {max_pages} stran.")
        return read_pdf(data)
    except InvalidPdf as exc:
        raise HTTPException(
            422, "PDF se nepodařilo přečíst. Zkontrolujte, zda není poškozené nebo zaheslované."
        ) from exc


def process_extraction(
    extraction_id: int | str,
    pages: list[str],
    ocr_pages: list[int],
    template: Template,
    extractor: Extractor,
    table: type[Extraction] | type[ExternalExtraction] = Extraction,
) -> None:
    started = time.perf_counter()
    started_at = utcnow()
    with Session(engine) as session:
        extraction = session.get(table, extraction_id)
        if extraction is None:
            return
        # Persist the phase before calling the model so polling clients can distinguish
        # a batch item waiting in BackgroundTasks from one actually being processed.
        created_at = (
            extraction.created_at.replace(tzinfo=started_at.tzinfo)
            if extraction.created_at.tzinfo is None
            else extraction.created_at
        )
        progress = {
            "phase": "processing",
            "started_at": started_at.isoformat(),
            "queue_wait_s": round(max(0, (started_at - created_at).total_seconds()), 1),
        }
        extraction.usage = extraction.usage | progress
        session.add(extraction)
        session.commit()
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
    result["usage"] = (
        result["usage"]
        | progress
        | {
            "phase": "failed" if result["status"] == "failed" else "completed",
            "latency_s": round(time.perf_counter() - started, 1),
        }
    )
    with Session(engine) as session:
        extraction = session.get(table, extraction_id)
        if extraction is None:  # deleted meanwhile (trial expiry, DELETE)
            return
        for key, value in result.items():
            setattr(extraction, key, value)
        session.add(extraction)
        session.commit()
