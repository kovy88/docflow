from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class InvalidPdf(ValueError):
    pass


def extract_pages(data: bytes) -> list[str]:
    """Return the text layer of each page. Scanned PDFs yield empty strings (no OCR yet)."""
    try:
        reader = PdfReader(BytesIO(data))
        return [(page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, ValueError) as exc:
        raise InvalidPdf(str(exc)) from exc
