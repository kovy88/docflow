"""The evaluation set is only useful if its labels are right: every labelled value must be
printed in the PDF (as the text layer sees it), normalise to the expected value, and the
expected issues must match what the rules say."""

import json
from pathlib import Path

import pytest

from app.checks import apply_rules, normalize, quote_in
from app.pdf import extract_pages
from app.templates import load_templates

DATASET = Path(__file__).resolve().parent.parent / "dataset"
LABELS = json.loads((DATASET / "labels.json").read_text())


def test_dataset_covers_all_templates_and_planted_errors():
    assert {x["template_id"] for x in LABELS} == set(load_templates())
    planted = {code for x in LABELS for code in x["expected_issues"]}
    assert {"sum_mismatch", "ico_checksum", "date_order"} <= planted
    assert sum(1 for x in LABELS if not x["expected_issues"]) >= len(LABELS) // 2


@pytest.mark.parametrize("label", LABELS, ids=[x["file"] for x in LABELS])
def test_label_matches_pdf(label):
    template = load_templates()[label["template_id"]]
    text = "\n".join(extract_pages((DATASET / label["file"]).read_bytes()))
    assert set(label["expected"]) == {f.key for f in template.fields}

    for spec in template.fields:
        raw, expected = label["raw"].get(spec.key), label["expected"][spec.key]
        if raw is None:
            assert expected is None
            continue
        assert quote_in(raw, text, threshold=1.0), f"{spec.key}: {raw!r} not in PDF text"
        assert normalize(raw, spec.type) == expected

    values = {k: v for k, v in label["expected"].items() if v is not None}
    codes = [i["code"] for i in apply_rules(template, values)]
    missing = [
        "missing" for f in template.fields if f.required and label["expected"][f.key] is None
    ]
    assert sorted(codes + missing) == label["expected_issues"]
