"""Evaluation corpus: synthetic documents with known ground truth.

## Why synthetic, and what that costs

Real customer invoices cannot be committed to a public repository, and a public
corpus of Czech/Central-European business documents with field-level labels does
not exist. So the corpus is generated: every document is rendered from a record we
already know, which makes the ground truth exact by construction rather than by
hand-labelling.

**The honest limitation:** synthetic documents are drawn from a distribution this
codebase authored, so they cannot surprise it the way real documents do. Layout
variety is bounded by the templates below, and the failure modes that dominate in
production — unusual vendor templates, multi-column tables, poor scans, handwriting
— are under-represented. Numbers measured here are therefore an **upper bound** on
real-world accuracy, and `docs/EVALUATION.md` says so wherever they appear.

What the corpus does support honestly:
* **Relative comparison.** Baseline vs model, prompt v1 vs v2, model A vs model B —
  all measured over identical inputs. This is what the harness is really for.
* **Regression detection.** A change that breaks date parsing shows up immediately.
* **Deliberate difficulty.** The generator injects the specific hazards that break
  extractors in practice (see `DIFFICULTY_FEATURES`), rather than only easy cases.
"""

from __future__ import annotations

import datetime as dt
import json
import random
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

# Hazards deliberately introduced, each mapped to the failure it provokes.
DIFFICULTY_FEATURES = {
    "decimal_comma": "European `1 234,56` instead of `1234.56`",
    "ambiguous_date": "`03/04/2024` — day/month order not determinable from the value",
    "no_currency_code": "currency only as a symbol (`Kč`, `€`)",
    "label_variants": "unusual or abbreviated field labels",
    "multi_page": "values split across pages",
    "extra_numbers": "phone numbers and postcodes that look like amounts",
    "no_labels": "values in a table with column headers only",
    "diacritics_stripped": "OCR-style loss of Czech diacritics",
    "rounding": "total rounded to whole currency units",
    "credit_note": "negative amounts",
    # Not injected by a generator below — added by `eval/scan_simulation.py` when a
    # document is routed through a real render -> degrade -> OCR pass instead of
    # being handed to the extractor as clean text.
    "ocr_scanned": "real OCR errors from a simulated scan, not clean synthetic text",
}


@dataclass
class GroundTruth:
    """One labelled document."""

    document_id: str
    document_type: str
    text: str
    fields: dict[str, Any]
    difficulty: list[str] = field(default_factory=list)
    language: str = "cs"
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


CZECH_COMPANIES = [
    ("ACME Solutions s.r.o.", "27074358", "CZ27074358", "Křižíkova 148/34, 186 00 Praha 8"),
    ("Beta Trading a.s.", "45274649", "CZ45274649", "Nádražní 12, 150 00 Praha 5"),
    ("Delta Systems s.r.o.", "26185610", "CZ26185610", "Veveří 456/9, 602 00 Brno"),
    ("Omega Consulting s.r.o.", "63078295", "CZ63078295", "Hlavní 88, 400 01 Ústí nad Labem"),
    ("Sigma Logistics a.s.", "25596641", "CZ25596641", "Přístavní 5, 702 00 Ostrava"),
]

UK_COMPANIES = [
    ("Northgate Supplies Ltd", "08123456", "GB812345678", "42 King Street, Manchester M2 6DN"),
    ("Brightwell Services Ltd", "09456123", "GB945612345", "17 Queen Road, Bristol BS1 4ND"),
]

LINE_ITEMS = [
    ("IT konzultace", "Consulting hours", "hod", Decimal("2500")),
    ("Licence software", "Software licence", "ks", Decimal("8000")),
    ("Správa serverů", "Server maintenance", "měsíc", Decimal("12000")),
    ("Školení", "Training day", "den", Decimal("15000")),
    ("Vývoj aplikace", "Application development", "hod", Decimal("1800")),
    ("Technická podpora", "Technical support", "hod", Decimal("1200")),
]

