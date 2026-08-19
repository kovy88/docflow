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

Also computes Expected Calibration Error (ECE) and a coarse 6-bucket view
(0.0-0.5, 0.5-0.6, ..., 0.9-1.0) alongside the decile table — the simplest
defensible formulation, not a research-grade implementation: ECE is the
sample-weighted mean absolute gap between each bucket's mean confidence score
and its actual accuracy (`sum(n_i/N * |acc_i - mean_score_i|)`), which is the
standard definition and needs nothing beyond arithmetic already available from
the per-field (score, correct) pairs this script collects. Results are also
saved as JSON (`eval_results/calibration-<timestamp>.json`) — the printed
table alone wasn't a durable artifact.

Usage:
    uv run docflow-calibrate
    uv run docflow-calibrate --provider google --size 20
    DOCFLOW_LLM_MODEL=gpt-5.6-luna uv run docflow-calibrate --provider openai
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from collections import defaultdict
from itertools import pairwise

from docflow.config import get_settings
from docflow.domain.confidence import HIGH_THRESHOLD, MEDIUM_THRESHOLD
from docflow.eval.cli import RESULTS_DIR, _build_provider
from docflow.eval.dataset import DEFAULT_CORPUS_PATH, read_corpus
from docflow.eval.runner import ExtractorRunner, RunnerConfig

DECILES = 10
# The exact ranges Phase 7 of the evaluation program asks for — coarser than
# deciles, useful because 6 buckets stay legible at a smaller real-model N
# where several individual deciles would be near-empty.
COARSE_EDGES = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def _bucket(score: float) -> int:
    # score == 1.0 must land in the last bucket (index 9), not overflow into a
    # nonexistent 10th.
    return min(DECILES - 1, int(score * DECILES))


def _coarse_bucket(score: float) -> int:
    for i in range(len(COARSE_EDGES) - 2, -1, -1):
        if score >= COARSE_EDGES[i]:
            return i
    return 0


def _expected_calibration_error(
    scores: list[float], correct: list[bool], *, n_buckets: int = 10
) -> float:
    """Sample-weighted mean |accuracy - mean confidence| across score buckets."""
    if not scores:
        return 0.0
    buckets: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for s, c in zip(scores, correct, strict=True):
        buckets[min(n_buckets - 1, int(s * n_buckets))].append((s, c))
    total = len(scores)
    ece = 0.0
    for pairs in buckets.values():
        bucket_scores = [p[0] for p in pairs]
        bucket_correct = [p[1] for p in pairs]
        mean_conf = sum(bucket_scores) / len(bucket_scores)
        acc = sum(bucket_correct) / len(bucket_correct)
        ece += (len(pairs) / total) * abs(acc - mean_conf)
    return ece


async def _run(args: argparse.Namespace) -> int:  # noqa: PLR0912, PLR0915 — linear reporting script, not logic to decompose
    settings = get_settings()
    if args.model:
        settings = settings.model_copy(
            update={"llm": settings.llm.model_copy(update={"model": args.model})}
        )
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

    pairs: list[tuple[float, bool]] = [
        (field.confidence, field.correct_normalised)
        for document in report.documents
        for field in document.fields
        if field.confidence is not None
    ]
    scores = [s for s, _ in pairs]
    corrects = [c for _, c in pairs]

    buckets: dict[int, list[bool]] = defaultdict(list)
    for score, correct in pairs:
        buckets[_bucket(score)].append(correct)

    total_fields = len(pairs)
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

    coarse_buckets: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for score, correct in pairs:
        coarse_buckets[_coarse_bucket(score)].append((score, correct))

    coarse_rows = []
    print(f"\n{'Score range':<14}{'Fields':>8}{'Accuracy':>10}{'Mean score':>12}")
    print("-" * 60)
    for i in range(len(COARSE_EDGES) - 1):
        low, high = COARSE_EDGES[i], COARSE_EDGES[i + 1]
        pairs_in_bucket = coarse_buckets.get(i, [])
        if pairs_in_bucket:
            acc = sum(c for _, c in pairs_in_bucket) / len(pairs_in_bucket)
            mean_score = sum(s for s, _ in pairs_in_bucket) / len(pairs_in_bucket)
        else:
            acc = mean_score = None
        print(
            f"[{low:.1f}, {high:.1f}{']' if high == 1.0 else ')'}   {len(pairs_in_bucket):>6}   "
            f"{(f'{acc * 100:.1f}%' if acc is not None else '—'):>8}   "
            f"{(f'{mean_score:.3f}' if mean_score is not None else '—'):>9}"
        )
        coarse_rows.append(
            {
                "range": [low, high],
                "fields": len(pairs_in_bucket),
                "accuracy": acc,
                "mean_score": mean_score,
            }
        )

    ece = _expected_calibration_error(scores, corrects, n_buckets=DECILES)
    print(f"\nExpected Calibration Error (ECE, 10 buckets): {ece:.4f}")
    print(
        "Lower is better — 0 would mean confidence scores exactly match observed "
        "accuracy in every bucket. This is the simplest standard ECE formulation "
        "(sample-weighted mean |accuracy - mean confidence| per bucket), not a "
        "research benchmark implementation."
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    payload = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "provider": provider.name,
        "model": settings.llm.model,
        "documents": len(corpus),
        "scored_fields": total_fields,
        "ece_10_bucket": ece,
        "coarse_buckets": coarse_rows,
        "deciles": [
            {
                "range": [i / DECILES, (i + 1) / DECILES],
                "fields": len(buckets.get(i, [])),
                "accuracy": (sum(buckets[i]) / len(buckets[i]) if buckets.get(i) else None),
            }
            for i in range(DECILES)
        ],
    }
    out_path = RESULTS_DIR / f"calibration-{provider.name}-{stamp}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (RESULTS_DIR / "calibration-latest.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(f"\nSaved to {out_path} and {RESULTS_DIR}/calibration-latest.json")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default=None, help="Defaults to DOCFLOW_LLM_PROVIDER.")
    parser.add_argument(
        "--model",
        default=None,
        help="Override the model for this run. Defaults to DOCFLOW_LLM_MODEL.",
    )
    parser.add_argument(
        "--size", type=int, default=None, help="Truncate the corpus to N documents."
    )
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
