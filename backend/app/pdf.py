import shutil
from dataclasses import dataclass, field
from io import BytesIO

import pypdfium2 as pdfium
import pytesseract
from PIL import ImageFilter
from pypdf import PdfReader
from pypdf.errors import PdfReadError

OCR_LANG = "ces"
OCR_DPI = 300
# Scanners often stamp a page number or a date on top of the image; a page with less
# than this many visible characters in its text layer is treated as a scan.
MIN_TEXT_CHARS = 20
# Some scanners add a text layer without diacritics ("Najemnl smlouva c."). Czech text
# has 9–19 % letters with diacritics (measured on real contracts and the dataset); such a
# broken layer has none, so the page is read by OCR instead.
CZECH_LETTERS = set("áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ")
MIN_DIACRITICS_SHARE = 0.02
MIN_LETTERS_FOR_CHECK = 200


def needs_ocr(text: str) -> bool:
    if len("".join(text.split())) < MIN_TEXT_CHARS:
        return True
    letters = sum(c.isalpha() for c in text)
    if letters < MIN_LETTERS_FOR_CHECK:
        return False
    return sum(c in CZECH_LETTERS for c in text) / letters < MIN_DIACRITICS_SHARE


class InvalidPdf(ValueError):
    pass


@dataclass
class PdfText:
    pages: list[str]
    ocr_pages: list[int] = field(default_factory=list)  # 1-based pages read by OCR


def ocr_available() -> bool:
    return shutil.which(pytesseract.pytesseract.tesseract_cmd) is not None


def ocr_page(pdf: pdfium.PdfDocument, index: int) -> str:
    image = pdf[index].render(scale=OCR_DPI / 72, grayscale=True).to_pil().convert("L")
    # A 3×3 median removes scanner speckles that Tesseract otherwise reads as punctuation
    # inside numbers (measured on the scan set: 19 → 36 of 39 values read exactly).
    image = image.filter(ImageFilter.MedianFilter(3))
    return pytesseract.image_to_string(image, lang=OCR_LANG).strip()


def read_pdf(data: bytes, ocr: bool = True) -> PdfText:
    """Text of each page: the text layer where there is a usable one, local OCR
    (Tesseract) for scanned pages and broken text layers."""
    try:
        reader = PdfReader(BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, ValueError) as exc:
        raise InvalidPdf(str(exc)) from exc

    scanned = [i for i, text in enumerate(pages) if needs_ocr(text)]
    if not scanned or not ocr or not ocr_available():
        return PdfText(pages)
    pdf = pdfium.PdfDocument(data)
    try:
        ocr_pages = []
        for i in scanned:
            text = ocr_page(pdf, i)
            if text:
                pages[i] = text
                ocr_pages.append(i + 1)
    finally:
        pdf.close()
    return PdfText(pages, ocr_pages)


def count_pages(data: bytes) -> int:
    try:
        return len(PdfReader(BytesIO(data)).pages)
    except (PdfReadError, ValueError) as exc:
        raise InvalidPdf(str(exc)) from exc


def extract_pages(data: bytes) -> list[str]:
    """Text layer of each page, without OCR (scanned pages yield empty strings)."""
    return read_pdf(data, ocr=False).pages
