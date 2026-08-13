"""Reusable field types, parsers and checksum validators.

These are the deterministic core of the product. An LLM is good at *finding* the
invoice total on a page; it is unnecessary and unreliable for *deciding* whether
`CZ6508000000192000145399` is a well-formed IBAN. Every fact that can be checked with
arithmetic is checked with arithmetic, and the model's answer is treated as a
candidate rather than as truth.

Parsers here are deliberately locale-tolerant. The target market is Czech/Central
European SMBs, where a single accounting inbox routinely contains `1 234,56 Kč`,
`€1.234,56` and `$1,234.56` — all meaning different numbers under different
conventions. Getting this wrong is a silent, expensive class of bug that no amount of
prompt engineering fixes.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

# --------------------------------------------------------------------------- money

_CURRENCY_CHARS = re.compile(r"[^\d,.\-+  ']")
_SPACES = re.compile(r"[\s ']")


class MoneyParseError(ValueError):
    pass


def parse_decimal(value: Any) -> Decimal | None:  # noqa: PLR0912 — separator disambiguation is inherently branchy
    """Parse a monetary/numeric value from messy real-world text.

    Handles, in order of appearance in the wild:
      "1234.56" · "1,234.56" · "1 234,56" · "1.234,56" · "1234,56" · "€ 1.234,56"
      "(1 234,56)" (accounting negative) · "1 234,56 Kč" · "-1234.56"

    The ambiguous case is a single separator with exactly three following digits:
    `1,234` could be one thousand two hundred thirty-four (US) or 1.234 (EU). We
    resolve it as a **thousands separator**, because a bare `1,234` in a business
    document is overwhelmingly more likely to be 1234 than 1.234. Documents that
    need the other reading will disagree with the arithmetic cross-check
    (`total == subtotal + tax`) and be routed to review rather than silently accepted.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]

    text = _CURRENCY_CHARS.sub("", text)
    text = _SPACES.sub("", text)
    if not text:
        return None

    if text.startswith("-"):
        negative, text = True, text[1:]
    elif text.startswith("+"):
        text = text[1:]

    last_dot, last_comma = text.rfind("."), text.rfind(",")

    if last_dot >= 0 and last_comma >= 0:
        # Both present: the rightmost one is the decimal separator.
        if last_comma > last_dot:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif last_comma >= 0:
        tail = len(text) - last_comma - 1
        text = text.replace(",", "") if tail == 3 else text.replace(",", ".")
    elif last_dot >= 0:
        tail = len(text) - last_dot - 1
        # "1.234" -> thousands; "1.23" / "1.2345" -> decimal.
        if tail == 3 and text.count(".") >= 1 and len(text.replace(".", "")) > 3:
            text = text.replace(".", "")

    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyParseError(f"could not parse number from {value!r}") from exc
    return -result if negative else result


def _money_validator(value: Any) -> Any:
    if value is None or isinstance(value, Decimal):
        return value
    try:
        return parse_decimal(value)
    except MoneyParseError:
        # Let Pydantic produce the type error so it surfaces as a schema
        # validation issue with a field path, rather than an opaque exception.
        return value


Money = Annotated[
    Decimal,
    BeforeValidator(_money_validator),
    # Serialise as a string: JSON floats cannot represent 0.1 exactly, and money
    # that silently drifts by 1e-17 is a support ticket waiting to happen.
    PlainSerializer(lambda v: str(v) if v is not None else None, return_type=str),
]

# ---------------------------------------------------------------------------- date

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_DMY = re.compile(r"^(\d{1,2})[./\- ](\d{1,2})[./\- ](\d{2,4})")
_YMD_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    # Czech
    "led": 1,
    "uno": 2,
    "bre": 3,
    "dub": 4,
    "kve": 5,
    "cvn": 6,
    "cvc": 7,
    "srp": 8,
    "zar": 9,
    "rij": 10,
    "lis": 11,
    "pro": 12,
}
_TEXT_DATE = re.compile(r"(\d{1,2})[.\s-]+([a-zA-Zěščřžýáíéúůň]{3,12})[.\s,-]+(\d{4})")


class DateParseResult:
    __slots__ = ("ambiguous", "value", "was_fuzzy")

    def __init__(self, value: dt.date | None, *, was_fuzzy: bool, ambiguous: bool) -> None:
        self.value = value
        self.was_fuzzy = was_fuzzy
        self.ambiguous = ambiguous


