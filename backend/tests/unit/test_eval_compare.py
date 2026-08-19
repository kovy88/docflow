"""Regression-comparison tool: matches runs by (provider, model), flags gate violations.

Uses hand-built report dicts, not a real eval run — this tests the comparison
logic itself, which is pure data transformation with no LLM/network dependency.
"""

from __future__ import annotations

import json

from docflow.scripts.eval_compare import GATES, compare


def _report(
    provider: str,
    model: str,
    *,
    required: float,
    critical: float,
    doc_success: float,
    review_rate: float,
    cost: float = 0.001,
) -> dict:
    return {
        "provider": provider,
        "model": model,
        "accuracy": {
            "field_normalised": 0.8,
            "required_normalised": required,
            "critical_normalised": critical,
            "document_success_rate": doc_success,
        },
        "operational": {"review_rate": review_rate},
        "cost": {"per_document_usd": cost},
    }


def _write(tmp_path, name: str, runs: list[dict]):
    path = tmp_path / name
    path.write_text(json.dumps({"runs": runs}), encoding="utf-8")
    return str(path)


def test_identical_reports_produce_no_violations(tmp_path) -> None:
    run = _report(
        "openai", "gpt-4.1-mini", required=1.0, critical=0.8, doc_success=1.0, review_rate=0.175
    )
    before = _write(tmp_path, "before.json", [run])
    after = _write(tmp_path, "after.json", [run])

    rows, violations = compare(before, after)

    assert len(rows) == 1
    assert violations == []


def test_required_field_accuracy_drop_beyond_gate_is_flagged(tmp_path) -> None:
    before_run = _report(
        "openai", "gpt-4.1-mini", required=1.0, critical=0.8, doc_success=1.0, review_rate=0.175
    )
    drop = GATES["required_field_accuracy_max_drop_pct"] / 100 + 0.05
    after_run = _report(
        "openai",
        "gpt-4.1-mini",
        required=1.0 - drop,
        critical=0.8,
        doc_success=1.0,
        review_rate=0.175,
    )
    before = _write(tmp_path, "before.json", [before_run])
    after = _write(tmp_path, "after.json", [after_run])

    _rows, violations = compare(before, after)

    assert any("required-field accuracy dropped" in v for v in violations)


def test_document_success_rate_any_decrease_is_flagged(tmp_path) -> None:
    before_run = _report(
        "openai", "gpt-4.1-mini", required=1.0, critical=0.8, doc_success=1.0, review_rate=0.175
    )
    after_run = _report(
        "openai", "gpt-4.1-mini", required=1.0, critical=0.8, doc_success=0.99, review_rate=0.175
    )
    before = _write(tmp_path, "before.json", [before_run])
    after = _write(tmp_path, "after.json", [after_run])

    _rows, violations = compare(before, after)

    assert any("document success rate decreased" in v for v in violations)


def test_review_rate_increase_within_gate_is_not_flagged(tmp_path) -> None:
    """A small, expected review-rate increase (e.g. tightening a threshold on
    purpose) shouldn't trip the gate — only a jump beyond it should."""
    before_run = _report(
        "openai", "gpt-4.1-mini", required=1.0, critical=0.8, doc_success=1.0, review_rate=0.175
    )
    after_run = _report(
        "openai", "gpt-4.1-mini", required=1.0, critical=0.8, doc_success=1.0, review_rate=0.20
    )
    before = _write(tmp_path, "before.json", [before_run])
    after = _write(tmp_path, "after.json", [after_run])

    _rows, violations = compare(before, after)

    assert violations == []


def test_large_review_rate_increase_is_flagged_but_explained(tmp_path) -> None:
    """A large jump (like clean-vs-OCR: 17.5% -> 81.7%) is still flagged — the
    gate can't distinguish "regression" from "intentional OCR comparison," so
    it flags either way and the message says so explicitly."""
    before_run = _report(
        "openai", "gpt-5.6-luna", required=1.0, critical=0.8, doc_success=1.0, review_rate=0.175
    )
    after_run = _report(
        "openai", "gpt-5.6-luna", required=0.99, critical=0.8, doc_success=0.93, review_rate=0.817
    )
    before = _write(tmp_path, "before.json", [before_run])
    after = _write(tmp_path, "after.json", [after_run])

    _rows, violations = compare(before, after)

    assert any("review rate increased" in v and "OCR" in v for v in violations)


def test_run_missing_from_after_is_reported_not_silently_dropped(tmp_path) -> None:
    run = _report(
        "google", "gemini-3.6-flash", required=1.0, critical=0.8, doc_success=1.0, review_rate=0.455
    )
    before = _write(tmp_path, "before.json", [run])
    after = _write(tmp_path, "after.json", [])

    rows, _violations = compare(before, after)

    assert rows == [
        {"provider": "google", "model": "gemini-3.6-flash", "status": "missing in after"}
    ]
