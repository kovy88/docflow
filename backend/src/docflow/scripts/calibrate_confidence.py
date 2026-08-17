"""Re-derive confidence-band thresholds from measured accuracy, not intuition.

`domain/confidence.py` declares `HIGH_THRESHOLD = 0.85` and
`MEDIUM_THRESHOLD = 0.60` as "empirical starting points" — this script is
what makes that claim checkable instead of asserted. It runs the extraction
pipeline over the evaluation corpus, buckets every scored field by its raw
confidence score into deciles (not just the three production bands), and
reports the *actual* accuracy in each decile. A well-calibrated score has
accuracy that rises monotonically with the bucket; a threshold sitting where
accuracy actually jumps is defensible, one placed by feel is not.

Runs against the fixture provider by default — deterministic and free, and
the only provider with enough corpus volume (all 120 documents, not a
quota-limited slice) to make decile-level buckets meaningful. Real-provider
calibration is directly limited by whatever quota that provider allows; see
docs/EVALUATION.md for what's actually been measured with a real model.

Usage:
    uv run docflow-calibrate
    uv run docflow-calibrate --provider google --size 20
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from itertools import pairwise

from docflow.config import get_settings
from docflow.domain.confidence import HIGH_THRESHOLD, MEDIUM_THRESHOLD
from docflow.eval.cli import _build_provider
from docflow.eval.dataset import DEFAULT_CORPUS_PATH, read_corpus
from docflow.eval.runner import ExtractorRunner, RunnerConfig

DECILES = 10


def _bucket(score: float) -> int:
    # score == 1.0 must land in the last bucket (index 9), not overflow into a
    # nonexistent 10th.
    return min(DECILES - 1, int(score * DECILES))


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    corpus = read_corpus(DEFAULT_CORPUS_PATH)
    if args.size:
        corpus = corpus[: args.size]

    provider = _build_provider(args.provider or settings.llm.provider, settings)
    print(f"Running {provider.name}/{settings.llm.model} over {len(corpus)} documents...")

    runner = ExtractorRunner(provider, settings=settings)
    report = await runner.run(
        corpus, config=RunnerConfig(label="calibration", concurrency=args.concurrency)
    )
    await provider.aclose()

    buckets: dict[int, list[bool]] = defaultdict(list)
    for document in report.documents:
        for field in document.fields:
            if field.confidence is None:
                continue
            buckets[_bucket(field.confidence)].append(field.correct_normalised)

    total_fields = sum(len(v) for v in buckets.values())
    if total_fields == 0:
        print("No scored fields — nothing to calibrate. Was every document a hard failure?")
        return 1

    print(f"\n{'Score range':<14}{'Fields':>8}{'Accuracy':>10}   Current threshold")
    print("-" * 60)
    for i in range(DECILES):
        low, high = i / DECILES, (i + 1) / DECILES
        outcomes = buckets.get(i, [])
        acc = sum(outcomes) / len(outcomes) if outcomes else None
        marker = ""
        if low <= HIGH_THRESHOLD < high:
            marker = f"  <- HIGH_THRESHOLD ({HIGH_THRESHOLD})"
        elif low <= MEDIUM_THRESHOLD < high:
            marker = f"  <- MEDIUM_THRESHOLD ({MEDIUM_THRESHOLD})"
        acc_str = f"{acc * 100:.1f}%" if acc is not None else "—"
        print(f"[{low:.1f}, {high:.1f})   {len(outcomes):>6}   {acc_str:>8}{marker}")

    # A monotonic decile accuracy curve is the property that matters — not a
    # specific number. Report whether it holds rather than silently trusting it.
    ordered = [(sum(buckets[i]) / len(buckets[i])) for i in range(DECILES) if buckets.get(i)]
    violations = sum(1 for a, b in pairwise(ordered) if b < a - 0.05)
    print(
        f"\nMonotonicity: {len(ordered) - 1 - violations}/{max(1, len(ordered) - 1)} "
        f"adjacent buckets non-decreasing (a >5pt drop counts as a violation)."
    )
    if violations:
        print(
            "Some buckets are less accurate than a lower-scored bucket — the score is "
            "not cleanly separating correct from incorrect there. Expected with a small "
            "sample in a bucket; worth a second look if it persists at larger N."
        )
    else:
        print("No violations — accuracy rises with score across every populated bucket.")

    print(
        f"\nCurrent bands: high >= {HIGH_THRESHOLD}, medium >= {MEDIUM_THRESHOLD}. "
        "This script reports; it does not rewrite domain/confidence.py — changing a "
        "production threshold is a decision to make deliberately, with this evidence "
        "in hand, not something to automate."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default=None, help="Defaults to DOCFLOW_LLM_PROVIDER.")
    parser.add_argument(
        "--size", type=int, default=None, help="Truncate the corpus to N documents."
    )
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