def parse_date_detailed(  # noqa: PLR0911 — one return per recognised date format
    value: Any, *, day_first: bool = True
) -> DateParseResult:
    """Parse a date and report *how hard it was*.

    The `was_fuzzy` / `ambiguous` flags feed the format-cleanliness confidence
    signal: a date recovered from `03/04/2024` (which is either 3 April or 4 March
    depending on the writer's continent) should not be presented as certain.

    `day_first=True` matches the European convention, which is correct for the
    initial target market. It is a per-document-type setting, not a global constant.
    """
    if value is None:
        return DateParseResult(None, was_fuzzy=False, ambiguous=False)
    if isinstance(value, dt.datetime):
        return DateParseResult(value.date(), was_fuzzy=False, ambiguous=False)
    if isinstance(value, dt.date):
        return DateParseResult(value, was_fuzzy=False, ambiguous=False)

    text = str(value).strip()
    if not text:
        return DateParseResult(None, was_fuzzy=False, ambiguous=False)

    if m := _ISO.match(text):
        y, mo, d = (int(g) for g in m.groups())
        return DateParseResult(_safe_date(y, mo, d), was_fuzzy=False, ambiguous=False)

    if m := _YMD_COMPACT.match(text):
        y, mo, d = (int(g) for g in m.groups())
        return DateParseResult(_safe_date(y, mo, d), was_fuzzy=True, ambiguous=False)

    if m := _DMY.match(text):
        a, b, y = (int(g) for g in m.groups())
        if y < 100:
            y += 2000 if y < 70 else 1900
        # If one component is > 12 the order is unambiguous regardless of locale.
        if a > 12:
            return DateParseResult(_safe_date(y, b, a), was_fuzzy=True, ambiguous=False)
        if b > 12:
            return DateParseResult(_safe_date(y, a, b), was_fuzzy=True, ambiguous=False)
        day, month = (a, b) if day_first else (b, a)
        return DateParseResult(_safe_date(y, month, day), was_fuzzy=True, ambiguous=True)

    if m := _TEXT_DATE.search(text):
        d_s, mon_s, y_s = m.groups()
        key = _fold_ascii(mon_s)[:3]
        month_or_none = _MONTHS.get(key)
        if month_or_none is not None:
            month = month_or_none
            return DateParseResult(
                _safe_date(int(y_s), month, int(d_s)), was_fuzzy=True, ambiguous=False
            )

    return DateParseResult(None, was_fuzzy=True, ambiguous=False)


