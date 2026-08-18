"""Turn a clean synthetic ground-truth document into a realistic scanned one.

`ExtractorRunner` (and the whole eval harness) normally hands clean synthetic text
straight to the LLM extractor — see `runner.py`'s own docstring: "documents are
already text." That's a deliberate, correct shortcut for measuring extraction
quality on its own, but it also means the harness has never once exercised the OCR
code path (`documents/text_extraction.py`), at any corpus size — see
docs/EVALUATION.md's "OCR accuracy — Not measured" line.

This module closes that gap for the extraction runner specifically (generation is
untouched): render a document's ground-truth text to a PDF (reportlab), rasterise
and degrade it to look like a real scan (pdf2image + PIL), then run the degraded
*image* through the real `TextExtractor` — the same code a real scanned upload
hits, via the same `image/*` path `_from_image` always OCRs, never falling back to
a text layer the way the PDF path can. The returned `GroundTruth` keeps the
original `.fields` (the underlying document didn't change) but replaces `.text`
with what OCR actually produced, and tags `.difficulty` with `"ocr_scanned"` so the
existing by-difficulty reporting (`eval/metrics.py::EvaluationReport.by_difficulty`)
breaks it out automatically — no new metrics plumbing needed for that part.

Degradation is deliberately randomised-but-seeded (one `random.Random` per
document, derived from the document id) so a corpus rebuild is reproducible like
every other part of this harness, not a new source of run-to-run noise.
"""

from __future__ import annotations

import hashlib
import io
import random
from pathlib import Path

from docflow.config import ProcessingSettings
from docflow.documents.text_extraction import TextExtractor
from docflow.domain.errors import TextExtractionError
from docflow.eval.dataset import GroundTruth

# First working path wins. Courier-family for the render step specifically:
# preserving the generators' fixed-width column alignment (`f"{x:<32}{y:>10}"`)
# only looks like a real table if the glyphs are actually monospaced.
_MONOSPACE_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
)

# Real scans are rarely 300 DPI clean — this is deliberately below
# ProcessingSettings.ocr_dpi (300) to actually stress recognition rather than
# hand OCR a favourable image.
SCAN_DPI = 150


class ScanSimulationUnavailableError(Exception):
    """No monospace font could be found to render the test PDF."""


def _find_font() -> str:
    for path in _MONOSPACE_FONT_CANDIDATES:
        try:
            with Path(path).open("rb"):
                return path
        except OSError:
            continue
    raise ScanSimulationUnavailableError(
        "No monospace TTF font found among: " + ", ".join(_MONOSPACE_FONT_CANDIDATES)
    )


def render_to_pdf(text: str, *, font_path: str | None = None) -> bytes:
    """Render plain text to a single-page PDF, one line per source line.

    Not reflowed — the corpus generators pad columns with spaces on the assumption
    of a monospace font, and reflowing would destroy that alignment before OCR
    ever sees it.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font_path = font_path or _find_font()
    font_name = "DocflowEvalMono"
    pdfmetrics.registerFont(TTFont(font_name, font_path))

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    _width, height = A4
    font_size = 10
    line_height = font_size * 1.4
    margin = 40

    page.setFont(font_name, font_size)
    y = height - margin
    for line in text.split("\n"):
        if y < margin:
            page.showPage()
            page.setFont(font_name, font_size)
            y = height - margin
        page.drawString(margin, y, line)
        y -= line_height
    page.save()
    return buffer.getvalue()


def _degrade(image, *, rng: random.Random):
    from PIL import Image, ImageEnhance, ImageFilter

    image = image.convert("L")

    angle = rng.uniform(-2.0, 2.0)
    image = image.rotate(angle, expand=True, fillcolor=255, resample=Image.BICUBIC)

    noise = Image.effect_noise(image.size, 22)
    image = Image.blend(image, noise, alpha=0.06)

    image = image.filter(ImageFilter.GaussianBlur(radius=0.5))
    image = ImageEnhance.Contrast(image).enhance(0.80)
    return ImageEnhance.Brightness(image).enhance(1.03)


def _seed_for(document_id: str) -> int:
    """A stable seed derived from the document id.

    Deliberately not Python's built-in `hash()`: string hashing is randomised per
    *process* (`PYTHONHASHSEED`) unless explicitly disabled, so `hash(doc_id)`
    would give a different degradation on every single invocation of this CLI —
    exactly the "new source of run-to-run noise" this module's own docstring
    promises not to introduce.
    """
    digest = hashlib.sha256(document_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def rasterize_and_degrade(pdf_bytes: bytes, *, dpi: int = SCAN_DPI, seed: int) -> bytes:
    from pdf2image import convert_from_bytes

    images = convert_from_bytes(pdf_bytes, dpi=dpi)
    rng = random.Random(seed)  # noqa: S311 — reproducibility, not security
    degraded = _degrade(images[0], rng=rng)

    buffer = io.BytesIO()
    degraded.save(buffer, format="PNG")
    return buffer.getvalue()


def simulate_scan(item: GroundTruth, *, settings: ProcessingSettings | None = None) -> GroundTruth:
    """Real render -> real degrade -> real OCR. Returns a new `GroundTruth`.

    Raises `ScanSimulationUnavailableError` or `OCRUnavailableError` if the local
    toolchain (font, Tesseract) is missing — these are environment problems, not
    per-document ones, so callers should let them abort the whole corpus build
    rather than silently producing a corpus of empty documents.
    """
    settings = settings or ProcessingSettings()
    pdf_bytes = render_to_pdf(item.text)
    scanned_png = rasterize_and_degrade(pdf_bytes, seed=_seed_for(item.document_id))

    extracted = TextExtractor(settings).extract(scanned_png, "image/png")

    return GroundTruth(
        document_id=item.document_id,
        document_type=item.document_type,
        text=extracted.text,
        fields=item.fields,
        difficulty=[*item.difficulty, "ocr_scanned"],
        language=item.language,
        notes=(item.notes + " " if item.notes else "") + "OCR-simulated for eval.",
    )


def build_scanned_corpus(
    corpus: list[GroundTruth], *, settings: ProcessingSettings | None = None
) -> tuple[list[GroundTruth], list[tuple[str, str]]]:
    """Scan-simulate a whole corpus. Returns (scanned documents, skipped-with-reason).

    Per-document failures (a page too degraded to yield any text, an occasional
    rasterisation hiccup) are skipped rather than aborting the run — the same
    posture `ExtractorRunner._evaluate` takes toward extraction failures, and for
    the same reason: one bad document should not erase a 119-document result.
    Toolchain-missing errors (no font, no Tesseract) are not caught here — those
    mean every document would fail identically, which is a setup problem to
    surface immediately, not a per-document statistic.
    """
    settings = settings or ProcessingSettings()
    scanned: list[GroundTruth] = []
    skipped: list[tuple[str, str]] = []

    for item in corpus:
        try:
            scanned.append(simulate_scan(item, settings=settings))
        except TextExtractionError as exc:
            skipped.append((item.document_id, type(exc).__name__))

    return scanned, skipped
