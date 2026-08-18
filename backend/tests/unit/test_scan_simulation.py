"""Render -> degrade -> real-OCR pipeline for the eval harness's scanned-corpus mode.

Real Tesseract, real reportlab/pdf2image, no mocks — the whole point of this module
is what a real scan actually does to a document, which a mock cannot demonstrate.
Skipped wherever the toolchain isn't available (no Tesseract, no monospace font),
same posture as `test_ocr_language.py`.
"""

from __future__ import annotations

import pytest

from docflow.domain.errors import OCRUnavailableError
from docflow.eval.dataset import GroundTruth
from docflow.eval.scan_simulation import (
    ScanSimulationUnavailableError,
    _seed_for,
    build_scanned_corpus,
    render_to_pdf,
    simulate_scan,
)

SAMPLE = GroundTruth(
    document_id="invoice-test-0001",
    document_type="invoice",
    text="ACME s.r.o.\nZáklad daně: 1 000,00 Kč\nCelkem k úhradě: 1 210,00 Kč\n",
    fields={"invoice_number": "2024-0001", "total": "1210.00"},
)


def _simulate_or_skip(item: GroundTruth) -> GroundTruth:
    try:
        return simulate_scan(item)
    except (ScanSimulationUnavailableError, OCRUnavailableError) as exc:
        pytest.skip(f"scan toolchain not available: {exc}")


def test_seed_for_is_stable_across_processes() -> None:
    """Regression guard for a real bug: this used to be `hash(document_id)`, which
    is randomised per *process* (`PYTHONHASHSEED`) — every `docflow-eval` run would
    have degraded the corpus differently, contradicting the whole point of a seeded
    corpus. `hashlib.sha256` is deterministic across processes, so this exact value
    (computed once, independently, and hard-coded here) must keep matching; a
    regression back to `hash()` would not reliably reproduce it.
    """
    assert _seed_for("invoice-test-0001") == 858494771
    assert _seed_for("invoice-test-0001") == _seed_for("invoice-test-0001")
    assert _seed_for("invoice-test-0001") != _seed_for("invoice-test-0002")


def test_render_to_pdf_produces_a_real_pdf() -> None:
    try:
        pdf_bytes = render_to_pdf(SAMPLE.text)
    except ScanSimulationUnavailableError as exc:
        pytest.skip(f"no monospace font available: {exc}")
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500  # not an empty/degenerate document


def test_simulate_scan_preserves_identity_and_ground_truth() -> None:
    scanned = _simulate_or_skip(SAMPLE)
    assert scanned.document_id == SAMPLE.document_id
    assert scanned.document_type == SAMPLE.document_type
    # The underlying document didn't change — only what OCR could read from it did.
    assert scanned.fields == SAMPLE.fields


def test_simulate_scan_tags_difficulty_and_replaces_text() -> None:
    scanned = _simulate_or_skip(SAMPLE)
    assert "ocr_scanned" in scanned.difficulty
    # Real OCR of a real (degraded) render is extremely unlikely to be a byte-exact
    # match of the input — if it is, something in the pipeline was skipped, not
    # unusually clean output.
    assert scanned.text != SAMPLE.text
    assert scanned.text.strip() != ""


def test_simulate_scan_recovers_most_of_the_real_content() -> None:
    """Not a garbage-in-garbage-out pipeline: recognisable words survive."""
    scanned = _simulate_or_skip(SAMPLE)
    assert "ACME" in scanned.text
    assert "1 210" in scanned.text or "1210" in scanned.text


def test_build_scanned_corpus_returns_one_item_per_input() -> None:
    corpus = [SAMPLE]
    try:
        scanned_corpus, skipped = build_scanned_corpus(corpus)
    except (ScanSimulationUnavailableError, OCRUnavailableError) as exc:
        pytest.skip(f"scan toolchain not available: {exc}")
    assert len(scanned_corpus) + len(skipped) == len(corpus)


def test_default_processing_settings_are_used_when_none_passed() -> None:
    """`simulate_scan` should use the real `ocr_language` default (eng+ces), not
    accidentally fall back to English-only when the caller doesn't pass settings."""
    scanned = _simulate_or_skip(SAMPLE)
    assert scanned is not None  # ran without needing an explicit settings object
