"""Compare two evaluation reports and flag regressions.

Infrastructure for the workflow docs/EVALUATION_PROTOCOL.md describes: run the
baseline, change something (prompt, model, threshold), run again, compare. Not
wired into CI — real-LLM evaluation runs cost money and take minutes, so running
one on every PR is a choice to make deliberately, not a default this script
imposes. This is the offline tool a future CI job (or a human, on demand) would
call.

Usage:
    uv run docflow-eval-compare --before eval_results/report-20260818-070354.json \\
                                 --after  eval_results/latest.json

Matches runs between the two files by (provider, model) — not by label, since
labels carry cosmetic suffixes (" (scanned)", "[deterministic ...]") that would
otherwise prevent an exact match. A run present in one file and not the other is
reported, not silently skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Provisional engineering thresholds (see docs/EVALUATION_PROTOCOL.md and
# docs/EVALUATION.md's quality-gates section) — not derived from a real
# customer SLA, because none exists yet. Chosen to be loose enough that normal
# run-to-run model noise (see gpt-5.6-luna's unpinned temperature,
# docs/EVALUATION.md) doesn't trip them, tight enough to catch an actual
# regression. Revisit once real usage data exists.
GATES = {
    "required_field_accuracy_max_drop_pct": 3.0,
    "critical_field_accuracy_max_drop_pct": 3.0,
    "document_success_rate_min_drop_pct": 0.0,  # must not decrease at all
    "review_rate_max_increase_pct": 10.0,
}


def _load(path: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    runs = payload.get("runs", [])
    return {(r["provider"], r["model"]): r for r in runs}


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _delta_pct(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return (after - before) * 100


def compare(before_path: str, after_path: str) -> tuple[list[dict[str, Any]], list[str]]:
    before_runs = _load(before_path)
    after_runs = _load(after_path)

    rows: list[dict[str, Any]] = []
    violations: list[str] = []

    all_keys = sorted(set(before_runs) | set(after_runs))
    for key in all_keys:
        provider, model = key
        before = before_runs.get(key)
        after = after_runs.get(key)
        if before is None:
            rows.append({"provider": provider, "model": model, "status": "new run, no baseline"})
            continue
        if after is None:
            rows.append({"provider": provider, "model": model, "status": "missing in after"})
            continue

        b_acc, a_acc = before["accuracy"], after["accuracy"]
        b_op, a_op = before["operational"], after["operational"]
        b_cost, a_cost = before.get("cost", {}), after.get("cost", {})

        row = {
            "provider": provider,
            "model": model,
            "field_accuracy": (b_acc["field_normalised"], a_acc["field_normalised"]),
            "required_field_accuracy": (b_acc["required_normalised"], a_acc["required_normalised"]),
            "critical_field_accuracy": (b_acc["critical_normalised"], a_acc["critical_normalised"]),
            "document_success_rate": (
                b_acc["document_success_rate"],
                a_acc["document_success_rate"],
            ),
            "review_rate": (b_op["review_rate"], a_op["review_rate"]),
            "cost_per_doc_usd": (b_cost.get("per_document_usd"), a_cost.get("per_document_usd")),
        }
        rows.append(row)

        req_delta = _delta_pct(*row["required_field_accuracy"])
        if req_delta is not None and req_delta < -GATES["required_field_accuracy_max_drop_pct"]:
            violations.append(
                f"{provider}/{model}: required-field accuracy dropped {abs(req_delta):.1f} pts "
                f"(gate: max {GATES['required_field_accuracy_max_drop_pct']} pt drop)"
            )

        crit_delta = _delta_pct(*row["critical_field_accuracy"])
        if crit_delta is not None and crit_delta < -GATES["critical_field_accuracy_max_drop_pct"]:
            violations.append(
                f"{provider}/{model}: critical-field accuracy dropped {abs(crit_delta):.1f} pts "
                f"(gate: max {GATES['critical_field_accuracy_max_drop_pct']} pt drop)"
            )

        success_delta = _delta_pct(*row["document_success_rate"])
        if (
            success_delta is not None
            and success_delta < -GATES["document_success_rate_min_drop_pct"]
        ):
            violations.append(
                f"{provider}/{model}: document success rate decreased "
                f"({_pct(row['document_success_rate'][0])} -> {_pct(row['document_success_rate'][1])}) "
                "(gate: must not decrease)"
            )

        review_delta = _delta_pct(*row["review_rate"])
        if review_delta is not None and review_delta > GATES["review_rate_max_increase_pct"]:
            violations.append(
                f"{provider}/{model}: review rate increased {review_delta:.1f} pts "
                f"(gate: max +{GATES['review_rate_max_increase_pct']} pt increase) — "
                "if this is an intentional OCR/degradation comparison, that's expected; "
                "the gate doesn't know the difference, a human reading this does"
            )

    return rows, violations


def render(rows: list[dict[str, Any]], violations: list[str]) -> str:
    lines = ["# Evaluation regression comparison", ""]
    lines.append(
        "| Provider/model | Field acc. | Required | Critical | Doc success | Review rate | Cost/doc |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for row in rows:
        if "status" in row:
            lines.append(f"| {row['provider']}/{row['model']} | {row['status']} | | | | | |")
            continue
        lines.append(
            f"| {row['provider']}/{row['model']} "
            f"| {_pct(row['field_accuracy'][0])} -> {_pct(row['field_accuracy'][1])} "
            f"| {_pct(row['required_field_accuracy'][0])} -> {_pct(row['required_field_accuracy'][1])} "
            f"| {_pct(row['critical_field_accuracy'][0])} -> {_pct(row['critical_field_accuracy'][1])} "
            f"| {_pct(row['document_success_rate'][0])} -> {_pct(row['document_success_rate'][1])} "
            f"| {_pct(row['review_rate'][0])} -> {_pct(row['review_rate'][1])} "
            f"| ${row['cost_per_doc_usd'][0] or 0:.4f} -> ${row['cost_per_doc_usd'][1] or 0:.4f} |"
        )
    lines.append("")
    if violations:
        lines.append(f"## {len(violations)} provisional quality gate(s) failed")
        lines.append("")
        lines.extend(f"- {v}" for v in violations)
    else:
        lines.append("## All provisional quality gates passed")
    lines.append("")
    lines.append(
        "Gates are **provisional engineering thresholds** (see "
        "`docflow/scripts/eval_compare.py::GATES`), not derived from a real "
        "customer SLA — treat a failure as a prompt to look, not an automatic block."
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, help="path to the baseline report JSON")
    parser.add_argument("--after", required=True, help="path to the new report JSON")
    args = parser.parse_args()

    rows, violations = compare(args.before, args.after)
    print(render(rows, violations))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