# Valid IBAN/account pairs — real checksums, so the validation layer exercises its
# actual algorithms rather than agreeing with a fabricated constant.
BANK_ACCOUNTS = [
    ("CZ6508000000192000145399", "19-2000145399/0800"),
    ("CZ9455000000001011038193", "1011038193/5500"),
    ("CZ2201000000430047834027", "43-0047834027/0100"),
]


def _money(value: Decimal, *, decimal_comma: bool, thousands: bool = True) -> str:
    text = f"{value:,.2f}"
    if not thousands:
        text = text.replace(",", "")
    if decimal_comma:
        # `1,234.56` -> `1 234,56`
        text = text.replace(",", " ").replace(".", ",")
    return text


def _date(value: dt.date, style: str) -> str:
    if style == "iso":
        return value.isoformat()
    if style == "cz":
        return f"{value.day}.{value.month}.{value.year}"
    if style == "cz_padded":
        return f"{value.day:02d}.{value.month:02d}.{value.year}"
    if style == "slash":
        return f"{value.day:02d}/{value.month:02d}/{value.year}"
    if style == "uk_text":
        return value.strftime("%d %B %Y")
    return value.isoformat()


def _strip_diacritics(text: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKD", text)
    return "".join(c for c in folded if not unicodedata.combining(c))


def generate_invoice(rng: random.Random, index: int) -> GroundTruth:
    difficulty: list[str] = []

    czech = rng.random() < 0.7
    supplier = rng.choice(CZECH_COMPANIES if czech else UK_COMPANIES)
    customer = rng.choice([c for c in CZECH_COMPANIES if c != supplier])

    decimal_comma = czech and rng.random() < 0.85
    if decimal_comma:
        difficulty.append("decimal_comma")

    date_style = rng.choice(
        ["cz_padded", "cz", "iso", "slash"] if czech else ["iso", "uk_text", "slash"]
    )
    if date_style == "slash":
        difficulty.append("ambiguous_date")

    issue = dt.date(2024, rng.randint(1, 12), rng.randint(1, 28))
    due = issue + dt.timedelta(days=rng.choice([14, 21, 30, 60]))

    rows = rng.sample(LINE_ITEMS, k=rng.randint(1, 4))
    items = []
    subtotal = Decimal("0")
    for cz_name, en_name, unit, price in rows:
        qty = Decimal(rng.randint(1, 20))
        line_total = qty * price
        subtotal += line_total
        items.append(
            {
                "description": cz_name if czech else en_name,
                "quantity": str(qty),
                "unit": unit,
                "unit_price": str(price),
                "line_total": str(line_total),
            }
        )

    vat_rate = Decimal("21") if czech else Decimal("20")
    tax = (subtotal * vat_rate / 100).quantize(Decimal("0.01"))
    total = subtotal + tax

    currency = "CZK" if czech else "GBP"
    show_code = rng.random() < 0.45
    if not show_code:
        difficulty.append("no_currency_code")
    symbol = {"CZK": "Kč", "GBP": "£"}[currency]

    iban, account = rng.choice(BANK_ACCOUNTS)
    invoice_number = f"{issue.year}-{index:04d}"
    variable_symbol = f"{issue.year}{index:04d}"

    labels = rng.choice(
        [
            {"no": "Faktura číslo", "issue": "Datum vystavení", "due": "Datum splatnosti"},
            {"no": "Faktura č.", "issue": "Vystaveno", "due": "Splatnost"},
            {"no": "Invoice number", "issue": "Date of issue", "due": "Due date"},
        ]
        if czech
        else [{"no": "Invoice No.", "issue": "Invoice Date", "due": "Payment Due"}]
    )
    if labels["no"] in ("Faktura č.", "Invoice No."):
        difficulty.append("label_variants")

    money = lambda v: _money(v, decimal_comma=decimal_comma)  # noqa: E731

    text = f"""{supplier[0]}
{supplier[3]}
IČO: {supplier[1]}    DIČ: {supplier[2]}

{"FAKTURA - DAŇOVÝ DOKLAD" if czech else "TAX INVOICE"}

{labels["no"]}: {invoice_number}
{labels["issue"]}: {_date(issue, date_style)}
{labels["due"]}: {_date(due, date_style)}
{"Variabilní symbol" if czech else "Reference"}: {variable_symbol}

{"Odběratel" if czech else "Bill to"}:
{customer[0]}
{customer[3]}
IČO: {customer[1]}

{"Popis":<32}{"Množství" if czech else "Qty":>10}{"MJ" if czech else "Unit":>8}{"Cena/ks" if czech else "Unit price":>14}{"Celkem" if czech else "Amount":>16}
{"-" * 80}
"""
    for item in items:
        text += (
            f"{item['description']:<32}{item['quantity']:>10}{item['unit']:>8}"
            f"{money(Decimal(item['unit_price'])):>14}{money(Decimal(item['line_total'])):>16}\n"
        )

    unit = f" {symbol}" if not show_code else f" {currency}"
    text += f"""
{"Základ daně" if czech else "Subtotal"}: {money(subtotal)}{unit}
{"DPH" if czech else "VAT"} {vat_rate}%: {money(tax)}{unit}
{"Celkem k úhradě" if czech else "Total due"}: {money(total)}{unit}

{"Bankovní spojení" if czech else "Bank account"}: {account}
IBAN: {iban}
"""

    # Decoy numbers that look like amounts. A naive "largest number on the page"
    # heuristic picks the phone number; this is why the corpus includes them. The
    # PO-shaped line is not decorative to a schema-driven extractor, though: it is
    # exactly what the real, declared `Invoice.purchase_order_number` field asks
    # for, and it must be recorded in ground truth whenever it is written — it was
    # silently absent from `fields` on all 75 invoices, decoy or not, which scored
    # every correct extraction of it as wrong. See
    # docs/EVALUATION_ERROR_ANALYSIS.md, Finding 4.
    purchase_order_number = None
    if rng.random() < 0.5:
        difficulty.append("extra_numbers")
        text += (
            f"\nTel: +420 {rng.randint(200, 799)} {rng.randint(100, 999)} {rng.randint(100, 999)}\n"
        )
        purchase_order_number = f"OBJ-{rng.randint(1000, 9999)}"
        text += f"{'Č. objednávky' if czech else 'PO'}: {purchase_order_number}\n"

    if rng.random() < 0.2:
        difficulty.append("diacritics_stripped")
        text = _strip_diacritics(text)

    return GroundTruth(
        document_id=f"invoice-{index:04d}",
        document_type="invoice",
        text=text,
        language="cs" if czech else "en",
        difficulty=difficulty,
        fields={
            "invoice_number": invoice_number,
            "supplier": {
                "name": supplier[0],
                "registration_id": supplier[1],
                "vat_id": supplier[2],
                "address": supplier[3],
            },
            "customer": {
                "name": customer[0],
                "registration_id": customer[1],
                "address": customer[3],
            },
            "issue_date": issue.isoformat(),
            "due_date": due.isoformat(),
            "currency": currency,
            "subtotal": str(subtotal),
            "tax_amount": str(tax),
            "tax_rate": str(vat_rate),
            "total": str(total),
            "variable_symbol": variable_symbol,
            "purchase_order_number": purchase_order_number,
            "bank_details": {"iban": iban, "account_number": account},
            "line_items": items,
        },
    )


def generate_purchase_order(rng: random.Random, index: int) -> GroundTruth:
    buyer = rng.choice(CZECH_COMPANIES)
    supplier = rng.choice([c for c in CZECH_COMPANIES if c != buyer])
    order_date = dt.date(2024, rng.randint(1, 12), rng.randint(1, 28))
    delivery = order_date + dt.timedelta(days=rng.choice([7, 14, 30]))

    rows = rng.sample(LINE_ITEMS, k=rng.randint(1, 3))
    items, subtotal = [], Decimal("0")
    for cz_name, _en, unit, price in rows:
        qty = Decimal(rng.randint(1, 10))
        line_total = qty * price
        subtotal += line_total
        items.append(
            {
                "description": cz_name,
                "quantity": str(qty),
                "unit": unit,
                "unit_price": str(price),
                "line_total": str(line_total),
            }
        )
    tax = (subtotal * Decimal("21") / 100).quantize(Decimal("0.01"))
    total = subtotal + tax
    po_number = f"OBJ-{order_date.year}-{index:04d}"
    money = lambda v: _money(v, decimal_comma=True)  # noqa: E731

    text = f"""{buyer[0]}
{buyer[3]}
IČO: {buyer[1]}

OBJEDNÁVKA

Číslo objednávky: {po_number}
Datum objednávky: {_date(order_date, "cz_padded")}
Termín dodání: {_date(delivery, "cz_padded")}

Dodavatel:
{supplier[0]}
{supplier[3]}

Místo dodání: {buyer[3]}
Incoterms: DAP

"""
    for item in items:
        text += (
            f"{item['description']:<32}{item['quantity']:>8} {item['unit']:<8}"
            f"{money(Decimal(item['unit_price'])):>12}{money(Decimal(item['line_total'])):>14}\n"
        )
    text += f"""
Základ daně: {money(subtotal)} CZK
DPH 21%: {money(tax)} CZK
Celkem: {money(total)} CZK

Platební podmínky: 30 dní od dodání
"""
    return GroundTruth(
        document_id=f"po-{index:04d}",
        document_type="purchase_order",
        text=text,
        difficulty=["decimal_comma"],
        fields={
            "po_number": po_number,
            "buyer": {"name": buyer[0], "registration_id": buyer[1], "address": buyer[3]},
            "supplier": {"name": supplier[0], "address": supplier[3]},
            "order_date": order_date.isoformat(),
            "requested_delivery_date": delivery.isoformat(),
            "currency": "CZK",
            "subtotal": str(subtotal),
            "tax_amount": str(tax),
            "total": str(total),
            "shipping_terms": "DAP",
            "line_items": items,
        },
    )


def generate_receipt(rng: random.Random, index: int) -> GroundTruth:
    merchants = [
        ("Kavárna Slavia", "CZ48136450"),
        ("Restaurace U Fleků", "CZ25776541"),
        ("Benzina ČEPRO", "CZ60193531"),
        ("Albert Supermarket", "CZ44012373"),
    ]
    merchant, vat_id = rng.choice(merchants)
    date = dt.date(2024, rng.randint(1, 12), rng.randint(1, 28))
    subtotal = Decimal(rng.randint(50, 2000))
    tax = (subtotal * Decimal("21") / 100).quantize(Decimal("0.01"))
    total = subtotal + tax
    money = lambda v: _money(v, decimal_comma=True, thousands=False)  # noqa: E731

    text = f"""{merchant}
DIČ: {vat_id}

ÚČTENKA č. {index:06d}

Datum: {_date(date, "cz_padded")}   Čas: {rng.randint(8, 21):02d}:{rng.randint(0, 59):02d}

Základ: {money(subtotal)}
DPH 21%: {money(tax)}
CELKEM: {money(total)} Kč

Platba kartou
Děkujeme za návštěvu
"""
    difficulty = ["decimal_comma"]
    # Receipts are the OCR-heavy class, so most of them lose their diacritics.
    if rng.random() < 0.6:
        difficulty.append("diacritics_stripped")
        text = _strip_diacritics(text)

    return GroundTruth(
        document_id=f"receipt-{index:04d}",
        document_type="receipt",
        text=text,
        difficulty=difficulty,
        fields={
            "merchant_name": merchant,
            "merchant_vat_id": vat_id,
            "receipt_number": f"{index:06d}",
            "purchase_date": date.isoformat(),
            "currency": "CZK",
            "subtotal": str(subtotal),
            "tax_amount": str(tax),
            "total": str(total),
            "payment_method": "card",
        },
    )


def generate_contract(rng: random.Random, index: int) -> GroundTruth:
    a = rng.choice(CZECH_COMPANIES)
    b = rng.choice([c for c in CZECH_COMPANIES if c != a])
    effective = dt.date(2024, rng.randint(1, 12), rng.randint(1, 28))
    term_months = rng.choice([12, 24, 36])
    expiry = dt.date(effective.year + term_months // 12, effective.month, max(1, effective.day - 1))
    notice = rng.choice([30, 60, 90])
    auto_renewal = rng.random() < 0.6
    value = Decimal(rng.randint(100, 2000)) * 1000

    text = f"""SERVICE AGREEMENT

This Agreement is entered into between:

{a[0]}, IČO {a[1]}, of {a[3]} ("the Supplier")

and

{b[0]}, IČO {b[1]}, of {b[3]} ("the Customer")

WHEREAS the Supplier wishes to provide services to the Customer;

1. TERM
Commencement date: {_date(effective, "cz_padded")}
This Agreement shall continue until {_date(expiry, "cz_padded")}, being an initial
term of {term_months} months.
{"This Agreement shall renew automatically for successive periods of equal length unless notice is given." if auto_renewal else "This Agreement shall expire at the end of the initial term and shall not renew."}

2. TERMINATION
Either party may terminate this Agreement by giving {notice} days written notice.

3. CHARGES
Total contract value: {_money(value, decimal_comma=True)} CZK
Payment terms: 30 days from invoice date.

4. LIABILITY
The Supplier's aggregate liability shall not exceed the total charges paid in the
twelve months preceding the claim.

5. CONFIDENTIALITY
Each party shall keep confidential all information disclosed by the other.

6. GOVERNING LAW
This Agreement is governed by the laws of the Czech Republic.

IN WITNESS WHEREOF the parties have executed this Agreement.
"""
    return GroundTruth(
        document_id=f"contract-{index:04d}",
        document_type="contract",
        text=text,
        language="en",
        difficulty=["label_variants"],
        fields={
            "title": "Service Agreement",
            "contract_type": "service_agreement",
            "parties": [{"name": a[0]}, {"name": b[0]}],
            "effective_date": effective.isoformat(),
            "expiration_date": expiry.isoformat(),
            "term_months": str(term_months),
            "auto_renewal": auto_renewal,
            "notice_period_days": str(notice),
            "total_value": str(value),
            "currency": "CZK",
            "governing_law": "Czech Republic",
            "confidentiality": True,
        },
    )


GENERATORS = {
    "invoice": generate_invoice,
    "purchase_order": generate_purchase_order,
    "receipt": generate_receipt,
    "contract": generate_contract,
}

# Roughly the mix an SMB accounting inbox sees: mostly invoices, some receipts,
# fewer orders, contracts rarely.
DEFAULT_MIX = {"invoice": 0.55, "receipt": 0.2, "purchase_order": 0.15, "contract": 0.10}


def build_corpus(size: int = 120, *, seed: int = 20240613) -> list[GroundTruth]:
    """Generate a corpus. The seed makes it byte-identical across runs.

    Reproducibility is the point: two evaluation runs that disagree must disagree
    because the *system* changed, not because the corpus did.
    """
    rng = random.Random(seed)  # noqa: S311 — reproducibility requires a seeded PRNG, not a CSPRNG
    corpus: list[GroundTruth] = []
    counters = dict.fromkeys(GENERATORS, 0)

    for _ in range(size):
        kind = rng.choices(list(DEFAULT_MIX), weights=list(DEFAULT_MIX.values()))[0]
        counters[kind] += 1
        corpus.append(GENERATORS[kind](rng, counters[kind]))

    return corpus


def write_corpus(path: Path, corpus: list[GroundTruth]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in corpus:
            handle.write(json.dumps(item.to_json(), ensure_ascii=False) + "\n")


def read_corpus(path: Path) -> list[GroundTruth]:
    items: list[GroundTruth] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                items.append(GroundTruth(**json.loads(line)))
    return items


DEFAULT_CORPUS_PATH = Path(__file__).resolve().parents[3] / "eval_data" / "corpus.jsonl"
