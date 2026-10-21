"""Received-invoice XML import files for Czech accounting software.

Formats follow the published schemas, not vendor prose:
- POHODA: Stormware XML v2 (data.xsd, invoice.xsd, type.xsd), invoice without items,
  amounts in invoiceSummary/homeCurrency.
- Money S3: MoneyData/SeznamFaktPrij (Solitea XSD); Zaklad5/DPH5 = reduced rate,
  Zaklad22/DPH22 = standard rate (historical names, the rates themselves are 12/21).

Only what the invoice template extracts is exported. One VAT rate per invoice and CZK
only; anything else is refused so an accountant books it by hand instead of importing
a wrong VAT summary.
"""

import re
from datetime import date
from xml.etree.ElementTree import Element, SubElement, register_namespace, tostring

RATES = {"high": 21, "low": 12}
POHODA = {
    "dat": "http://www.stormware.cz/schema/version_2/data.xsd",
    "inv": "http://www.stormware.cz/schema/version_2/invoice.xsd",
    "typ": "http://www.stormware.cz/schema/version_2/type.xsd",
}
for prefix, uri in POHODA.items():
    register_namespace(prefix, uri)
REQUIRED = [
    "supplier_name",
    "invoice_number",
    "issue_date",
    "net_amount",
    "vat_amount",
    "total_amount",
]


class AccountingError(ValueError):
    pass


def clean(value: str, limit: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()[:limit]


def money(value: float) -> str:
    return f"{value:.2f}"


def vat_rate(net: float, vat: float) -> str:
    """Single VAT rate implied by the totals: none, low (12 %) or high (21 %)."""
    if abs(vat) < 0.005:
        return "none"
    for name, percent in RATES.items():
        if abs(net * percent / 100 - vat) <= 1:
            return name
    raise AccountingError("DPH neodpovídá jedné sazbě 21 % ani 12 %")


def invoice(values: dict) -> dict:
    """Validate one reviewed invoice and derive the VAT split. Raises AccountingError."""
    missing = [key for key in REQUIRED if values.get(key) in (None, "")]
    if missing:
        raise AccountingError(f"chybí {', '.join(missing)}")
    currency = (values.get("currency") or "CZK").strip().upper()
    if currency not in ("CZK", "KČ", "KC"):
        raise AccountingError(f"měna {currency} – export zatím podporuje jen CZK")
    net, vat, total = (float(values[k]) for k in ("net_amount", "vat_amount", "total_amount"))
    if abs(net + vat - total) > 0.5:
        raise AccountingError("základ + DPH nedává celkovou částku")
    number = clean(str(values["invoice_number"]), 32)
    return {
        "supplier": clean(str(values["supplier_name"]), 255),
        "ico": re.sub(r"\D", "", str(values.get("supplier_ico") or ""))[:8] or None,
        "number": number,
        # A variable symbol is numeric; only a purely numeric invoice number can serve.
        "symbol": number if re.fullmatch(r"\d{1,10}", number) else None,
        "issued": date.fromisoformat(values["issue_date"]),
        # DUZP; when the document states none, the issue date is the usual fallback.
        "taxed": date.fromisoformat(values.get("tax_date") or values["issue_date"]),
        "due": values.get("due_date") and date.fromisoformat(values["due_date"]),
        "net": net,
        "vat": vat,
        "total": total,
        "rate": vat_rate(net, vat),
    }


def text(parent: Element, tag: str, value: object) -> None:
    if value is not None:
        SubElement(parent, tag).text = value.isoformat() if isinstance(value, date) else str(value)


def pohoda(invoices: list[dict], pack_id: str) -> bytes:
    dat, inv, typ = (f"{{{POHODA[p]}}}" for p in ("dat", "inv", "typ"))
    pack = Element(
        f"{dat}dataPack",
        {"version": "2.0", "id": pack_id, "application": "Docflow", "note": "Přijaté faktury"},
    )
    for index, item in enumerate(invoices, 1):
        wrapper = SubElement(
            pack, f"{dat}dataPackItem", {"version": "2.0", "id": f"{pack_id}-{index}"}
        )
        node = SubElement(wrapper, f"{inv}invoice", {"version": "2.0"})
        header = SubElement(node, f"{inv}invoiceHeader")
        text(header, f"{inv}invoiceType", "receivedInvoice")
        text(header, f"{inv}symVar", item["symbol"])
        text(header, f"{inv}originalDocument", item["number"])
        text(header, f"{inv}date", item["issued"])
        text(header, f"{inv}dateTax", item["taxed"])
        text(header, f"{inv}dateAccounting", item["taxed"])
        text(header, f"{inv}dateDue", item["due"])
        text(header, f"{inv}text", clean(f"{item['supplier']} {item['number']}", 240))
        address = SubElement(SubElement(header, f"{inv}partnerIdentity"), f"{typ}address")
        text(address, f"{typ}company", item["supplier"])
        text(address, f"{typ}ico", item["ico"])
        home = SubElement(SubElement(node, f"{inv}invoiceSummary"), f"{inv}homeCurrency")
        if item["rate"] == "none":
            text(home, f"{typ}priceNone", money(item["net"]))
        else:
            text(home, f"{typ}price{item['rate'].capitalize()}", money(item["net"]))
            text(home, f"{typ}price{item['rate'].capitalize()}VAT", money(item["vat"]))
    return tostring(pack, encoding="utf-8", xml_declaration=True)


def money_s3(invoices: list[dict]) -> bytes:
    root = Element("MoneyData")
    listing = SubElement(root, "SeznamFaktPrij")
    for item in invoices:
        node = SubElement(listing, "FaktPrij")
        text(node, "Popis", clean(f"{item['supplier']} {item['number']}", 50))
        text(node, "Vystaveno", item["issued"])
        text(node, "DatUcPr", item["taxed"])
        text(node, "PlnenoDPH", item["taxed"])
        text(node, "Splatno", item["due"])
        text(node, "VarSymbol", item["symbol"])
        text(node, "PrijatDokl", item["number"])
        text(node, "Druh", "N")
        text(node, "Dobropis", "0")
        text(node, "SazbaDPH1", RATES["low"])
        text(node, "SazbaDPH2", RATES["high"])
        text(node, "Proplatit", money(item["total"]))
        summary = SubElement(node, "SouhrnDPH")
        net = {item["rate"]: item["net"]}
        vat = {item["rate"]: item["vat"]}
        text(summary, "Zaklad0", money(net.get("none", 0)))
        text(summary, "Zaklad5", money(net.get("low", 0)))
        text(summary, "Zaklad22", money(net.get("high", 0)))
        text(summary, "DPH5", money(vat.get("low", 0)))
        text(summary, "DPH22", money(vat.get("high", 0)))
        text(node, "Celkem", money(item["total"]))
        partner = SubElement(node, "DodOdb")
        text(partner, "ObchNazev", item["supplier"])
        text(partner, "FaktNazev", item["supplier"])
        text(partner, "ICO", item["ico"])
    return tostring(root, encoding="utf-8", xml_declaration=True)
