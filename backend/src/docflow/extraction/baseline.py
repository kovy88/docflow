"""Rule-based baseline extractor.

Every LLM system needs a non-LLM control, otherwise "96% field accuracy" is a
number with nothing to compare it to. The question this answers is the one a
sceptical buyer actually asks: *does the AI earn its cost over a competent
deterministic parser?*

This is a genuine attempt, not a strawman. Building a deliberately weak baseline to
make the LLM look good is the same dishonesty as inventing the metric. The approach
here — label-anchored extraction with locale-aware value parsing — is what a decent
engineer would build in a week without an LLM, and on clean templated documents it
does well.

Its structural weakness is real and worth stating plainly: it can only find values
that sit next to a label it was told about. Unlabelled values, table layouts,
multi-column pages, unfamiliar phrasing and OCR noise defeat it. That is precisely
the gap the model is being paid to close, and the evaluation measures the size of
that gap rather than asserting it.

The baseline also serves two production purposes beyond evaluation:

* it powers the `fixture` provider, so the whole pipeline runs and demos without an
  API key;
* its agreement with the model output is a confidence signal (see
  `docflow.domain.confidence`) — two independent methods agreeing on a bank account
  is meaningfully stronger evidence than either alone.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from docflow.schemas.fields import (
    normalize_currency,
    normalize_iban,
    normalize_whitespace,
    parse_date,
    parse_decimal,
)
from docflow.validation.paths import set_path

# --------------------------------------------------------------------- primitives

# 2 letters + 2 check digits + 11..30 alphanumerics, optionally space-grouped.
# An earlier form required a mandatory 1-3 character trailing group, which silently
# failed on every 24-character IBAN (CZ, SK, RO, ES, SE) because those divide
# exactly into 4-character groups with nothing left over.
IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30})\b")
CZ_ACCOUNT_RE = re.compile(r"\b((?:\d{1,6}-)?\d{2,10}/\d{4})\b")
SWIFT_RE = re.compile(r"\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")
VAT_RE = re.compile(
    r"\b((?:CZ|SK|PL|DE|AT|HU|GB|FR|IT|ES|NL|BE|SE|DK|FI|IE|PT|RO|BG|SI|HR|LT|LV|EE|LU|MT|CY|GR)\s?[0-9A-Z]{8,12})\b"
)
ICO_RE = re.compile(r"\b(\d{8})\b")
AMOUNT_RE = re.compile(r"(-?\(?[\d][\d  .,']{0,15}[\d](?:[.,]\d{1,2})?\)?)")
CURRENCY_TOKEN_RE = re.compile(
    r"(?:^|[\s(])(CZK|EUR|USD|GBP|PLN|HUF|CHF|SEK|NOK|DKK|RON|BGN|CAD|AUD|JPY|Kč|KČ|€|\$|£|zł)(?:[\s).,]|$)",
    re.IGNORECASE,
)

# Values that follow a label on the same line, or on the next line if the label
# line has nothing after it. Both layouts are common in real documents.
_MAX_LOOKAHEAD_LINES = 2


@functools.lru_cache(maxsize=8192)
def _fold_char(char: str) -> str:
    """Map one character to its unaccented base, preserving length.

    Length preservation is the whole point. The usual idiom — NFKD-normalise the
    whole string and drop combining marks — shortens it, which would make match
    offsets computed on the folded text wrong when applied back to the original.
    Folding per character and keeping only the first component of each
    decomposition gives a string that is positionally identical to the input, so
    `line[match.end():]` slices the original text correctly.
    """
    decomposed = unicodedata.normalize("NFKD", char)
    if not decomposed:
        return char
    base = decomposed[0]
    return char if unicodedata.combining(base) else base


def fold_accents(text: str) -> str:
    """`Datum vystavení` -> `Datum vystaveni`, character-for-character."""
    return "".join(_fold_char(c) for c in text)


@dataclass(frozen=True, slots=True)
class FieldRule:
    """How to find one field by the labels that precede it."""

    path: str
    labels: tuple[str, ...]
    kind: str  # text | amount | date | currency | iban | account | vat | ico | digits
    # Labels that must NOT appear on the matched line. Disambiguates the classic
    # invoice trap where "Due date" and "Date of issue" both match a bare "date".
    exclude: tuple[str, ...] = ()
    # Prefer the last match rather than the first. Totals usually appear at the
    # bottom, after per-line subtotals that would otherwise win.
    prefer_last: bool = False
    max_chars: int = 200


@dataclass
class BaselineResult:
    data: dict[str, Any] = field(default_factory=dict)
    matched_paths: set[str] = field(default_factory=set)
    # Verbatim source line for each match, used as evidence in the UI and as the
    # grounding signal when the baseline is used as a cross-check.
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def field_count(self) -> int:
        return len(self.matched_paths)


class LabelExtractor:
    """Label-anchored extraction over plain text.

    The algorithm is intentionally simple and inspectable:
      1. split into lines, keeping order;
      2. for each rule, scan lines for a label match;
      3. take the text after the label on that line, or the next non-empty line;
      4. parse it according to the field kind.

    Every step is deterministic, which is the point: when the baseline is wrong you
    can see exactly why, and its behaviour never changes between runs.
    """

    def __init__(self, rules: tuple[FieldRule, ...], *, day_first: bool = True) -> None:
        self._rules = rules
        self._day_first = day_first
        # Labels are matched against an accent-folded copy of each line. OCR output
        # and many PDF text layers lose diacritics, so a rule written as
        # "datum vystavení" must still match a line reading "Datum vystaveni" —
        # otherwise the baseline scores near zero on exactly the scanned documents
        # it is meant to be compared on.
        self._compiled = {
            rule.path: re.compile(
                r"(?i)(?:^|\b)(?:"
                + "|".join(re.escape(fold_accents(lbl)) for lbl in rule.labels)
                + r")\s*[:：#]?\s*"
            )
            for rule in rules
        }

    def extract(self, text: str) -> BaselineResult:
        lines = [ln.rstrip() for ln in text.splitlines()]
        result = BaselineResult()

        for rule in self._rules:
            match = self._find(lines, rule)
            if match is None:
                continue
            raw, evidence_line = match
            value = self._parse(raw, rule.kind)
            if value is None:
                continue
            try:
                set_path(result.data, rule.path, value)
            except KeyError:
                continue
            result.matched_paths.add(rule.path)
            result.evidence[rule.path] = evidence_line[:300]

        return result

    # ------------------------------------------------------------------ internals

    def _find(self, lines: list[str], rule: FieldRule) -> tuple[str, str] | None:
        pattern = self._compiled[rule.path]
        candidates: list[tuple[str, str]] = []

        for index, line in enumerate(lines):
            if not line.strip():
                continue
            # Match against the folded copy but slice the *original* line, so the
            # extracted value keeps its diacritics. `fold_accents` is length-
            # preserving, which is what makes the offsets transferable.
            folded = fold_accents(line)
            lowered = folded.lower()
            if any(fold_accents(bad).lower() in lowered for bad in rule.exclude):
                continue
            found = pattern.search(folded)
            if not found:
                continue

            tail = line[found.end() :].strip()
            if tail:
                candidates.append((tail[: rule.max_chars], line))
                continue

            # Label alone on its line — value is very likely below it.
            for offset in range(1, _MAX_LOOKAHEAD_LINES + 1):
                if index + offset >= len(lines):
                    break
                nxt = lines[index + offset].strip()
                if nxt:
                    candidates.append((nxt[: rule.max_chars], f"{line} / {nxt}"))
                    break

        if not candidates:
            return None
        return candidates[-1] if rule.prefer_last else candidates[0]

    def _parse(self, raw: str, kind: str) -> Any:  # noqa: PLR0911, PLR0912 — dispatch over field kinds; a table of one-liners reads worse than the chain
        raw = raw.strip()
        if not raw:
            return None

        if kind == "text":
            return normalize_whitespace(raw)

        if kind == "amount":
            return self._parse_amount(raw)

        if kind == "date":
            parsed = parse_date(raw, day_first=self._day_first)
            return parsed.isoformat() if parsed else None

        if kind == "currency":
            if m := CURRENCY_TOKEN_RE.search(raw):
                return normalize_currency(m.group(1))
            return normalize_currency(raw)

        if kind == "iban":
            if m := IBAN_RE.search(raw.upper()):
                return normalize_iban(m.group(1))
            return None

        if kind == "account":
            if m := CZ_ACCOUNT_RE.search(raw):
                return m.group(1)
            return None

        if kind == "vat":
            if m := VAT_RE.search(raw.upper().replace(" ", "")):
                return m.group(1)
            return None

        if kind == "ico":
            if m := ICO_RE.search(raw):
                return m.group(1)
            return None

        if kind == "digits":
            digits = re.sub(r"\D", "", raw)
            return digits or None

        return normalize_whitespace(raw)

    @staticmethod
    def _parse_amount(raw: str) -> str | None:
        """Pick the monetary figure out of the text following a label.

        Two rules, both learned from real documents:

        * **Skip percentages.** `DPH 21%: 6 930,00` contains two numbers, and the
          rate is not the amount. Any figure immediately followed by `%` is a rate.
        * **Take the last remaining figure.** On a labelled line the value is
          conventionally last: `Total (incl. 21% VAT) ......... 39 930,00`.

        Czech/European documents commonly group thousands with a non-breaking
        space (`\xa0`) rather than an ASCII one — `AMOUNT_RE`'s character class
        only recognises the latter, so an ungrounded `\xa0` splits `78\xa0287,00`
        into two matches (`78`, `287,00`) and "take the last" silently keeps only
        the tail. Folding all Unicode whitespace to ASCII space before matching
        keeps a grouped number as a single candidate.
        """
        raw = normalize_whitespace(raw) or ""
        candidates: list[str] = []
        for match in AMOUNT_RE.finditer(raw):
            trailing = raw[match.end() :].lstrip()
            if trailing.startswith("%"):
                continue
            candidates.append(match.group(1))
        if not candidates:
            return None
        try:
            value = parse_decimal(candidates[-1])
        except Exception:
            return None
        return str(value) if isinstance(value, Decimal) else None


# ------------------------------------------------------------------- rule catalogue

INVOICE_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "invoice_number",
        (
            "invoice number",
            "invoice no",
            "invoice #",
            "invoice",
            "faktura číslo",
            "faktura č",
            "číslo faktury",
            "daňový doklad č",
            "rechnungsnummer",
        ),
        "text",
        exclude=("purchase order", "objednávk"),
        max_chars=40,
    ),
    FieldRule(
        "issue_date",
        (
            "date of issue",
            "issue date",
            "invoice date",
            "datum vystavení",
            "vystaveno",
            "rechnungsdatum",
        ),
        "date",
        exclude=("due", "splatnost"),
    ),
    FieldRule(
        "due_date",
        ("due date", "payment due", "date due", "datum splatnosti", "splatnost", "fällig"),
        "date",
    ),
    FieldRule(
        "tax_point_date",
        ("tax point date", "date of taxable supply", "duzp", "datum zdanitelného plnění"),
        "date",
    ),
    FieldRule(
        "subtotal",
        (
            "subtotal",
            "net amount",
            "total excl",
            "total net",
            "základ daně",
            "cena bez dph",
            "celkem bez dph",
            "nettobetrag",
        ),
        "amount",
        prefer_last=True,
    ),
    FieldRule(
        "tax_amount",
        # "dph" / "vat" alone are included because most Czech and UK invoices
        # label the tax line with just the rate ("DPH 21%"). The percentage itself
        # is skipped by `_parse_amount`; the excludes keep this rule off the net
        # and gross lines, which also mention the tax.
        (
            "vat amount",
            "tax amount",
            "total vat",
            "total tax",
            "dph celkem",
            "daň celkem",
            "výše dph",
            "mwst",
            "dph",
            "vat",
        ),
        "amount",
        exclude=("základ", "zaklad", "bez dph", "celkem s dph", "incl", "excl", "net"),
        prefer_last=True,
    ),
    FieldRule(
        "total",
        (
            "total amount due",
            "amount due",
            "total due",
            "grand total",
            "total incl",
            "total",
            "k úhradě",
            "celkem k úhradě",
            "celkem s dph",
            "celkem",
            "gesamtbetrag",
        ),
        "amount",
        exclude=("subtotal", "bez dph", "excl", "net amount"),
        prefer_last=True,
    ),
    FieldRule(
        "currency",
        ("currency", "měna", "währung"),
        "currency",
    ),
    FieldRule(
        "variable_symbol",
        ("variable symbol", "variabilní symbol", "var. symbol", "vs"),
        "digits",
        max_chars=20,
    ),
    FieldRule(
        "bank_details.iban",
        ("iban",),
        "iban",
    ),
    FieldRule(
        "bank_details.account_number",
        ("account number", "bank account", "číslo účtu", "účet", "bankovní spojení"),
        "account",
    ),
    FieldRule(
        "bank_details.swift_bic",
        ("swift", "bic", "swift/bic"),
        "text",
        max_chars=20,
    ),
    FieldRule(
        "supplier.vat_id",
        ("vat id", "vat number", "dič", "vat reg", "ust-idnr"),
        "vat",
    ),
    FieldRule(
        "supplier.registration_id",
        ("company id", "ičo", "ico", "reg. no", "registration number"),
        "ico",
    ),
    FieldRule(
        "purchase_order_number",
        ("purchase order", "po number", "objednávka č", "order reference"),
        "text",
        max_chars=40,
    ),
    FieldRule(
        "payment_terms",
        ("payment terms", "terms of payment", "platební podmínky"),
        "text",
        max_chars=80,
    ),
)

PURCHASE_ORDER_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "po_number",
        (
            "purchase order number",
            "po number",
            "po no",
            "purchase order",
            "order number",
            "číslo objednávky",
            "objednávka č",
        ),
        "text",
        max_chars=40,
    ),
    FieldRule("order_date", ("order date", "date of order", "datum objednávky"), "date"),
    FieldRule(
        "requested_delivery_date",
        ("requested delivery", "delivery date", "deliver by", "termín dodání"),
        "date",
    ),
    FieldRule("subtotal", ("subtotal", "net total", "total excl"), "amount", prefer_last=True),
    FieldRule("tax_amount", ("vat", "tax amount", "dph"), "amount", prefer_last=True),
    FieldRule(
        "total",
        ("order total", "total amount", "grand total", "total", "celkem"),
        "amount",
        exclude=("subtotal", "excl"),
        prefer_last=True,
    ),
    FieldRule("currency", ("currency", "měna"), "currency"),
    FieldRule(
        "shipping_terms", ("incoterms", "shipping terms", "delivery terms"), "text", max_chars=60
    ),
    FieldRule("payment_terms", ("payment terms", "terms of payment"), "text", max_chars=80),
    FieldRule(
        "delivery_address", ("ship to", "deliver to", "delivery address"), "text", max_chars=200
    ),
    FieldRule("requester", ("requested by", "requester", "ordered by"), "text", max_chars=80),
    FieldRule(
        "cost_center", ("cost centre", "cost center", "nákladové středisko"), "text", max_chars=40
    ),
    FieldRule("buyer.registration_id", ("company id", "ičo", "ico"), "ico"),
)

RECEIPT_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "receipt_number",
        ("receipt no", "receipt number", "doklad č", "účtenka č"),
        "text",
        max_chars=40,
    ),
    FieldRule("purchase_date", ("date", "datum"), "date", exclude=("due", "splatnost")),
    FieldRule("purchase_time", ("time", "čas"), "text", max_chars=12),
    FieldRule("subtotal", ("subtotal", "net", "bez dph"), "amount", prefer_last=True),
    FieldRule("tax_amount", ("vat", "tax", "dph"), "amount", prefer_last=True),
    FieldRule(
        "total",
        ("total", "amount", "celkem", "k úhradě", "suma"),
        "amount",
        exclude=("subtotal", "bez dph"),
        prefer_last=True,
    ),
    FieldRule("currency", ("currency", "měna"), "currency"),
    FieldRule("merchant_vat_id", ("vat id", "dič", "vat number"), "vat"),
)

CONTRACT_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "title", ("agreement title", "title of agreement", "název smlouvy"), "text", max_chars=120
    ),
    FieldRule(
        "effective_date",
        ("effective date", "commencement date", "start date", "datum účinnosti", "účinnost od"),
        "date",
    ),
    FieldRule(
        "expiration_date",
        ("expiration date", "expiry date", "end date", "termination date", "datum ukončení"),
        "date",
    ),
    FieldRule("signature_date", ("signed on", "date of signature", "datum podpisu"), "date"),
    FieldRule("term_months", ("term", "initial term", "doba trvání"), "digits", max_chars=30),
    FieldRule(
        "notice_period_days",
        ("notice period", "výpovědní lhůta", "notice of termination"),
        "digits",
        max_chars=30,
    ),
    FieldRule("governing_law", ("governing law", "rozhodné právo"), "text", max_chars=80),
    FieldRule("total_value", ("contract value", "total value", "hodnota smlouvy"), "amount"),
    FieldRule("currency", ("currency", "měna"), "currency"),
    FieldRule("payment_terms", ("payment terms", "platební podmínky"), "text", max_chars=100),
)

GENERIC_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "reference_number",
        ("reference", "ref no", "document number", "číslo dokladu"),
        "text",
        max_chars=40,
    ),
    FieldRule("document_date", ("date", "datum"), "date"),
    FieldRule("total_amount", ("total", "amount", "celkem"), "amount", prefer_last=True),
    FieldRule("currency", ("currency", "měna"), "currency"),
)

RULES_BY_TYPE: dict[str, tuple[FieldRule, ...]] = {
    "invoice": INVOICE_RULES,
    "purchase_order": PURCHASE_ORDER_RULES,
    "receipt": RECEIPT_RULES,
    "contract": CONTRACT_RULES,
    "generic": GENERIC_RULES,
}


# ------------------------------------------------------------------- entry points


def extract_baseline(text: str, document_type: str, *, day_first: bool = True) -> BaselineResult:
    """Run the baseline extractor for a document type."""
    rules = RULES_BY_TYPE.get(document_type, GENERIC_RULES)
    result = LabelExtractor(rules, day_first=day_first).extract(text)
    _post_process(result, text, document_type)
    return result


def _post_process(result: BaselineResult, text: str, document_type: str) -> None:
    """Fill in what label-anchoring structurally cannot reach.

    Two additions worth their complexity:

    * **Free-standing identifiers.** IBANs, Czech account numbers and VAT IDs have
      distinctive shapes and are frequently printed with no label at all. A
      document-wide regex sweep finds them without needing to guess the label.
    * **Currency inference.** A symbol anywhere in the document is decent evidence
      when no explicit "Currency:" label exists — which is most of the time.
    """
    upper = text.upper()

    if document_type in {"invoice", "purchase_order"}:
        if "bank_details.iban" not in result.matched_paths and (m := IBAN_RE.search(upper)):
            _safe_set(result, "bank_details.iban", normalize_iban(m.group(1)), m.group(0))
        if "bank_details.account_number" not in result.matched_paths and (
            m := CZ_ACCOUNT_RE.search(text)
        ):
            _safe_set(result, "bank_details.account_number", m.group(1), m.group(0))

    if (
        "currency" not in result.matched_paths
        and (m := CURRENCY_TOKEN_RE.search(text))
        and (code := normalize_currency(m.group(1)))
    ):
        _safe_set(result, "currency", code, m.group(0).strip())

    # The supplier name is almost always the most prominent text at the top of the
    # page. Taking the first substantial line is crude but works on the majority of
    # templated documents, and being wrong here is visible rather than silent.
    if (
        document_type == "invoice"
        and "supplier.name" not in result.matched_paths
        and (name := _leading_entity_name(text))
    ):
        _safe_set(result, "supplier.name", name, name)
    if (
        document_type == "receipt"
        and "merchant_name" not in result.matched_paths
        and (name := _leading_entity_name(text))
    ):
        _safe_set(result, "merchant_name", name, name)


_SKIP_LEADING = re.compile(
    r"(?i)^(invoice|faktura|tax invoice|daňový doklad|receipt|účtenka|purchase order|"
    r"objednávka|page \d|strana \d|\d{1,3}$)"
)


def _leading_entity_name(text: str) -> str | None:
    for line in text.splitlines()[:12]:
        candidate = line.strip()
        if len(candidate) < 3 or len(candidate) > 80:
            continue
        if _SKIP_LEADING.match(candidate):
            continue
        if sum(c.isdigit() for c in candidate) > len(candidate) / 3:
            continue
        return normalize_whitespace(candidate)
    return None


def _safe_set(result: BaselineResult, path: str, value: Any, evidence: str) -> None:
    if value is None:
        return
    try:
        set_path(result.data, path, value)
    except KeyError:
        return
    result.matched_paths.add(path)
    result.evidence[path] = evidence[:300]
