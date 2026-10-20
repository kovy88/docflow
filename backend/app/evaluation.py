"""Scoring of pipeline results against the labelled dataset.

Field accuracy compares normalised values (so "12 100,00 Kč" and "12100" agree); rule
detection measures whether planted document errors are flagged without false alarms;
grounding/confidence splits show whether the signals the UI surfaces actually predict
wrong values."""

import re
import unicodedata
from difflib import SequenceMatcher
from statistics import mean, median
from typing import Any

RULE_CODES = {"sum_mismatch", "ico_checksum", "date_order"}
HIGH_CONFIDENCE = 0.9


def _canon(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" .,;:\"'„“”()")


def values_match(kind: str, expected: Any, predicted: Any) -> bool:
    # A list is a set of acceptable answers (real documents: annual or monthly rent, …).
    if isinstance(expected, list):
        return any(values_match(kind, e, predicted) for e in expected)
    if expected is None or predicted is None:
        return expected is None and predicted is None
    if kind == "number":
        return abs(float(expected) - float(predicted)) < 0.01
    if kind == "date":
        return expected == predicted
    e, p = _canon(expected), _canon(predicted)
    if e.replace(" ", "").isdigit():  # IČO, policy numbers: ignore spacing
        return e.replace(" ", "") == p.replace(" ", "")
    if len(e) > 30:  # free-text fields such as a contract subject
        return e in p or p in e or SequenceMatcher(None, e, p).ratio() >= 0.85
    return e == p


def score_document(label: dict, result: dict) -> dict:
    """Compare one pipeline result with its label; returns per-field rows and rule counts."""
    fields = {f["key"]: f for f in result.get("fields", [])}
    rows = []
    for key, expected in label["expected"].items():
        f = fields.get(key, {})
        predicted = f.get("value")
        rows.append(
            {
                "key": key,
                "expected": expected,
                "predicted": predicted,
                "raw": f.get("raw"),
                "quote": f.get("quote"),
                "correct": values_match(f.get("type", "string"), expected, predicted),
                "grounded": f.get("grounded", False),
                "confidence": f.get("confidence", 0.0),
            }
        )
    expected_rules = set(label["expected_issues"]) & RULE_CODES
    predicted_rules = {i["code"] for i in result.get("issues", [])} & RULE_CODES
    return {
        "file": label["file"],
        "template_id": label["template_id"],
        "difficulty": label.get("difficulty", "standard"),
        "status": result.get("status"),
        "fields": rows,
        "rules": {
            "expected": sorted(expected_rules),
            "predicted": sorted(predicted_rules),
            "tp": len(expected_rules & predicted_rules),
            "fp": len(predicted_rules - expected_rules),
            "fn": len(expected_rules - predicted_rules),
        },
    }


def _rate(rows: list[dict]) -> float | None:
    return round(sum(r["correct"] for r in rows) / len(rows), 4) if rows else None


def summarize(docs: list[dict]) -> dict:
    """Aggregate scored documents (output of score_document plus latency/usage/error)."""
    ok_docs = [d for d in docs if not d.get("error")]
    rows = [
        r | {"template_id": d["template_id"], "difficulty": d.get("difficulty", "standard")}
        for d in ok_docs
        for r in d["fields"]
    ]
    present = [r for r in rows if r["predicted"] is not None]
    tp = sum(d["rules"]["tp"] for d in ok_docs)
    fp = sum(d["rules"]["fp"] for d in ok_docs)
    fn = sum(d["rules"]["fn"] for d in ok_docs)
    latencies = [d["latency_s"] for d in ok_docs if "latency_s" in d]
    tokens = [
        d["usage"].get("input_tokens", 0) + d["usage"].get("output_tokens", 0)
        for d in ok_docs
        if d.get("usage")
    ]
    by = lambda attr: sorted({r[attr] for r in rows})  # noqa: E731
    return {
        "documents": len(docs),
        "failed_documents": len(docs) - len(ok_docs),
        "fields": len(rows),
        "field_accuracy": _rate(rows),
        "documents_fully_correct": sum(all(r["correct"] for r in d["fields"]) for d in ok_docs),
        "per_template": {
            t: _rate([r for r in rows if r["template_id"] == t]) for t in by("template_id")
        },
        "per_field": {k: _rate([r for r in rows if r["key"] == k]) for k in by("key")},
        "per_difficulty": {
            k: _rate([r for r in rows if r["difficulty"] == k]) for k in by("difficulty")
        },
        "rule_detection": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        },
        "signals": {
            "grounded_share": _share(present, "grounded"),
            "accuracy_grounded": _rate([r for r in present if r["grounded"]]),
            "accuracy_ungrounded": _rate([r for r in present if not r["grounded"]]),
            "accuracy_high_confidence": _rate(
                [r for r in present if r["confidence"] >= HIGH_CONFIDENCE]
            ),
            "accuracy_low_confidence": _rate(
                [r for r in present if r["confidence"] < HIGH_CONFIDENCE]
            ),
        },
        "latency_s": {
            "mean": round(mean(latencies), 1) if latencies else None,
            "median": round(median(latencies), 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "tokens_per_document": round(mean(tokens)) if tokens else None,
        "errors": [
            {"file": d["file"], **{k: r[k] for k in ("key", "expected", "predicted", "raw")}}
            for d in ok_docs
            for r in d["fields"]
            if not r["correct"]
        ],
    }


def _share(rows: list[dict], attr: str) -> float | None:
    return round(sum(bool(r[attr]) for r in rows) / len(rows), 4) if rows else None


def markdown(summary: dict, title: str) -> str:
    s = summary
    rd = s["rule_detection"]
    pct = lambda x: "–" if x is None else f"{x:.1%}"  # noqa: E731
    lines = [
        f"## {title}",
        "",
        "| Metrika | Hodnota |",
        "|---|---|",
        f"| Dokumenty (selhalo) | {s['documents']} ({s['failed_documents']}) |",
        f"| Přesnost polí | {pct(s['field_accuracy'])} ({s['fields']} polí) |",
        f"| Dokumenty bez chyby | {s['documents_fully_correct']} / {s['documents'] - s['failed_documents']} |",
        f"| Vložené chyby zachyceny (recall) | {pct(rd['recall'])} ({rd['tp']}/{rd['tp'] + rd['fn']}) |",
        f"| Nálezy pravidel navíc (chyba dokumentu nebo modelu) | {rd['fp']} |",
        f"| Přesnost ověřených / neověřených polí | {pct(s['signals']['accuracy_grounded'])} / {pct(s['signals']['accuracy_ungrounded'])} |",
        f"| Přesnost při jistotě ≥ 90 % / < 90 % | {pct(s['signals']['accuracy_high_confidence'])} / {pct(s['signals']['accuracy_low_confidence'])} |",
        f"| Čas na dokument (medián / max) | {s['latency_s']['median']} s / {s['latency_s']['max']} s |",
        f"| Tokeny na dokument | {s['tokens_per_document']} |",
        "",
        "Podle šablony: " + ", ".join(f"{k} {pct(v)}" for k, v in s["per_template"].items()),
        "",
        "Podle obtížnosti: "
        + ", ".join(f"{k} {pct(v)}" for k, v in s.get("per_difficulty", {}).items()),
    ]
    if s["errors"]:
        lines += ["", "| Dokument | Pole | Očekáváno | Model |", "|---|---|---|---|"]
        lines += [
            f"| {e['file']} | {e['key']} | {e['expected']} | {e['predicted'] if e['predicted'] is not None else '–'} |"
            for e in s["errors"]
        ]
    return "\n".join(lines) + "\n"
