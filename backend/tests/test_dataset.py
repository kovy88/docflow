"""The evaluation set is only useful if its labels are right: every labelled value must be
printed in the PDF (as the text layer sees it), normalise to the expected value, and the
expected issues must match what the rules say. Scans have no text layer; for them the
check is that OCR finds the document's text."""

import json
from pathlib import Path

import pytest

from app.checks import apply_rules, normalize, quote_in
from app.pdf import extract_pages, ocr_available, read_pdf
from app.templates import load_templates

DATASET = Path(__file__).resolve().parent.parent / "dataset"
LABELS = json.loads((DATASET / "labels.json").read_text())
SCANS = [x for x in LABELS if x["difficulty"] == "scan"]


def test_dataset_covers_evaluated_templates_and_planted_errors():
    # Lease is a new pilot template; it has no measured model accuracy yet.
    evaluated = set(load_templates()) - {"lease"}
    assert {x["template_id"] for x in LABELS} == evaluated
    assert {x["template_id"] for x in SCANS} == evaluated
    planted = {code for x in LABELS for code in x["expected_issues"]}
    assert {"sum_mismatch", "ico_checksum", "date_order"} <= planted
    assert sum(1 for x in LABELS if not x["expected_issues"]) >= len(LABELS) // 2


@pytest.mark.parametrize("label", LABELS, ids=[x["file"] for x in LABELS])
def test_label_matches_pdf(label):
    template = load_templates()[label["template_id"]]
    pages = extract_pages((DATASET / label["file"]).read_bytes())
    text = "\n".join(pages)
    assert set(label["expected"]) == {f.key for f in template.fields}

    for spec in template.fields:
        raw, expected = label["raw"].get(spec.key), label["expected"][spec.key]
        if raw is None:
            assert expected is None
            continue
        if label["difficulty"] != "scan":
            assert quote_in(raw, text, threshold=1.0), f"{spec.key}: {raw!r} not in PDF text"
        assert normalize(raw, spec.type) == expected

    if label["difficulty"] == "scan":
        assert not any(pages), "a scan must not have a text layer"

    values = {k: v for k, v in label["expected"].items() if v is not None}
    codes = [i["code"] for i in apply_rules(template, values)]
    missing = [
        "missing" for f in template.fields if f.required and label["expected"][f.key] is None
    ]
    assert sorted(codes + missing) == label["expected_issues"]


@pytest.mark.skipif(not ocr_available(), reason="tesseract is not installed")
@pytest.mark.parametrize("label", SCANS, ids=[x["file"] for x in SCANS])
def test_ocr_reads_scans(label):
    pdf = read_pdf((DATASET / label["file"]).read_bytes())
    assert pdf.ocr_pages == list(range(1, len(pdf.pages) + 1))
    text = "\n".join(pdf.pages)
    values = [v for v in label["raw"].values() if v is not None]
    found = [v for v in values if quote_in(v, text, threshold=0.85, ocr=True)]
    assert len(found) >= 0.8 * len(values), set(values) - set(found)
