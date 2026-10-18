import pytest

from app.checks import apply_rules, ico_valid, locate_quote, parse_date, parse_number, quote_in
from app.templates import load_templates


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12 100,00 Kč", 12100.0),
        ("12 100,50", 12100.5),
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("15 000 000", 15000000.0),
        ("2.100", 2.1),
        ("1.000.000", 1000000.0),
        ("EUR 99", 99.0),
        ("185 000,- Kč", 185000.0),
        ("4,235.00 EUR", 4235.0),
        ("3.000 000 Kč", 3000000.0),  # OCR read a thousands space as a dot
        ("12 077.88", 12077.88),
    ],
)
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_parse_number_rejects_text():
    with pytest.raises(ValueError):
        parse_number("neuvedeno")


@pytest.mark.parametrize(
    "raw",
    [
        "3. 9. 2026",
        "03.09.2026",
        "2026-09-03",
        "3/9/2026",
        "3. září 2026",
        "3. Září 2026",
        "3. 9. 2026, 0:00 hod.",
        "3. 9. 2026 24:00",
    ],
)
def test_parse_date(raw):
    assert parse_date(raw).isoformat() == "2026-09-03"


def test_ico_checksum():
    assert ico_valid("25596641")
    assert ico_valid("2559 6641")
    assert not ico_valid("25596642")
    assert not ico_valid("abc")


def test_quote_matching_tolerates_whitespace_and_case():
    page = "Celkem   k úhradě:\n12 100,00 Kč"
    assert quote_in("celkem k úhradě: 12 100,00", page)
    assert not quote_in("Celkem 99 999 Kč", page)


def test_locate_quote_finds_correct_page():
    pages = ["první strana", "Číslo faktury 2026-0142"]
    assert locate_quote("faktury 2026-0142", pages, page=1) == 2
    assert locate_quote("nic takového", pages, page=1) is None


def test_locate_quote_is_looser_on_ocr_pages():
    pages = ["Celkem k úhradě: 3 000 00 Kč"]  # OCR dropped a zero
    assert locate_quote("3 000 000 Kč", pages, page=1) is None
    assert locate_quote("3 000 000 Kč", pages, page=1, ocr_pages=[1]) == 1
    # OCR separates label and value; the model bridges them with an ellipsis
    pages = ["Net / Základ daně:\nVAT / DPH celkem:\n\nAmount\n698.65 EUR"]
    assert locate_quote("Net / Základ daně: ... 698.65", pages, 1, ocr_pages=[1]) == 1
    assert locate_quote("Net / Základ daně: ... 546.32", pages, 1, ocr_pages=[1]) is None


def test_invoice_rules():
    template = load_templates()["invoice"]
    values = {
        "supplier_ico": "25596642",
        "net_amount": 10000.0,
        "vat_amount": 2100.0,
        "total_amount": 12010.0,
        "issue_date": "2026-09-20",
        "due_date": "2026-09-06",
    }
    codes = {i["code"] for i in apply_rules(template, values)}
    assert codes == {"ico_checksum", "sum_mismatch", "date_order"}


def test_rules_skip_missing_values():
    template = load_templates()["invoice"]
    assert apply_rules(template, {"net_amount": 1.0}) == []
