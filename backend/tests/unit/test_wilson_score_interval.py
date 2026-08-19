"""Wilson score confidence intervals for the evaluation report's proportions.

Sanity-checked against textbook cases rather than an external stats library —
the formula is short enough to verify by hand, which is the point of choosing
it over a heavier dependency.
"""

from __future__ import annotations

import pytest

from docflow.eval.metrics import wilson_score_interval


def test_zero_documents_returns_none() -> None:
    assert wilson_score_interval(0, 0) is None


def test_interval_always_stays_within_zero_one() -> None:
    """The normal-approximation interval this replaces can go outside [0, 1]
    near 100% — the whole reason Wilson was chosen. Confirm it doesn't."""
    low, high = wilson_score_interval(120, 120, confidence=0.95)
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert high == 1.0  # 120/120 correctly cannot rule out "really is 100%"


def test_wider_interval_at_smaller_n() -> None:
    """The contract slice (n=4, see docs/EVALUATION_DATASET.md) should show a
    visibly wider interval than the invoice slice (n=75) at the same
    proportion — this is the whole point of reporting it."""
    small_n_low, small_n_high = wilson_score_interval(3, 4, confidence=0.95)
    large_n_low, large_n_high = wilson_score_interval(56, 75, confidence=0.95)  # same ~75% rate

    assert (small_n_high - small_n_low) > (large_n_high - large_n_low)


def test_higher_confidence_widens_the_interval() -> None:
    low_95, high_95 = wilson_score_interval(80, 100, confidence=0.95)
    low_99, high_99 = wilson_score_interval(80, 100, confidence=0.99)
    assert (high_99 - low_99) > (high_95 - low_95)


def test_matches_a_known_textbook_value() -> None:
    """56/100 at 95% confidence: a commonly-cited worked example for the
    Wilson interval (e.g. Wikipedia's own worked example uses p=0.56, n=100),
    landing at roughly [0.462, 0.653]."""
    low, high = wilson_score_interval(56, 100, confidence=0.95)
    assert low == pytest.approx(0.462, abs=0.01)
    assert high == pytest.approx(0.653, abs=0.01)


def test_unknown_confidence_level_raises_rather_than_silently_approximating() -> None:
    with pytest.raises(ValueError, match="No z-score tabulated"):
        wilson_score_interval(10, 20, confidence=0.80)
