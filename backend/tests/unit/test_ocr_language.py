"""OCR actually reads Czech diacritics with the configured language pack.

Real OCR, not a mock — this is the one place mocking the boundary would prove
nothing, since the entire bug being guarded against (`ocr_language` defaulting to
`"eng"` while the Docker image installs `tesseract-ocr-ces` for exactly this
market — see config.py) is a property of the real Tesseract binary's output, not
of any code this project owns. Skipped wherever Tesseract or a Unicode-capable
font isn't available (a plain `uv sync` without the `ocr` extra, or a CI image
with no fonts) — same posture as conftest.py's Postgres-unavailable skip: validate
real behaviour wherever it *can* run rather than faking the boundary.

Measured on 2026-08-18 against real external ground truth (not just this test's
tiny sample): rendering ICDAR2019 post-OCR-correction Czech text
(https://zenodo.org/records/3515403, CZ1 subset, National Library of the Czech
Republic) and a modern Czech invoice paragraph, `eng`-only scored 7.9-10.3%
character error rate; `eng+ces` scored 0.1-0.7% on the same images. That
ad-hoc script isn't part of this repo (it downloads a 54MB third-party archive),
but the numbers are why this test's threshold is set where it is.
"""

from __future__ import annotations

import io

import pytest

from docflow.config import ProcessingSettings
from docflow.documents.text_extraction import TextExtractor
from docflow.domain.errors import OCRUnavailableError

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)

# Every Czech-specific diacritic in one short, realistic invoice-style line.
CZECH_TEXT = (
    "Základ daně: 33 000,00 Kč. Dodavatel: Nováková. Splatnost: 28. března. Přeji hezký den."
)


def _render_to_png(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for path in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, 32)
            break
        except OSError:
            continue
    if font is None:
        pytest.skip("no Unicode-capable TTF font available to render the test image")

    width = int(font.getlength(text)) + 40
    image = Image.new("L", (width, 70), color=255)
    ImageDraw.Draw(image).text((20, 15), text, font=font, fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _character_error_rate(hypothesis: str, reference: str) -> float:
    """Levenshtein distance over character count. No external dependency for one use."""
    a, b = hypothesis.strip(), reference.strip()
    if not b:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1] / len(b)


@pytest.fixture
def czech_test_image() -> bytes:
    return _render_to_png(CZECH_TEXT)


def _extract_or_skip(settings: ProcessingSettings, image: bytes) -> str:
    try:
        return TextExtractor(settings).extract(image, "image/png").text
    except OCRUnavailableError as exc:
        pytest.skip(f"OCR not available in this environment: {exc}")


def test_default_ocr_language_reads_czech_diacritics(czech_test_image: bytes) -> None:
    # Measured 0.0% CER on this image (2026-08-18, local Tesseract 5.5.3 + tesseract-lang
    # 4.1.0) — 0.03 leaves headroom for minor cross-environment font-rendering variance.
    recognised = _extract_or_skip(ProcessingSettings(), czech_test_image)
    assert _character_error_rate(recognised, CZECH_TEXT) < 0.03


def test_english_only_is_measurably_worse_on_the_same_image(czech_test_image: bytes) -> None:
    # Measured 9.2% CER on the same image and settings — this isn't a marginal
    # difference, so a wide margin (> 0.03) still separates it cleanly from the
    # eng+ces case above without the test being sensitive to exact rendering.
    recognised = _extract_or_skip(ProcessingSettings(ocr_language="eng"), czech_test_image)
    assert _character_error_rate(recognised, CZECH_TEXT) > 0.03
