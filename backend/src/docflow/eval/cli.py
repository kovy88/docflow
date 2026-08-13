"""Evaluation CLI.

    uv run docflow-eval                          # baseline vs configured provider
    uv run docflow-eval --size 200               # bigger corpus
    uv run docflow-eval --only baseline          # skip the provider run
    uv run docflow-eval --provider anthropic     # measure a real model

Writes a markdown report and a JSON blob to `eval_results/`. The JSON is the
machine-readable artifact CI compares between runs to catch regressions.

**Every number this prints is computed from a real run.** Nothing is a placeholder.
When a run is not possible (no API key), the report says so instead of guessing.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from docflow.config import get_settings
from docflow.eval.dataset import DEFAULT_CORPUS_PATH, build_corpus, read_corpus, write_corpus
from docflow.eval.metrics import EvaluationReport, compare_reports
from docflow.eval.runner import BaselineRunner, ExtractorRunner, RunnerConfig
from docflow.observability.logging import configure_logging

RESULTS_DIR = Path(__file__).resolve().parents[3] / "eval_results"


def _pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "n/a"


def _num(value: float | None, digits: int = 1) -> str:
    return f"{value:,.{digits}f}" if value is not None else "n/a"


def _usd(value: float | None) -> str:
    return f"${value:.4f}" if value is not None else "n/a"


def render_markdown(reports: list[EvaluationReport], *, corpus_note: str) -> str:
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Evaluation results",
        "",
        f"Generated {now} by `uv run docflow-eval`.",
        "",
        corpus_note,
        "",
        "## Headline",
        "",
        "| Run | Field acc. (norm.) | Required | Critical | Doc success | Review rate | Cost/doc | Mean latency |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        lines.append(
            f"| {report.label} "
            f"| {_pct(report.field_accuracy())} "
            f"| {_pct(report.required_field_accuracy())} "
            f"| {_pct(report.critical_field_accuracy())} "
            f"| {_pct(report.document_success_rate())} "
            f"| {_pct(report.review_rate())} "
            f"| {_usd(report.cost().get('per_document_usd'))} "
            f"| {_num(report.latency().get('mean_ms'))} ms |"
        )

    lines += ["", "**Field acc. (norm.)** counts a field correct when it matches after "
              "type-aware normalisation — `39 930,00 Kč` equals `39930.00`. "
              "**Doc success** is the fraction of documents where *every* required "
              "field is correct, which is the number that maps to whether a human "
              "has to intervene.", ""]

    # State the gap in the artifact itself. A report that silently omits the model
    # run invites the reader to assume the numbers above describe one.
    if not any(r.extractor == "llm" and r.provider not in ("fixture", "none") for r in reports):
        lines += [
            "> ### ⚠️ Language-model accuracy is NOT measured in this run",
            "> ",
            "> Every row above was produced by a **deterministic rule-based extractor**, "
            "not by a language model. No API key was configured, so no model was called "
            "and none of these numbers describe model performance.",
            "> ",
            "> Model accuracy, cost per document and latency are **not yet measured**. "
            "They are deliberately left blank rather than estimated.",
            "> ",
            "> To measure them:",
            "> ",
            "> ```bash",
            "> echo 'DOCFLOW_LLM_ANTHROPIC_API_KEY=sk-ant-...' >> backend/.env",
            "> uv run docflow-eval --provider anthropic",
            "> ```",
            "",
        ]

    for report in reports:
        lines += _render_detail(report)

    if len(reports) >= 2:
        comparison = compare_reports(reports[0], reports[1])
        lines += [
            "## Baseline vs model",
            "",
            "| Metric | Baseline | Model | Delta |",
            "|---|---|---|---|",
        ]
        for key in ("field_accuracy", "required_field_accuracy", "document_success_rate"):
            row = comparison[key]
            lines.append(
                f"| {key.replace('_', ' ')} | {_pct(row['baseline'])} | "
                f"{_pct(row['candidate'])} | "
                f"{('+' if (row['delta'] or 0) >= 0 else '')}{_pct(row['delta'])} |"
            )
        lines += [
            f"| cost per document | {_usd(comparison['cost_per_document_usd']['baseline'])} "
            f"| {_usd(comparison['cost_per_document_usd']['candidate'])} | |",
            f"| mean latency | {_num(comparison['mean_latency_ms']['baseline'])} ms "
            f"| {_num(comparison['mean_latency_ms']['candidate'])} ms | |",
            "",
        ]

    return "\n".join(lines) + "\n"


def _render_detail(report: EvaluationReport) -> list[str]:
    lines = [f"## {report.label}", ""]
    lines += [
        f"- Extractor: `{report.extractor}` · provider `{report.provider}` · model `{report.model}`",
        f"- Documents: {len(report.documents)} · wall clock {report.wall_clock_seconds:.1f}s",
        "",
        "### Accuracy by match level",
        "",
        "| Level | Field accuracy |",
        "|---|---|",
        f"| exact (string identical) | {_pct(report.field_accuracy('exact'))} |",
        f"| normalised (type-aware) | {_pct(report.field_accuracy('normalised'))} |",
        f"| fuzzy (free text ≥90% similar) | {_pct(report.field_accuracy('fuzzy'))} |",
        "",
    ]

    pr = report.precision_recall()
    lines += [
        "### Precision / recall",
        "",
        f"Precision {_pct(pr['precision'])} · Recall {_pct(pr['recall'])} · F1 {_pct(pr['f1'])} "
        f"(TP {pr['true_positive']}, FP {pr['false_positive']}, FN {pr['false_negative']})",
        "",
        f"Classification accuracy: {_pct(report.classification_accuracy())}",
        f"Validation failure rate: {_pct(report.validation_failure_rate())}",
        f"Hard failure rate: {_pct(report.failure_rate())}",
        "",
    ]

    latency = report.latency()
    if latency:
        lines += [
            "### Latency",
            "",
            f"mean {_num(latency['mean_ms'])} ms · p50 {_num(latency['p50_ms'])} ms · "
            f"p95 {_num(latency['p95_ms'])} ms · p99 {_num(latency['p99_ms'])} ms",
            "",
        ]

    cost = report.cost()
    if cost:
        lines += [
            "### Cost",
            "",
            f"Total {_usd(cost['total_usd'])} · per document {_usd(cost['per_document_usd'])} · "
            f"{_num(cost['tokens_per_document'], 0)} tokens/document",
            "",
        ]

    calibration = report.calibration()
    if calibration:
        lines += [
            "### Confidence calibration",
            "",
            "Does the confidence score separate correct fields from incorrect ones?",
            "",
            "| Band | Fields | Actual accuracy | Mean score |",
            "|---|---|---|---|",
        ]
        for row in calibration:
            lines.append(
                f"| {row['band']} | {row['fields']} | {_pct(row['accuracy'])} "
                f"| {row['mean_confidence']:.3f} |"
            )
        lines.append("")

    worst = report.worst_fields()
    if worst:
        lines += [
            "### Fields with the most errors",
            "",
            "| Field | Errors | Occurrences | Accuracy | Required |",
            "|---|---|---|---|---|",
        ]
        for row in worst:
            lines.append(
                f"| `{row['field_path']}` | {row['errors']} | {row['total']} "
                f"| {_pct(row['accuracy'])} | {'yes' if row['required'] else 'no'} |"
            )
        lines.append("")

    difficulty = report.by_difficulty()
    if difficulty:
        lines += [
            "### Accuracy by injected difficulty",
            "",
            "| Hazard | Fields | Accuracy |",
            "|---|---|---|",
        ]
        for row in difficulty:
            lines.append(f"| {row['difficulty']} | {row['fields']} | {_pct(row['accuracy'])} |")
        lines.append("")

    return lines


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.observability)

    corpus_path = Path(args.corpus) if args.corpus else DEFAULT_CORPUS_PATH
    if args.regenerate or not corpus_path.exists():
        corpus = build_corpus(size=args.size, seed=args.seed)
        write_corpus(corpus_path, corpus)
        print(f"Generated corpus: {len(corpus)} documents -> {corpus_path}")
    else:
        corpus = read_corpus(corpus_path)
        print(f"Loaded corpus: {len(corpus)} documents from {corpus_path}")

    if args.size and len(corpus) > args.size:
        corpus = corpus[: args.size]

    by_type: dict[str, int] = {}
    for item in corpus:
        by_type[item.document_type] = by_type.get(item.document_type, 0) + 1
    print(f"  mix: {', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}")

    reports: list[EvaluationReport] = []

    if args.only in (None, "baseline"):
        print("\nRunning rule-based baseline...")
        reports.append(await BaselineRunner().run(corpus, config=RunnerConfig("baseline (rules)")))
        print(f"  done in {reports[-1].wall_clock_seconds:.1f}s")

    if args.only in (None, "llm"):
        provider_name = args.provider or settings.llm.provider
        try:
            provider = _build_provider(provider_name, settings)
        except Exception as exc:  # noqa: BLE001
            print(f"\n! Skipping model run: {exc}")
            print("  Set DOCFLOW_LLM_PROVIDER and the matching API key to measure a real model.")
            provider = None

        if provider is not None:
            label = f"{provider.name}/{settings.llm.model}"
            if provider.name == "fixture":
                label += " [deterministic local extractor, NOT a language model]"
            print(f"\nRunning {label}...")
            runner = ExtractorRunner(provider, settings=settings)
            reports.append(
                await runner.run(corpus, config=RunnerConfig(label, concurrency=args.concurrency))
            )
            print(f"  done in {reports[-1].wall_clock_seconds:.1f}s")
            await provider.aclose()

    if not reports:
        print("No runs completed.")
        return 1

    corpus_note = (
        f"Corpus: **{len(corpus)} synthetic documents** with exact ground truth "
        f"(seed `{args.seed}`). Synthetic documents are generated from templates in "
        "this repository, so these numbers are an **upper bound** on real-world "
        "accuracy — see `docs/EVALUATION.md` for what that does and does not "
        "support."
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")

    markdown = render_markdown(reports, corpus_note=corpus_note)
    (RESULTS_DIR / "latest.md").write_text(markdown, encoding="utf-8")
    (RESULTS_DIR / f"report-{stamp}.md").write_text(markdown, encoding="utf-8")

    payload: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "corpus_size": len(corpus),
        "corpus_seed": args.seed,
        "corpus_mix": by_type,
        "runs": [r.to_dict() for r in reports],
    }
    (RESULTS_DIR / "latest.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    print("\n" + "=" * 78)
    print(markdown.split("## Baseline vs model")[0].split("## " + reports[0].label)[0].strip())
    print("=" * 78)
    print(f"\nReports written to {RESULTS_DIR}/latest.md and latest.json")
    return 0


def _build_provider(name: str, settings):
    from docflow.config import LLMSettings
    from docflow.llm.registry import build_provider

    return build_provider(LLMSettings(**{**settings.llm.model_dump(), "provider": name}))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="docflow-eval", description="Measure extraction quality against ground truth."
    )
    parser.add_argument("--size", type=int, default=120, help="corpus size (default 120)")
    parser.add_argument("--seed", type=int, default=20240613, help="corpus seed")
    parser.add_argument("--corpus", type=str, default=None, help="path to an existing corpus")
    parser.add_argument("--regenerate", action="store_true", help="rebuild the corpus")
    parser.add_argument("--only", choices=["baseline", "llm"], default=None)
    parser.add_argument("--provider", choices=["anthropic", "openai", "fixture"], default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
