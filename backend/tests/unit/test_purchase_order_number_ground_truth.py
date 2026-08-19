"""Regression test for the Finding-4 class of bug (EVALUATION_ERROR_ANALYSIS.md).

`generate_invoice` sometimes writes a labelled purchase-order reference
("Č. objednávky: OBJ-XXXX" / "PO: OBJ-XXXX") into the document text as part of
its `extra_numbers` decoy block. Ground truth silently never recorded it, on any
of the 75 invoices in the checked-in corpus — decoy present or not — which
scored every correct extraction of a genuinely-present, labelled value as wrong.
Root-cause investigation: `docs/EVALUATION_ERROR_ANALYSIS.md` Finding 4.

This test does not read the checked-in corpus (a corpus-content check belongs in
EVALUATION_DATASET.md's own audit, and only covers whatever seed happens to be
checked in). It re-derives the invariant directly from `generate_invoice` across
many seeds and indices, so it fails immediately if a future change to the decoy
block's text is not mirrored in the ground truth it writes — the exact shape of
change that caused the original bug.
"""

from __future__ import annotations

import random
import re
import unicodedata

from docflow.eval.dataset import generate_invoice

# Matches the decoy line regardless of the `diacritics_stripped` hazard, which is
# applied to the whole document (including this line) independently of whether
# the decoy itself was written — the corpus already contains cases of both, and
# the diacritics-stripped form ("C. objednavky") must be recognised too, or this
# test would silently skip validating a real subset of documents.
_DECOY_RE = re.compile(r"(?:C\. objednavky|PO): (OBJ-\d{1,4})\b")


def _strip_diacritics(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return "".join(c for c in folded if not unicodedata.combining(c))


def test_purchase_order_number_ground_truth_matches_generated_text() -> None:
    """Every invoice, across a wide seed/index sweep: ground truth has the decoy
    value if and only if the text actually contains it, and — when present — the
    two are character-for-character identical. Never absent-but-present-in-text
    (the original bug); never present-but-absent-from-text (would mean ground
    truth is inventing a value the document doesn't support)."""
    checked = 0
    with_decoy = 0
    for seed in range(30):
        rng = random.Random(seed)
        for index in range(1, 21):
            gt = generate_invoice(rng, index)
            checked += 1

            match = _DECOY_RE.search(_strip_diacritics(gt.text))
            ground_truth_po = gt.fields.get("purchase_order_number")

            if match is not None:
                with_decoy += 1
                assert ground_truth_po == match.group(1), (
                    f"seed={seed} index={index}: text contains {match.group(1)!r} "
                    f"but ground truth has {ground_truth_po!r}"
                )
                assert "extra_numbers" in gt.difficulty, (
                    f"seed={seed} index={index}: decoy line present but "
                    "'extra_numbers' missing from difficulty tags"
                )
            else:
                assert ground_truth_po is None, (
                    f"seed={seed} index={index}: no PO reference in text but "
                    f"ground truth has {ground_truth_po!r} — a fabricated value "
                    "not supported by the source document"
                )

    # Sanity check on the sweep itself: both branches must actually be exercised,
    # or this test would trivially pass without ever checking the bug it guards.
    assert checked > 0
    assert 0 < with_decoy < checked, (
        f"expected a mix of decoy/no-decoy documents across {checked} generated "
        f"invoices, got {with_decoy} with a decoy — the ~50% gate in "
        "generate_invoice may have changed; widen the seed/index sweep above"
    )


def test_purchase_order_number_key_always_present_never_missing() -> None:
    """`purchase_order_number` must be an explicit key (value `None` when there is
    no reference), never an absent key — `build_field_outcomes` treats the two
    equivalently for scoring today, but an explicit `None` is self-documenting
    ("checked, genuinely absent") versus a key some future reader might assume was
    simply forgotten, which is exactly the state this bug was found in."""
    rng = random.Random(20240613)
    for index in range(1, 11):
        gt = generate_invoice(rng, index)
        assert "purchase_order_number" in gt.fields