def _fold_ascii(text: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def parse_date(value: Any, *, day_first: bool = True) -> dt.date | None:
    return parse_date_detailed(value, day_first=day_first).value


def _date_validator(value: Any) -> Any:
    if value is None or isinstance(value, dt.date):
        return value
    parsed = parse_date(value)
    return parsed if parsed is not None else value


FlexibleDate = Annotated[dt.date, BeforeValidator(_date_validator)]

# ------------------------------------------------------------------------ currency

# ISO 4217 subset covering the target market plus the majors. Restricting the set is
# deliberate: an "extracted" currency of "US$" or "Euro" should fail loudly rather
# than propagate into an accounting system.
SUPPORTED_CURRENCIES = frozenset(
    {
        "CZK",
        "EUR",
        "USD",
        "GBP",
        "PLN",
        "HUF",
        "CHF",
        "SEK",
        "NOK",
        "DKK",
        "RON",
        "BGN",
        "HRK",
        "CAD",
        "AUD",
        "JPY",
    }
)

_CURRENCY_ALIASES = {
    "KČ": "CZK",
    "KC": "CZK",
    "CZ": "CZK",
    "CZK": "CZK",
    "KORUN": "CZK",
    "KČS": "CZK",
    "€": "EUR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "EUR": "EUR",
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "£": "GBP",
    "GBP": "GBP",
    "POUND": "GBP",
    "ZŁ": "PLN",
    "ZL": "PLN",
    "PLN": "PLN",
    "FT": "HUF",
    "HUF": "HUF",
}


def normalize_currency(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().upper()
    if not raw:
        return None
    if raw in SUPPORTED_CURRENCIES:
        return raw
    return _CURRENCY_ALIASES.get(raw)


def _currency_validator(value: Any) -> Any:
    if value is None:
        return None
    return normalize_currency(value) or value


CurrencyCode = Annotated[str, BeforeValidator(_currency_validator)]

# ---------------------------------------------------------------------------- IBAN

_IBAN_LENGTHS = {
    "AD": 24,
    "AT": 20,
    "BE": 16,
    "BG": 22,
    "CH": 21,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "EE": 20,
    "ES": 24,
    "FI": 18,
    "FR": 27,
    "GB": 22,
    "GR": 27,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IS": 26,
    "IT": 27,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MC": 27,
    "MT": 31,
    "NL": 18,
    "NO": 15,
    "PL": 28,
    "PT": 25,
    "RO": 24,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
}


def normalize_iban(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"[\s\-]", "", str(value)).upper() or None


def is_valid_iban(value: Any) -> bool:
    """ISO 13616 / ISO 7064 MOD-97-10 check.

    This catches transposed digits in a bank account — the single most damaging
    extraction error the product can make, because it sends money to the wrong place
    and nobody notices until reconciliation.
    """
    iban = normalize_iban(value)
    if not iban or len(iban) < 15 or len(iban) > 34:
        return False
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]+", iban):
        return False
    expected = _IBAN_LENGTHS.get(iban[:2])
    if expected is not None and len(iban) != expected:
        return False

    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


# ------------------------------------------------------- Czech business identifiers


def normalize_ico(value: Any) -> str | None:
    """Czech company registration number (IČO): 8 digits, zero-padded."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    return digits.zfill(8) if len(digits) <= 8 else digits


def is_valid_ico(value: Any) -> bool:
    """IČO weighted-modulus checksum (weights 8..2, mod 11).

    A deterministic check that an LLM cannot do reliably and should not be asked to.
    """
    ico = normalize_ico(value)
    if not ico or len(ico) != 8 or not ico.isdigit():
        return False
    weights = (8, 7, 6, 5, 4, 3, 2)
    total = sum(int(d) * w for d, w in zip(ico[:7], weights, strict=True))
    remainder = total % 11
    if remainder == 0:
        check = 1
    elif remainder == 1:
        check = 0
    else:
        check = 11 - remainder
    return check == int(ico[7])


_VAT_PATTERNS = {
    "CZ": re.compile(r"^CZ\d{8,10}$"),
    "SK": re.compile(r"^SK\d{10}$"),
    "PL": re.compile(r"^PL\d{10}$"),
    "DE": re.compile(r"^DE\d{9}$"),
    "AT": re.compile(r"^ATU\d{8}$"),
    "HU": re.compile(r"^HU\d{8}$"),
}


def normalize_vat_id(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[\s\-./]", "", str(value)).upper()
    return cleaned or None


def is_plausible_vat_id(value: Any) -> bool:
    """Format-level plausibility only.

    Real validation requires the EU VIES service. That is a network call with its own
    availability and rate limits, so it belongs in an enrichment step rather than in
    the synchronous validation path — see `docs/DECISIONS.md`.
    """
    vat = normalize_vat_id(value)
    if not vat or len(vat) < 8:
        return False
    prefix = vat[:2]
    if pattern := _VAT_PATTERNS.get(prefix):
        return bool(pattern.match(vat))
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{6,14}", vat))


# --------------------------------------------------- Czech domestic account numbers


def is_valid_czech_account_number(value: Any) -> bool:
    """Czech domestic account `[prefix-]number/bankcode`, ČNB weighted mod-11 check."""
    if value is None:
        return False
    text = re.sub(r"\s", "", str(value))
    m = re.fullmatch(r"(?:(\d{1,6})-)?(\d{2,10})/(\d{4})", text)
    if not m:
        return False
    prefix, number, _bank = m.groups()

    def _mod11(part: str, weights: tuple[int, ...]) -> bool:
        padded = part.rjust(len(weights), "0")
        total = sum(int(d) * w for d, w in zip(padded, weights, strict=True))
        return total % 11 == 0

    prefix_weights = (10, 5, 8, 4, 2, 1)
    number_weights = (6, 3, 7, 9, 10, 5, 8, 4, 2, 1)
    if prefix and not _mod11(prefix, prefix_weights):
        return False
    return _mod11(number, number_weights)


def normalize_whitespace(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


CleanStr = Annotated[str, BeforeValidator(lambda v: normalize_whitespace(v) if v else v)]
