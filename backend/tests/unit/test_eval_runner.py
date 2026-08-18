"""ExtractorRunner's OCR-awareness in confidence scoring.

`_score` mirrors the real pipeline's `context_signal = 0.65 if ctx.used_ocr else
0.95` (`pipeline/stages/extract.py`) — before this, the eval runner always scored
as if every document were clean native text, because it never had a way to know
otherwise (see `eval/scan_simulation.py`, which is what actually sets `used_ocr`
via the `"ocr_scanned"` difficulty tag now).
"""

from __future__ import annotations

from docflow.eval.runner import ExtractorRunner
from docflow.llm.fixture_provider import FixtureProvider
from docflow.schemas.registry import get_registry


def _runner() -> ExtractorRunner:
    return ExtractorRunner(FixtureProvider(allow_heuristic=True))


def test_ocr_scanned_documents_score_lower_confidence_than_clean_ones() -> None:
    spec = get_registry().resolve("invoice")
    data = {"invoice_number": "2024-0001"}
    text = "Faktura číslo: 2024-0001"

    _confidences_clean, overall_clean = _runner()._score(data, spec, text, [], used_ocr=False)
    _confidences_ocr, overall_ocr = _runner()._score(data, spec, text, [], used_ocr=True)

    assert overall_clean is not None
    assert overall_ocr is not None
    assert overall_ocr < overall_clean


def test_default_is_clean_text_not_ocr() -> None:
    """A caller that forgets the flag should get the pre-existing behaviour
    (native-text confidence), not silently discounted scores — `used_ocr` must
    default to False, matching the runner's normal text-bypass mode."""
    spec = get_registry().resolve("invoice")
    data = {"invoice_number": "2024-0001"}
    text = "Faktura číslo: 2024-0001"

    _confidences_default, overall_default = _runner()._score(data, spec, text, [])
    _confidences_clean, overall_clean = _runner()._score(data, spec, text, [], used_ocr=False)

    assert overall_default == overall_clean
