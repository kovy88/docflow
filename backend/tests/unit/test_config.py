"""Configuration defaults that matter for correctness, not just convenience.

Settings with a wrong-but-plausible default don't fail loudly — they just quietly
produce worse output (see `ocr_language` below, found while investigating why the
Docker image installs a Czech Tesseract pack that nothing was ever requesting).
"""

from __future__ import annotations

from docflow.config import ProcessingSettings


def test_ocr_language_includes_czech() -> None:
    """English-only OCR on a CZ/CEE-market document silently mangles diacritics.

    The Docker image installs `tesseract-ocr-ces` specifically for this (see
    Dockerfile) — the default here has to actually request it, not just have it
    available on disk.
    """
    settings = ProcessingSettings()
    languages = settings.ocr_language.split("+")
    assert "ces" in languages
    assert "eng" in languages
