"""Deterministic post-processing of LLM answers: normalisation, grounding against the
PDF text and template rules. Nothing here trusts the model."""

import re
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from app.templates import DateOrderRule, IcoRule, SumRule, Template

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y")


def parse_number(raw: str) -> float:
    s = re.sub(r"[^\d,.\-]", "", raw.replace("−", "-"))
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if decimal == "," else ",", "").replace(",", ".")
    elif s.count(",") == 1:
        s = s.replace(",", ".")
    elif s.count(",") > 1 or s.count(".") > 1:
        s = s.replace(",", "").replace(".", "")
    s = s.rstrip(".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        raise ValueError(f"not a number: {raw!r}")
    return float(s)


def parse_date(raw: str) -> date:
    s = re.sub(r"\s+", "", raw)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date: {raw!r}")


def normalize(raw: str, kind: str) -> Any:
    if kind == "number":
        return parse_number(raw)
    if kind == "date":
        return parse_date(raw).isoformat()
    return raw.strip()


def _canon(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def quote_in(quote: str, page_text: str, threshold: float = 0.9) -> bool:
    q, t = _canon(quote), _canon(page_text)
    if not q:
        return False
    if q in t:
        return True
    # PDF text layers often differ from the rendered text (hyphenation, spacing).
    m = SequenceMatcher(None, t, q, autojunk=False).find_longest_match(0, len(t), 0, len(q))
    return m.size / len(q) >= threshold


def locate_quote(quote: str, pages: list[str], page: int | None) -> int | None:
    """Return the 1-based page that really contains the quote, preferring the cited one."""
    order = list(range(1, len(pages) + 1))
    if page in order:
        order.remove(page)
        order.insert(0, page)
    return next((p for p in order if quote_in(quote, pages[p - 1])), None)


def ico_valid(raw: str) -> bool:
    digits = re.sub(r"\s", "", raw)
    if not digits.isdigit() or len(digits) > 8:
        return False
    digits = digits.zfill(8)
    remainder = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1), strict=True)) % 11
    return (11 - remainder) % 10 == int(digits[7])


def cz_number(x: float) -> str:
    return f"{x:,.2f}".replace(",", "\u00a0").replace(".", ",")


def issue(severity: str, code: str, message: str, *fields: str) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "fields": list(fields)}


def apply_rules(template: Template, values: dict[str, Any]) -> list[dict[str, Any]]:
    labels = {f.key: f.label for f in template.fields}
    issues = []
    for rule in template.rules:
        if isinstance(rule, IcoRule):
            raw = values.get(rule.field)
            if raw is not None and not ico_valid(str(raw)):
                issues.append(
                    issue(
                        "error",
                        "ico_checksum",
                        f"{labels[rule.field]} {raw} má neplatný kontrolní součet.",
                        rule.field,
                    )
                )
        elif isinstance(rule, SumRule):
            parts = [values.get(k) for k in rule.fields]
            total = values.get(rule.equals)
            if total is None or any(p is None for p in parts):
                continue
            computed = round(sum(parts), 2)
            if abs(computed - total) > rule.tolerance:
                names = " + ".join(labels[k] for k in rule.fields)
                issues.append(
                    issue(
                        "error",
                        "sum_mismatch",
                        f"{names} = {cz_number(computed)}, ale {labels[rule.equals]} = {cz_number(total)}.",
                        *rule.fields,
                        rule.equals,
                    )
                )
        elif isinstance(rule, DateOrderRule):
            earlier, later = values.get(rule.earlier), values.get(rule.later)
            if earlier and later and earlier > later:
                issues.append(
                    issue(
                        "error",
                        "date_order",
                        f"{labels[rule.earlier]} ({earlier}) je po {labels[rule.later]} ({later}).",
                        rule.earlier,
                        rule.later,
                    )
                )
    return issues
