"""Ingestion stages: fetch, verify, extract text."""

from __future__ import annotations

import hashlib

import structlog

from docflow.config import ProcessingSettings, UploadSettings
from docflow.documents.text_extraction import TextExtractor
from docflow.domain.enums import ProcessingStage
from docflow.domain.errors import CorruptDocumentError, StorageObjectNotFoundError
from docflow.pipeline.context import PipelineContext
from docflow.pipeline.stage import Stage
from docflow.storage.base import StorageBackend

logger = structlog.get_logger(__name__)


class FileValidationStage(Stage):
    """Fetch the file from storage and verify it is the one we recorded.

    The checksum comparison is not paranoia. Between upload and processing the
    bytes travel through object storage and a queue; a mismatch means either
    storage corruption or a key collision, and both are conditions where
    continuing would attribute one tenant's document to another's record. Failing
    loudly is the only safe response.
    """

    stage = ProcessingStage.FILE_VALIDATION

    def __init__(self, storage: StorageBackend, settings: UploadSettings) -> None:
        self._storage = storage
        self._settings = settings

    async def run(self, ctx: PipelineContext) -> None:
        if ctx.file_bytes is None:
            try:
                ctx.file_bytes = await self._storage.get(ctx.storage_key)
            except StorageObjectNotFoundError:
                raise CorruptDocumentError(
                    "The stored document could not be found. It may have been deleted."
                ) from None

        actual = hashlib.sha256(ctx.file_bytes).hexdigest()
        if ctx.checksum_sha256 and actual != ctx.checksum_sha256:
            raise CorruptDocumentError(
                "The stored document does not match its recorded checksum"
            )

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        return {"size_bytes": len(ctx.file_bytes or b""), "content_type": ctx.content_type}


class TextExtractionStage(Stage):
    """Native text extraction, with per-page OCR fallback handled internally."""

    stage = ProcessingStage.TEXT_EXTRACTION

    def __init__(self, settings: ProcessingSettings, *, max_pages: int = 50) -> None:
        self._extractor = TextExtractor(settings, max_pages=max_pages)

    async def run(self, ctx: PipelineContext) -> None:
        import asyncio

        assert ctx.file_bytes is not None

        # PDF parsing and OCR are CPU-bound and synchronous. Running them inline
        # would block the worker's event loop for seconds, stalling every other
        # job in the same process.
        ctx.extracted = await asyncio.to_thread(
            self._extractor.extract, ctx.file_bytes, ctx.content_type
        )

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        if ctx.extracted is None:
            return {}
        return {
            "pages": ctx.extracted.page_count,
            "chars": ctx.extracted.char_count,
            "used_ocr": ctx.extracted.used_ocr,
            "ocr_pages": ctx.extracted.ocr_page_count,
            "language": ctx.extracted.language,
        }


class OCRStage(Stage):
    """Reports OCR usage as a distinct timeline entry.

    OCR itself happens inside `TextExtractionStage`, because the routing decision is
    per page and needs the extractor's page objects. Splitting the *work* across two
    stages would mean passing half-extracted state between them. But users need to
    see "this document was scanned" in the timeline, and support needs OCR usage as
    its own metric — so it gets its own step record, marked skipped when no page
    needed it.
    """

    stage = ProcessingStage.OCR
    optional = True

    def should_run(self, ctx: PipelineContext) -> bool:
        return bool(ctx.extracted and ctx.extracted.used_ocr)

    async def run(self, ctx: PipelineContext) -> None:
        if ctx.extracted and ctx.extracted.ocr_page_count:
            ctx.add_review_reason(
                f"{ctx.extracted.ocr_page_count} page(s) required OCR; text quality may be reduced"
            )

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        if ctx.extracted is None:
            return {}
        return {"ocr_pages": ctx.extracted.ocr_page_count, "total_pages": ctx.extracted.page_count}


class TextPersistenceStage(Stage):
    """Store the extracted text alongside the original.

    Keeping the text means re-processing with a new prompt or model does not need
    to re-parse or re-OCR the PDF — which is most of the wall-clock time and, for
    OCR-heavy documents, most of the compute. It is also what lets the review UI
    show evidence snippets without re-opening the source file.
    """

    stage = ProcessingStage.METADATA_EXTRACTION
    optional = True

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def should_run(self, ctx: PipelineContext) -> bool:
        return ctx.extracted is not None and bool(ctx.extracted.text)

    async def run(self, ctx: PipelineContext) -> None:
        from docflow.storage.base import build_key

        assert ctx.extracted is not None
        key = build_key(
            ctx.organization_id, ctx.document_id, kind="text", extension="txt"
        )
        await self._storage.put(
            key, ctx.extracted.text.encode("utf-8"), content_type="text/plain; charset=utf-8"
        )
        ctx.text_storage_key = key

    def detail(self, ctx: PipelineContext) -> dict[str, object]:
        return {"text_stored": ctx.text_storage_key is not None}
