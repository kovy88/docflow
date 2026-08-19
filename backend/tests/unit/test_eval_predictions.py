"""`RunnerConfig(persist_predictions=True)` — per-document diagnostics.

Added while root-causing `purchase_order_number`'s 36/75 failures
(EVALUATION_ERROR_ANALYSIS.md, Finding 4): the aggregate FieldOutcome counts could
say a field was wrong on 36 documents but not *which* 36 or what the model actually
returned for them. These tests lock in the two properties that matter: the
diagnostics are there when asked for, and — just as important — completely absent
otherwise, so a routine run's report does not silently double in size.
"""

from __future__ import annotations

from docflow.eval.dataset import GroundTruth
from docflow.eval.runner import ExtractorRunner, RunnerConfig
from docflow.llm.fixture_provider import FixtureProvider

_CORPUS = [
    GroundTruth(
        document_id="test-0001",
        document_type="invoice",
        text="Faktura číslo: 2024-0001\nCelkem k úhradě: 1000,00 Kč",
        fields={"invoice_number": "2024-0001"},
    )
]


def _runner() -> ExtractorRunner:
    return ExtractorRunner(FixtureProvider(allow_heuristic=True))


async def test_predictions_omitted_by_default() -> None:
    report = await _runner().run(_CORPUS, config=RunnerConfig("test"))
    assert "predictions" not in report.to_dict()


async def test_persist_predictions_records_expected_raw_and_parsed() -> None:
    report = await _runner().run(_CORPUS, config=RunnerConfig("test", persist_predictions=True))
    payload = report.to_dict()

    assert "predictions" in payload
    assert len(payload["predictions"]) == 1

    entry = payload["predictions"][0]
    assert entry["document_id"] == "test-0001"
    assert entry["document_type"] == "invoice"
    assert entry["expected"] == {"invoice_number": "2024-0001"}
    # The fixture's heuristic provider re-derives this from the same text, so it
    # will not be empty — the point of this assertion is that the value made it
    # onto the report at all, not what it specifically is.
    assert entry["raw_model_output"]
    assert entry["parsed"]


async def test_persist_predictions_false_still_omits_key_with_multiple_documents() -> None:
    """Guards against a gate that only checks 'is the list empty' rather than the
    config flag itself — with persistence off, the key must not appear even when
    there is more than one document to have populated it from."""
    corpus = [
        GroundTruth(
            document_id=f"test-{i:04d}",
            document_type="invoice",
            text=f"Faktura číslo: 2024-{i:04d}",
            fields={"invoice_number": f"2024-{i:04d}"},
        )
        for i in range(3)
    ]
    report = await _runner().run(corpus, config=RunnerConfig("test"))
    assert "predictions" not in report.to_dict()
