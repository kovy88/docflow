"""Deterministic post-processing of LLM answers: normalisation, grounding against the
PDF text and template rules. Nothing here trusts the model."""

import re
import unicodedata
from collections.abc import Collection
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from app.templates import DateOrderRule, IcoRule, SumRule, Template

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y")
# Contracts often spell the month out: "1. října 2026" (genitive) or "říjen 2026".
CZ_MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("ledna", "leden"),
            ("února", "únor"),
            ("března", "březen"),
            ("dubna", "duben"),
            ("května", "květen"),
            ("června", "červen"),
            ("července", "červenec"),
            ("srpna", "srpen"),
            ("září",),
            ("října", "říjen"),
            ("listopadu", "listopad"),
            ("prosince", "prosinec"),
        ],
        start=1,
    )
    for name in names
}


def parse_number(raw: str) -> float:
    # OCR reads a thousands space as a dot: "3.000 000 Kč". Digit groups of three mixing
    # dots and spaces can only be thousands separators.
    if re.fullmatch(r"\s*-?\d{1,3}(?:[ \u00a0.]\d{3})+(?:,\d+)?\D*", raw) and re.search(
        r"\d \d", raw
    ):
        raw = raw.replace(".", " ")
    # Only the first number counts: "700,- Kč za hodinu (60 minut)" is 700, not 70060.
    # A space continues the number only before a group of exactly three digits.
    found = re.search(r"-?\d(?:[\d.,]|[  ](?=\d{3}(?!\d)))*", raw.replace("−", "-"))
    if found is None:
        raise ValueError(f"not a number: {raw!r}")
    s = re.sub(r"[  ]", "", found[0]).rstrip(",")  # "185 000,- Kč" → "185000"
    # Czech documents use the dot only as a thousands separator ("100.000 Kč",
    # "9.300,- Kč"); the decimal mark is a comma. Measured on real contracts, where every
    # such value was a thousands amount.
    if re.fullmatch(r"-?[1-9]\d{0,2}(?:\.\d{3})+", s):
        s = s.replace(".", "")
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
    # Insurance documents add a time of day: "1. 10. 2026, 0:00 hod.", "30. 9. 2027 24:00".
    raw = re.sub(r"[,\s]+(od\s+|do\s+)?\d{1,2}:\d{2}(\s*hod\.?)?\s*$", "", raw.strip())
    worded = re.fullmatch(r"\s*(\d{1,2})\.\s*([^\W\d_]+)\s+(\d{4})\s*", raw.casefold())
    if worded and worded[2] in CZ_MONTHS:
        return date(int(worded[3]), CZ_MONTHS[worded[2]], int(worded[1]))
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


def quote_in(quote: str, page_text: str, threshold: float = 0.9, ocr: bool = False) -> bool:
    q, t = _canon(quote), _canon(page_text)
    if not q:
        return False
    if q in t:
        return True
    if ocr:
        # OCR splits labels and values into separate blocks, so the model bridges them
        # with an ellipsis ("Celkem: ... 12 077,88"); every part must be on the page.
        parts = [p.strip() for p in re.split(r"\.{3}|…", q) if p.strip()]
        return bool(parts) and all(p in t or _window_ratio(p, t, threshold) for p in parts)
    # PDF text layers often differ from the rendered text (hyphenation, spacing).
    m = SequenceMatcher(None, t, q, autojunk=False).find_longest_match(0, len(t), 0, len(q))
    return m.size / len(q) >= threshold


def _window_ratio(q: str, t: str, threshold: float) -> bool:
    """True if some quote-sized window of the text is similar enough to the quote. OCR
    misreads single characters anywhere ("3 000 00O Kč"), which breaks the longest common
    substring but costs only a character or two of similarity."""
    sm = SequenceMatcher(None, autojunk=False)
    sm.set_seq2(q)  # the quote is analysed once; windows are cheap to swap in
    for i in range(max(1, len(t) - len(q) + 1)):
        sm.set_seq1(t[i : i + len(q)])
        if sm.real_quick_ratio() >= threshold and sm.ratio() >= threshold:
            return True
    return False


def locate_quote(
    quote: str,
    pages: list[str],
    page: int | None,
    ocr_pages: Collection[int] = (),
    threshold: float = 0.9,
    ocr_threshold: float = 0.85,
) -> int | None:
    """Return the 1-based page that really contains the quote, preferring the cited one.
    OCR pages are matched more loosely: the model may copy a misread character or fix it."""
    order = list(range(1, len(pages) + 1))
    if page in order:
        order.remove(page)
        order.insert(0, page)
    return next(
        (
            p
            for p in order
            if quote_in(
                quote,
                pages[p - 1],
                ocr_threshold if p in ocr_pages else threshold,
                ocr=p in ocr_pages,
            )
        ),
        None,
    )


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
