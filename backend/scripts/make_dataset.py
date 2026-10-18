"""Generate a realistic-looking but entirely fictional evaluation set: invoices, contracts
and insurance policies as PDFs plus ground-truth labels.

    uv run python scripts/make_dataset.py

Every document is marked as fictional in its footer. Company names, people, IČO, accounts
and amounts are invented; any resemblance to a real subject is accidental. Ground truth is
what is *printed* on the document, and expected issues are derived from it with the same
rule engine the app uses, so labels cannot drift from the templates.
"""

import json
import random
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.checks import apply_rules, normalize  # noqa: E402
from app.templates import load_templates  # noqa: E402

OUT = ROOT / "dataset"
FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FOOTER = "Fiktivní vzorový dokument (Docflow) – údaje nepatří žádné skutečné osobě ani firmě."
YEAR = 2026

MONTHS = "ledna února března dubna května června července srpna září října listopadu prosince"
MONTHS = MONTHS.split()

# --------------------------------------------------------------------------- fake data

COMPANY_NAMES = [
    "Modrá Kancelář s.r.o.",
    "Pekárna U Mlýna a.s.",
    "Javorová Stavební s.r.o.",
    "Lumen Data Systems s.r.o.",
    "Kovovýroba Hradiště s.r.o.",
    "Zelená Zahrada Bohemia s.r.o.",
    "Tiskárna Nová Linie s.r.o.",
    "Severní Logistika a.s.",
    "Atelier Pod Lipou s.r.o.",
    "Účetní Centrum Brno-sever s.r.o.",
    "Vinařství Na Kopečku s.r.o.",
    "Klimatech Morava s.r.o.",
    "Čisté Prostory CZ s.r.o.",
    "Webové Studio Jih s.r.o.",
    "Dřevodílna Šumava s.r.o.",
    "Kancelářské Potřeby Plus s.r.o.",
    "Hotel Pod Vyhlídkou s.r.o.",
    "Autoservis Vltava Sever s.r.o.",
    "Optika Jasný Pohled s.r.o.",
    "Fyzio Centrum Rovnováha s.r.o.",
    "Realitní Správa Jih a.s.",
    "Strojírna Bystřice Demo a.s.",
]
INSURERS = ["Ilustrační pojišťovna a.s.", "Pojišťovna Vzorník a.s.", "Demo Pojištění Morava a.s."]
FIRST = ["Martin", "Jana", "Petr", "Lucie", "Tomáš", "Eva", "Jakub", "Kateřina", "Ondřej", "Tereza"]
LAST_M = ["Svoboda", "Novák", "Dvořák", "Černý", "Procházka", "Kučera", "Veselý", "Horák"]
LAST_F = ["Svobodová", "Nováková", "Dvořáková", "Černá", "Procházková", "Kučerová", "Veselá"]
IN_CITY = {  # locative for "V Brně dne ..."
    "Praha 2": "Praze",
    "Brno": "Brně",
    "Ostrava": "Ostravě",
    "Plzeň": "Plzni",
    "Olomouc": "Olomouci",
    "Liberec": "Liberci",
    "Hradec Králové": "Hradci Králové",
    "České Budějovice": "Českých Budějovicích",
}
CITIES = [
    ("Praha 2", "120 00", "Městského soudu v Praze"),
    ("Brno", "602 00", "Krajského soudu v Brně"),
    ("Ostrava", "702 00", "Krajského soudu v Ostravě"),
    ("Plzeň", "301 00", "Krajského soudu v Plzni"),
    ("Olomouc", "779 00", "Krajského soudu v Ostravě"),
    ("Liberec", "460 01", "Krajského soudu v Ústí nad Labem"),
    ("Hradec Králové", "500 02", "Krajského soudu v Hradci Králové"),
    ("České Budějovice", "370 01", "Krajského soudu v Českých Budějovicích"),
]
STREETS = ["Vinohradská", "Masarykova", "Nádražní", "Husova", "Palackého", "Komenského"]
STREETS += ["Lidická", "Tyršova", "Jungmannova", "Školní", "Pražská", "Zahradní"]
BANK_CODES = ["0100", "0300", "0800", "2010", "5500", "0600", "6100"]

ITEMS = [  # (description, unit, unit price range, VAT rate)
    ("Kancelářský papír A4, 80 g, 500 listů", "bal", (89, 139), 21),
    ("Toner do tiskárny, černý", "ks", (1290, 2490), 21),
    ("Servis tiskáren – paušál", "měs", (1500, 3500), 21),
    ("Správa IT infrastruktury", "hod", (650, 1200), 21),
    ("Konzultace – návrh webu", "hod", (900, 1600), 21),
    ("Hosting a doména, roční poplatek", "ks", (2400, 6900), 21),
    ("Úklid kancelářských prostor", "měs", (4500, 12000), 21),
    ("Montáž klimatizační jednotky", "ks", (8900, 21000), 21),
    ("Revize elektroinstalace", "ks", (3500, 9800), 21),
    ("Dopravné", "km", (14, 32), 21),
    ("Catering – pracovní oběd", "os", (290, 590), 12),
    ("Ubytování – jednolůžkový pokoj", "noc", (1450, 2900), 12),
    ("Odborná literatura", "ks", (390, 1290), 12),
    ("Vstupné – školení BOZP", "os", (900, 1900), 12),
    ("Pekařské výrobky – rohlíky", "ks", (3, 5), 12),
    ("Kancelářská židle ergonomická", "ks", (4990, 12990), 21),
]

BOILERPLATE = {
    "povinnosti": [
        "Smluvní strany se zavazují vzájemně spolupracovat a poskytovat si veškeré informace "
        "potřebné k řádnému plnění této smlouvy.",
        "Každá ze smluvních stran je povinna bez zbytečného odkladu informovat druhou stranu "
        "o všech skutečnostech, které mohou mít vliv na plnění podle této smlouvy.",
        "Smluvní strany jsou povinny zachovávat mlčenlivost o všech skutečnostech, o nichž se "
        "dozvěděly v souvislosti s touto smlouvou, a to i po jejím skončení.",
    ],
    "sankce": [
        "V případě prodlení s úhradou peněžitého plnění je dlužník povinen zaplatit úrok "
        "z prodlení ve výši stanovené právními předpisy.",
        "Zaplacením smluvní pokuty není dotčeno právo na náhradu škody v plné výši.",
    ],
    "zaverecna": [
        "Tato smlouva se řídí právním řádem České republiky, zejména zákonem č. 89/2012 Sb., "
        "občanský zákoník.",
        "Změny této smlouvy lze činit pouze písemnými, vzestupně číslovanými dodatky "
        "podepsanými oběma smluvními stranami.",
        "Smlouva je vyhotovena ve dvou stejnopisech, z nichž každá smluvní strana obdrží jeden.",
        "Smluvní strany prohlašují, že si smlouvu přečetly, s jejím obsahem souhlasí a na důkaz "
        "toho připojují své podpisy.",
    ],
}


def ico_check(prefix: str) -> str:
    remainder = sum(int(d) * w for d, w in zip(prefix, range(8, 1, -1), strict=True)) % 11
    return str((11 - remainder) % 10)


def make_ico(rng: random.Random, valid: bool = True) -> str:
    prefix = f"{rng.randint(1000000, 9999999)}"
    check = ico_check(prefix)
    if not valid:
        check = str((int(check) + rng.randint(1, 9)) % 10)
    return prefix + check


@dataclass
class Party:
    name: str
    street: str
    city: str
    zip: str
    court: str
    ico: str
    vat_payer: bool = True
    person: str = ""
    role: str = "jednatel"
    file_no: str = ""

    @property
    def dic(self) -> str:
        return f"CZ{self.ico}"

    @property
    def address(self) -> str:
        return f"{self.street}, {self.zip} {self.city}"

    @property
    def registry(self) -> str:
        return f"zapsaná v obchodním rejstříku vedeném u {self.court}, oddíl {self.file_no}"


def person(rng: random.Random) -> str:
    first = rng.choice(FIRST)
    female = first.endswith("a")
    last = rng.choice(LAST_F if female else LAST_M)
    title = rng.choice(["", "", "Ing. ", "Mgr. "])
    return f"{title}{first} {last}"


def party(rng: random.Random, name: str, ico_valid: bool = True) -> Party:
    city, zip_code, court = rng.choice(CITIES)
    kind = "B" if name.endswith("a.s.") else "C"
    return Party(
        name=name,
        street=f"{rng.choice(STREETS)} {rng.randint(1, 180)}/{rng.randint(1, 40)}",
        city=city,
        zip=zip_code,
        court=court,
        ico=make_ico(rng, ico_valid),
        person=person(rng),
        role="člen představenstva" if kind == "B" else "jednatel",
        file_no=f"{kind}, vložka {rng.randint(1000, 299999)}",
    )


def bank_account(rng: random.Random) -> str:
    prefix = rng.choice(["", "", "19-", "35-"])
    return f"{prefix}{rng.randint(10**8, 10**10 - 1)}/{rng.choice(BANK_CODES)}"


def money(x: float, style: str = "cz", decimals: int = 2) -> str:
    s = f"{x:,.{decimals}f}"
    if style == "cz":
        return s.replace(",", " ").replace(".", ",")
    if style == "cz_dot":
        return s.replace(",", "_").replace(".", ",").replace("_", ".")
    return s  # en


def fmt_date(d: date, style: str) -> str:
    if style == "cz":
        return f"{d.day}. {d.month}. {d.year}"
    if style == "cz0":
        return f"{d:%d.%m.%Y}"
    if style == "iso":
        return d.isoformat()
    return f"{d.day}. {MONTHS[d.month - 1]} {d.year}"  # words


def random_date(rng: random.Random, start_month: int = 1, end_month: int = 10) -> date:
    return date(YEAR, rng.randint(start_month, end_month), rng.randint(1, 28))


# --------------------------------------------------------------------------- PDF helpers


class Doc(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(True, 22)
        self.add_font("A", fname=str(FONT_DIR / "Arial.ttf"))
        self.add_font("A", style="B", fname=str(FONT_DIR / "Arial Bold.ttf"))
        self.add_font("A", style="I", fname=str(FONT_DIR / "Arial Italic.ttf"))
        self.set_text_color(25, 28, 35)
        self.add_page()

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("A", "", 7)
        self.set_text_color(130, 130, 130)
        self.cell(0, 4, f"{FOOTER}   ·   Strana {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(25, 28, 35)

    @property
    def content_w(self) -> float:
        return self.w - self.l_margin - self.r_margin

    def para(self, text: str, size: float = 9.5, style: str = "", h: float = 4.8, **kw) -> None:
        self.set_font("A", style, size)
        self.multi_cell(kw.pop("w", 0), h, text, new_x="LMARGIN", new_y="NEXT", **kw)

    def gap(self, h: float = 3) -> None:
        self.ln(h)

    def rule(self, color: tuple[int, int, int] = (210, 214, 220)) -> None:
        self.set_draw_color(*color)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())

    def block(
        self, x: float, y: float, w: float, title: str, lines: list[str], boxed: bool
    ) -> float:
        """Draw a titled block of lines; return its bottom y."""
        self.set_xy(x + (3 if boxed else 0), y + (2.5 if boxed else 0))
        self.set_font("A", "B", 7.5)
        self.set_text_color(110, 115, 125)
        self.cell(w - 6, 4, title.upper(), new_x="LEFT", new_y="NEXT")
        self.set_text_color(25, 28, 35)
        for i, line in enumerate(lines):
            self.set_font("A", "B" if i == 0 else "", 10 if i == 0 else 9)
            self.multi_cell(w - 6, 4.6, line, new_x="LEFT", new_y="NEXT")
        bottom = self.get_y() + (2.5 if boxed else 0)
        if boxed:
            self.set_draw_color(200, 205, 212)
            self.rect(x, y, w, bottom - y)
        return bottom

    def kv(self, pairs: list[tuple[str, str]], x: float, label_w: float, w: float) -> float:
        for label, value in pairs:
            self.set_x(x)
            self.set_font("A", "", 9)
            self.set_text_color(95, 100, 110)
            self.cell(label_w, 5, label)
            self.set_text_color(25, 28, 35)
            self.set_font("A", "B", 9)
            self.cell(w - label_w, 5, value, new_x="LMARGIN", new_y="NEXT")
        return self.get_y()

    def table(self, headers, rows, widths, align, width=None, position="CENTER") -> None:
        self.set_font("A", "", 8.5)
        self.set_draw_color(215, 219, 225)
        with super().table(
            width=width or self.content_w,
            align=position,
            col_widths=widths,
            text_align=align,
            line_height=5.2,
            headings_style=FontFace(emphasis="BOLD", fill_color=(236, 239, 244)),
            borders_layout="HORIZONTAL_LINES",
            padding=1.2,
        ) as t:
            t.row(headers)
            for r in rows:
                t.row(r)


@dataclass
class Sample:
    file: str
    template_id: str
    description: str
    raw: dict[str, str | None] = field(default_factory=dict)


# --------------------------------------------------------------------------- invoices


def invoice(rng: random.Random, n: int, layout: str, errors: set[str], items_count: int) -> Sample:
    supplier_name, customer_name = rng.sample(COMPANY_NAMES, 2)
    supplier = party(rng, supplier_name, ico_valid="ico" not in errors)
    customer = party(rng, customer_name)
    issue = random_date(rng)
    due = issue + timedelta(days=rng.choice([14, 14, 15, 30]))
    if "dates" in errors:
        due = issue - timedelta(days=rng.choice([7, 14]))
    taxable = issue - timedelta(days=rng.choice([0, 0, 2, 5]))
    currency = "EUR" if layout == "C" else "CZK"
    style = "en" if layout == "C" else rng.choice(["cz", "cz", "cz_dot"])
    dstyle = "iso" if layout == "C" else rng.choice(["cz", "cz0"])

    lines, bases, vats = [], {}, {}
    # EUR invoices are B2B services; small consumables would look odd converted to euros.
    pool = [i for i in ITEMS if i[2][0] >= 600] if currency == "EUR" else ITEMS
    picked = rng.sample(pool, k=min(items_count, len(pool)))
    picked += [rng.choice(pool) for _ in range(items_count - len(picked))]
    for desc, unit, (lo, hi), rate in picked:
        qty = rng.choice([1, 1, 2, 3, 5, 10, 12, 25]) if unit != "měs" else 1
        price = round(rng.uniform(lo, hi) / (25 if currency == "EUR" else 1), 2)
        line = round(qty * price, 2)
        lines.append((desc, f"{qty} {unit}", price, rate, line))
        bases[rate] = round(bases.get(rate, 0) + line, 2)
    for rate, base in bases.items():
        vats[rate] = round(base * rate / 100, 2)
    net, vat = round(sum(bases.values()), 2), round(sum(vats.values()), 2)
    total = round(net + vat, 2)
    rounding = 0.0
    if layout == "A" and currency == "CZK" and rng.random() < 0.6:
        rounding = round(round(total) - total, 2)
        total = round(total + rounding, 2)
    if "sum" in errors:
        total = round(total + rng.choice([-900, -100, 90, 1000]), 2)

    number = rng.choice([f"FV{YEAR}{n:04d}", f"{YEAR}-{n:04d}", f"{YEAR % 100}0{n:03d}"])
    account = bank_account(rng)
    m = lambda x: money(x, style)  # noqa: E731
    d = lambda x: fmt_date(x, dstyle)  # noqa: E731
    pdf = Doc()
    raw = {
        "supplier_name": supplier.name,
        "supplier_ico": supplier.ico,
        "customer_name": customer.name,
        "invoice_number": number,
        "issue_date": d(issue),
        "due_date": d(due),
        "net_amount": m(net),
        "vat_amount": m(vat),
        "total_amount": m(total),
        "currency": currency,
        "bank_account": account,
    }

    def party_lines(p: Party) -> list[str]:
        out = [p.name, p.street, f"{p.zip} {p.city}", f"IČO: {p.ico}   DIČ: {p.dic}"]
        return out

    def items_table(english: bool = False) -> None:
        cur = "EUR" if english else "Kč"
        headers = (
            ["Description", "Qty", "Unit price", "VAT", f"Amount ({cur})"]
            if english
            else ["Označení dodávky", "Množství", "Cena za MJ", "DPH", "Celkem bez DPH"]
        )
        rows = [
            [desc, qty, m(price), f"{rate} %", m(line)] for desc, qty, price, rate, line in lines
        ]
        pdf.table(headers, rows, (78, 22, 26, 14, 34), ("LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"))

    def vat_recap() -> None:
        rows = [[f"{r} %", m(bases[r]), m(vats[r]), m(bases[r] + vats[r])] for r in sorted(bases)]
        rows.append(["Celkem", m(net), m(vat), m(net + vat)])
        pdf.table(
            ["Sazba DPH", "Základ", "DPH", "Celkem s DPH"],
            rows,
            (22, 30, 28, 30),
            ("LEFT", "RIGHT", "RIGHT", "RIGHT"),
            width=110,
            position="RIGHT",
        )

    if layout == "A":  # classic Czech tax document
        pdf.set_font("A", "B", 15)
        pdf.cell(0, 8, "FAKTURA – DAŇOVÝ DOKLAD", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("A", "", 10)
        pdf.cell(0, 5, f"číslo {number}", new_x="LMARGIN", new_y="NEXT")
        pdf.gap(4)
        y, half = pdf.get_y(), pdf.content_w / 2 - 3
        b1 = pdf.block(pdf.l_margin, y, half, "Dodavatel", party_lines(supplier), boxed=True)
        b2 = pdf.block(
            pdf.l_margin + half + 6, y, half, "Odběratel", party_lines(customer), boxed=True
        )
        pdf.set_y(max(b1, b2) + 2)
        pdf.set_font("A", "I", 7)
        pdf.cell(0, 4, f"Dodavatel je {supplier.registry}.", new_x="LMARGIN", new_y="NEXT")
        pdf.gap(3)
        y = pdf.get_y()
        pdf.kv(
            [
                ("Datum vystavení:", d(issue)),
                ("Datum uskut. zdan. plnění:", d(taxable)),
                ("Datum splatnosti:", d(due)),
            ],
            pdf.l_margin,
            44,
            half,
        )
        pdf.set_y(y)
        pdf.kv(
            [
                ("Forma úhrady:", "převodním příkazem"),
                ("Bankovní účet:", account),
                ("Variabilní symbol:", "".join(c for c in number if c.isdigit())),
            ],
            pdf.l_margin + half + 6,
            34,
            half,
        )
        pdf.gap(5)
        items_table()
        pdf.gap(4)
        vat_recap()
        pdf.gap(3)
        if rounding:
            pdf.kv([("Zaokrouhlení:", m(rounding))], pdf.l_margin + 100, 40, 74)
        pdf.set_x(pdf.l_margin + 90)
        pdf.set_fill_color(236, 239, 244)
        pdf.set_font("A", "B", 11)
        pdf.cell(
            pdf.content_w - 90, 9, f"Celkem k úhradě: {m(total)} {currency}", align="R", fill=True
        )
        pdf.ln(12)
        pdf.para(f"Vystavil: {supplier.person}", size=8.5)

    elif layout == "B":  # modern invoicing app
        pdf.set_font("A", "B", 18)
        pdf.set_text_color(47, 91, 234)
        pdf.cell(0, 9, supplier.name, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(25, 28, 35)
        pdf.para(f"{supplier.address}  ·  IČO {supplier.ico}  ·  DIČ {supplier.dic}", size=8.5)
        pdf.gap(6)
        pdf.set_font("A", "B", 13)
        pdf.cell(0, 7, f"Faktura {number}", new_x="LMARGIN", new_y="NEXT")
        pdf.gap(2)
        y, half = pdf.get_y(), pdf.content_w / 2 - 3
        b = pdf.block(pdf.l_margin, y, half, "Fakturujeme", party_lines(customer), boxed=False)
        pdf.set_y(y)
        pdf.kv(
            [
                ("Vystaveno", d(issue)),
                ("DUZP", d(taxable)),
                ("Splatnost", d(due)),
                ("Účet", account),
                ("Měna", currency),
            ],
            pdf.l_margin + half + 6,
            26,
            half,
        )
        pdf.set_y(max(b, pdf.get_y()) + 6)
        items_table()
        pdf.gap(4)
        rows = [("Mezisoučet bez DPH", m(net))]
        rows += [(f"DPH {r} % ze základu {m(bases[r])}", m(vats[r])) for r in sorted(bases)]
        rows += [("DPH celkem", m(vat))]
        for label, value in rows:
            pdf.set_x(pdf.l_margin + 70)
            pdf.set_font("A", "", 9)
            pdf.cell(70, 5.5, label)
            pdf.cell(pdf.content_w - 140, 5.5, value, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(pdf.l_margin + 70)
        pdf.rule()
        pdf.set_x(pdf.l_margin + 70)
        pdf.set_font("A", "B", 12)
        pdf.cell(55, 9, "Celkem k úhradě")
        pdf.cell(pdf.content_w - 125, 9, f"{m(total)} {currency}", align="R")
        pdf.ln(14)
        pdf.para("Děkujeme za spolupráci. Fakturu uhraďte na uvedený účet.", size=8.5)

    else:  # C: bilingual EUR invoice
        pdf.set_font("A", "B", 15)
        pdf.cell(0, 8, f"INVOICE / FAKTURA No. {number}", new_x="LMARGIN", new_y="NEXT")
        pdf.gap(4)
        y, half = pdf.get_y(), pdf.content_w / 2 - 3
        b1 = pdf.block(pdf.l_margin, y, half, "Supplier / Dodavatel", party_lines(supplier), True)
        b2 = pdf.block(
            pdf.l_margin + half + 6, y, half, "Customer / Odběratel", party_lines(customer), True
        )
        pdf.set_y(max(b1, b2) + 4)
        pdf.kv(
            [
                ("Issue date / Datum vystavení:", d(issue)),
                ("Due date / Datum splatnosti:", d(due)),
                ("Currency / Měna:", currency),
                ("Bank account / Účet:", account),
            ],
            pdf.l_margin,
            58,
            pdf.content_w,
        )
        pdf.gap(4)
        items_table(english=True)
        pdf.gap(4)
        for label, value in [("Net / Základ daně:", m(net)), ("VAT / DPH celkem:", m(vat))]:
            pdf.set_x(pdf.l_margin + 80)
            pdf.set_font("A", "", 9.5)
            pdf.cell(50, 6, label)
            pdf.cell(
                pdf.content_w - 130, 6, f"{value} EUR", align="R", new_x="LMARGIN", new_y="NEXT"
            )
        pdf.set_x(pdf.l_margin + 80)
        pdf.set_font("A", "B", 11)
        pdf.cell(50, 8, "Total due / Celkem:")
        pdf.cell(pdf.content_w - 130, 8, f"{m(total)} EUR", align="R")
        pdf.ln(12)

    name = f"invoice_{n:02d}.pdf"
    pdf.output(OUT / name)
    desc = f"layout {layout}, {len(lines)} pol., sazby DPH {sorted(bases)}, čísla {style}, data {dstyle}"
    if errors:
        desc += f", vložené chyby: {', '.join(sorted(errors))}"
    return Sample(name, "invoice", desc, raw)


# --------------------------------------------------------------------------- contracts

CONTRACT_TYPES = {
    "dilo": dict(
        title="SMLOUVA O DÍLO",
        law="uzavřená podle § 2586 a násl. zákona č. 89/2012 Sb., občanský zákoník",
        roles=("objednatel", "zhotovitel"),
        subjects=[
            "vytvoření e-shopu včetně napojení na účetní systém objednatele",
            "rekonstrukce kancelářských prostor ve 2. nadzemním podlaží",
            "dodávka a montáž vzduchotechniky do výrobní haly",
        ],
        verb="Zhotovitel se zavazuje provést pro objednatele dílo spočívající v",
        price_label="Cena díla činí {price} bez DPH.",
        price_range=(85_000, 1_450_000),
        term="fixed",
    ),
    "najem": dict(
        title="SMLOUVA O NÁJMU PROSTOR SLOUŽÍCÍCH K PODNIKÁNÍ",
        law="uzavřená podle § 2302 a násl. zákona č. 89/2012 Sb., občanský zákoník",
        roles=("pronajímatel", "nájemce"),
        subjects=[
            "nájem nebytových prostor o výměře 86 m² v přízemí budovy",
            "nájem skladovacích prostor o výměře 240 m²",
        ],
        verb="Pronajímatel přenechává nájemci k dočasnému užívání",
        price_label="Nájemné činí {price} měsíčně a je splatné vždy do 15. dne kalendářního měsíce.",
        price_range=(14_000, 68_000),
        term="indefinite",
    ),
    "sluzby": dict(
        title="SMLOUVA O POSKYTOVÁNÍ SLUŽEB",
        law="uzavřená podle § 1746 odst. 2 zákona č. 89/2012 Sb., občanský zákoník",
        roles=("objednatel", "poskytovatel"),
        subjects=[
            "správa IT infrastruktury a uživatelská podpora",
            "vedení účetnictví a zpracování mezd",
            "pravidelný úklid kancelářských prostor",
        ],
        verb="Poskytovatel se zavazuje poskytovat objednateli služby, jejichž předmětem je",
        price_label="Měsíční paušální odměna za poskytované služby činí {price} bez DPH.",
        price_range=(6_500, 45_000),
        term="either",
    ),
    "kupni": dict(
        title="KUPNÍ SMLOUVA",
        law="uzavřená podle § 2079 a násl. zákona č. 89/2012 Sb., občanský zákoník",
        roles=("prodávající", "kupující"),
        subjects=[
            "prodej užitkového vozidla tovární značky Demo Van, rok výroby 2023",
            "prodej CNC frézky včetně příslušenství a dokumentace",
        ],
        verb="Prodávající se zavazuje odevzdat kupujícímu předmět koupě, kterým je",
        price_label="Kupní cena činí {price} včetně DPH a je splatná do 10 dnů od podpisu smlouvy.",
        price_range=(180_000, 2_400_000),
        term="none",
    ),
}


SIGN_AS = {  # genitive for "za ..." under the signature line
    "objednatel": "objednatele",
    "zhotovitel": "zhotovitele",
    "pronajímatel": "pronajímatele",
    "nájemce": "nájemce",
    "poskytovatel": "poskytovatele",
    "prodávající": "prodávajícího",
    "kupující": "kupujícího",
}


def contract(rng: random.Random, n: int, kind: str, errors: set[str]) -> Sample:
    spec = CONTRACT_TYPES[kind]
    a_name, b_name = rng.sample(COMPANY_NAMES, 2)
    a = party(rng, a_name, ico_valid="ico" not in errors)
    b = party(rng, b_name)
    role_a, role_b = spec["roles"]
    subject = rng.choice(spec["subjects"])
    dstyle = rng.choice(["cz", "words", "words", "cz0"])
    d = lambda x: fmt_date(x, dstyle)  # noqa: E731
    signing = random_date(rng, 1, 9)
    start = signing + timedelta(days=rng.choice([0, 1, 7, 14]))
    term = spec["term"] if spec["term"] != "either" else rng.choice(["fixed", "indefinite"])
    end = None
    if term == "fixed":
        end = start + timedelta(days=rng.choice([90, 180, 364]))
        if "dates" in errors:
            end = start - timedelta(days=rng.choice([30, 60]))
    lo, hi = spec["price_range"]
    price_val = round(rng.uniform(lo, hi), -2)
    price = rng.choice([f"{money(price_val, 'cz', 0)} Kč", f"{money(price_val, 'cz', 0)},- Kč"])
    notice = (
        rng.choice(["1 měsíc", "2 měsíce", "3 měsíce"])
        if term == "indefinite" or kind == "sluzby"
        else None
    )
    number = f"{kind.upper()[:2]}-{YEAR}-{n:03d}"

    pdf = Doc()
    pdf.set_font("A", "B", 15)
    pdf.cell(0, 8, spec["title"], align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("A", "", 10)
    pdf.cell(0, 5, f"č. {number}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("A", "I", 8.5)
    pdf.cell(0, 5, spec["law"], align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.gap(6)
    pdf.para("Smluvní strany:", style="B", size=10)
    pdf.gap(2)
    for p, role in ((a, role_a), (b, role_b)):
        pdf.para(p.name, style="B", size=10)
        pdf.para(
            f"se sídlem: {p.address}\nIČO: {p.ico}, DIČ: {p.dic}\n{p.registry}\n"
            f"zastoupená: {p.person}, {p.role}",
            size=9.5,
        )
        pdf.para(f"(dále jen „{role}“)", size=9.5, style="I")
        if p is a:
            pdf.gap(2)
            pdf.para("a", size=9.5)
            pdf.gap(2)
    pdf.gap(4)

    articles = [
        ("Předmět smlouvy", [f"{spec['verb']} {subject} (dále jen „předmět smlouvy“)."]),
        (
            "Cena a platební podmínky",
            [spec["price_label"].format(price=price)]
            + ["Cena bude uhrazena na základě daňového dokladu vystaveného oprávněnou stranou."],
        ),
    ]
    duration = [f"Tato smlouva nabývá účinnosti dnem {d(start)}."]
    if term == "fixed":
        duration.append(f"Smlouva se uzavírá na dobu určitou do {d(end)}.")
    elif term == "indefinite":
        duration.append("Smlouva se uzavírá na dobu neurčitou.")
    if notice:
        duration.append(
            f"Kterákoli ze smluvních stran je oprávněna smlouvu vypovědět i bez udání důvodu; "
            f"výpovědní lhůta činí {notice} a počíná běžet prvním dnem měsíce následujícího po "
            "doručení výpovědi."
        )
    articles.append(("Doba trvání smlouvy", duration))
    articles.append(("Práva a povinnosti smluvních stran", BOILERPLATE["povinnosti"]))
    articles.append(("Sankce", BOILERPLATE["sankce"]))
    articles.append(("Závěrečná ustanovení", BOILERPLATE["zaverecna"]))

    for i, (title, paras) in enumerate(articles, 1):
        roman = ["I", "II", "III", "IV", "V", "VI", "VII"][i - 1]
        pdf.gap(2)
        pdf.para(f"{roman}.\n{title}", style="B", size=10, align="C")
        pdf.gap(1)
        for j, text in enumerate(paras, 1):
            pdf.para(f"{i}.{j}  {text}", size=9.5, align="J")
            pdf.gap(1)

    pdf.gap(10)
    pdf.para(f"V {IN_CITY[a.city]} dne {d(signing)}", size=9.5)
    pdf.gap(16)
    y, half = pdf.get_y(), pdf.content_w / 2
    for col, (p, role) in enumerate(((a, role_a), (b, role_b))):
        x = pdf.l_margin + col * half
        pdf.set_xy(x, y)
        pdf.set_font("A", "", 9)
        pdf.cell(half - 10, 5, "……………………………………", new_x="LEFT", new_y="NEXT")
        pdf.cell(half - 10, 5, f"za {SIGN_AS[role]}", new_x="LEFT", new_y="NEXT")
        pdf.cell(half - 10, 5, f"{p.person}, {p.role}", new_x="LEFT", new_y="NEXT")

    name = f"contract_{n:02d}.pdf"
    pdf.output(OUT / name)
    raw = {
        "party_a_name": a.name,
        "party_a_ico": a.ico,
        "party_b_name": b.name,
        "party_b_ico": b.ico,
        "subject": subject,
        "price": price,
        "start_date": d(start),
        "end_date": d(end) if end else None,
        "notice_period": notice,
        "signing_date": d(signing),
    }
    desc = f"{spec['title'].lower()}, {pdf.page_no()} str., data {dstyle}, doba {term}"
    if errors:
        desc += f", vložené chyby: {', '.join(sorted(errors))}"
    return Sample(name, "contract", desc, raw)


# --------------------------------------------------------------------------- insurance


def policy(rng: random.Random, n: int, kind: str, errors: set[str]) -> Sample:
    insurer = party(rng, rng.choice(INSURERS))
    holder = party(rng, rng.choice(COMPANY_NAMES), ico_valid="ico" not in errors)
    number = f"{rng.choice(['88', '41', '70'])}{rng.randint(10**7, 10**8 - 1)}"
    dstyle = rng.choice(["cz", "cz0"])
    d = lambda x: fmt_date(x, dstyle)  # noqa: E731
    start = date(YEAR, rng.choice([1, 1, 4, 7, 10]), 1)
    indefinite = rng.random() < 0.35
    end = None if indefinite else date(start.year + 1, start.month, 1) - timedelta(days=1)
    if "dates" in errors:
        indefinite = False
        end = start - timedelta(days=rng.choice([31, 90]))
    concluded = start - timedelta(days=rng.choice([5, 12, 20]))
    m = lambda x: money(x, "cz", 0)  # noqa: E731
    deductible = rng.choice([1_000, 5_000, 10_000, 20_000])

    if kind == "majetek":
        building = round(rng.uniform(4_000_000, 38_000_000), -5)
        movables = round(rng.uniform(300_000, 6_000_000), -4)
        sum_insured = building + movables
        rows = [
            [
                "Budova – sídlo pojistníka",
                f"{m(building)} Kč",
                f"{m(deductible)} Kč",
                f"{m(round(building * 0.0011, -1))} Kč",
            ],
            [
                "Soubor movitých věcí",
                f"{m(movables)} Kč",
                f"{m(deductible)} Kč",
                f"{m(round(movables * 0.0024, -1))} Kč",
            ],
        ]
        premium = round(building * 0.0011, -1) + round(movables * 0.0024, -1)
        title, product = "pojištění majetku podnikatelů", "Celková pojistná částka"
        headers = ["Předmět pojištění", "Pojistná částka", "Spoluúčast", "Roční pojistné"]
    else:
        sum_insured = rng.choice([1_000_000, 3_000_000, 5_000_000, 10_000_000, 20_000_000])
        premium = round(sum_insured * rng.uniform(0.0009, 0.002), -1)
        rows = [
            [
                "Obecná odpovědnost za újmu",
                f"{m(sum_insured)} Kč",
                f"{m(deductible)} Kč",
                f"{m(premium)} Kč",
            ]
        ]
        title, product = "pojištění odpovědnosti podnikatele", "Limit pojistného plnění"
        headers = ["Rozsah pojištění", "Limit plnění", "Spoluúčast", "Roční pojistné"]

    pdf = Doc()
    pdf.set_font("A", "B", 9)
    pdf.set_text_color(47, 91, 234)
    pdf.cell(0, 5, insurer.name.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(25, 28, 35)
    pdf.gap(4)
    pdf.set_font("A", "B", 15)
    pdf.cell(0, 8, f"POJISTNÁ SMLOUVA č. {number}", new_x="LMARGIN", new_y="NEXT")
    pdf.para(title, size=10)
    pdf.gap(5)
    y, half = pdf.get_y(), pdf.content_w / 2 - 3
    ins_lines = [
        insurer.name,
        insurer.address,
        f"IČO: {insurer.ico}",
        f"zastoupená: {insurer.person}",
    ]
    hold_lines = [
        holder.name,
        holder.address,
        f"IČO: {holder.ico}",
        f"zastoupená: {holder.person}, {holder.role}",
    ]
    b1 = pdf.block(pdf.l_margin, y, half, "Pojistitel", ins_lines, boxed=True)
    b2 = pdf.block(pdf.l_margin + half + 6, y, half, "Pojistník", hold_lines, boxed=True)
    pdf.set_y(max(b1, b2) + 3)
    pdf.para("Pojištěný: shodný s pojistníkem.", size=9)
    pdf.gap(4)
    pdf.para("1. Pojistná doba", style="B", size=10)
    if indefinite:
        pdf.para(
            f"Pojištění se sjednává na dobu neurčitou s počátkem pojištění dne {d(start)}.",
            size=9.5,
        )
    else:
        pdf.kv(
            [("Počátek pojištění:", d(start)), ("Konec pojištění:", d(end))], pdf.l_margin, 40, 120
        )
    pdf.gap(4)
    pdf.para("2. Rozsah pojištění", style="B", size=10)
    pdf.gap(1)
    pdf.table(headers, rows, (66, 36, 30, 42), ("LEFT", "RIGHT", "RIGHT", "RIGHT"))
    pdf.gap(2)
    pdf.kv(
        [(f"{product}:", f"{m(sum_insured)} Kč"), ("Spoluúčast:", f"{m(deductible)} Kč")],
        pdf.l_margin,
        48,
        120,
    )
    pdf.gap(4)
    pdf.para("3. Pojistné", style="B", size=10)
    freq = rng.choice([("čtvrtletně", 4), ("pololetně", 2), ("ročně", 1)])
    pdf.kv(
        [
            ("Roční pojistné celkem:", f"{m(premium)} Kč"),
            ("Frekvence placení:", freq[0]),
            ("Výše splátky:", f"{money(premium / freq[1], 'cz', 2)} Kč"),
            ("Číslo účtu pojistitele:", bank_account(rng)),
            ("Variabilní symbol:", number),
        ],
        pdf.l_margin,
        48,
        120,
    )
    pdf.gap(4)
    pdf.para("4. Závěrečná ujednání", style="B", size=10)
    for text in [
        "Pojištění se řídí touto pojistnou smlouvou, Všeobecnými pojistnými podmínkami pojistitele "
        "a zákonem č. 89/2012 Sb., občanský zákoník.",
        "Pojistník prohlašuje, že před uzavřením smlouvy převzal pojistné podmínky a informace "
        "o zpracování osobních údajů a že odpověděl pravdivě na všechny dotazy pojistitele.",
        "Pojistná událost musí být pojistiteli oznámena bez zbytečného odkladu, nejpozději do "
        "15 dnů ode dne, kdy se o ní pojistník dozvěděl.",
    ]:
        pdf.para(text, size=9, align="J")
        pdf.gap(1)
    pdf.gap(6)
    pdf.para(f"Smlouva uzavřena v {IN_CITY[insurer.city]} dne {d(concluded)}.", size=9.5)

    name = f"policy_{n:02d}.pdf"
    pdf.output(OUT / name)
    raw = {
        "insurer": insurer.name,
        "policyholder": holder.name,
        "policyholder_ico": holder.ico,
        "policy_number": number,
        "start_date": d(start),
        "end_date": d(end) if end else None,
        "sum_insured": f"{m(sum_insured)} Kč",
        "deductible": f"{m(deductible)} Kč",
        "annual_premium": f"{m(premium)} Kč",
    }
    desc = f"{title}, {'doba neurčitá' if indefinite else 'doba určitá'}, splátky {freq[0]}"
    if errors:
        desc += f", vložené chyby: {', '.join(sorted(errors))}"
    return Sample(name, "insurance_policy", desc, raw)


# --------------------------------------------------------------------------- main

PLAN = [
    # (kind, variant, planted errors, extra)
    *[("invoice", "A", set(), 4), ("invoice", "B", set(), 3), ("invoice", "C", set(), 2)],
    *[("invoice", "A", {"sum"}, 5), ("invoice", "B", set(), 6), ("invoice", "A", set(), 34)],
    *[("invoice", "B", {"ico"}, 2), ("invoice", "C", set(), 4), ("invoice", "A", {"dates"}, 3)],
    *[
        ("invoice", "B", set(), 8),
        ("invoice", "A", {"sum", "ico"}, 3),
        ("invoice", "C", {"dates"}, 3),
    ],
    *[
        ("contract", "dilo", set(), 0),
        ("contract", "najem", set(), 0),
        ("contract", "sluzby", set(), 0),
    ],
    *[
        ("contract", "kupni", set(), 0),
        ("contract", "dilo", {"dates"}, 0),
        ("contract", "sluzby", set(), 0),
    ],
    *[("contract", "najem", {"ico"}, 0), ("contract", "kupni", set(), 0)],
    *[
        ("policy", "majetek", set(), 0),
        ("policy", "odpovednost", set(), 0),
        ("policy", "majetek", {"ico"}, 0),
    ],
    *[
        ("policy", "odpovednost", set(), 0),
        ("policy", "majetek", {"dates"}, 0),
        ("policy", "odpovednost", set(), 0),
    ],
]


def label(sample: Sample) -> dict:
    template = load_templates()[sample.template_id]
    expected: dict = {}
    issues: list[str] = []
    for spec in template.fields:
        raw = sample.raw.get(spec.key)
        expected[spec.key] = normalize(raw, spec.type) if raw is not None else None
        if raw is None and spec.required:
            issues.append("missing")
    values = {k: v for k, v in expected.items() if v is not None}
    issues += [i["code"] for i in apply_rules(template, values)]
    return {
        "file": sample.file,
        "template_id": sample.template_id,
        "description": sample.description,
        "raw": sample.raw,
        "expected": expected,
        "expected_issues": sorted(issues),
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.pdf"):
        old.unlink()
    counters = {"invoice": 0, "contract": 0, "policy": 0}
    labels = []
    for i, (kind, variant, errors, extra) in enumerate(PLAN):
        rng = random.Random(1000 + i)
        counters[kind] += 1
        n = counters[kind]
        if kind == "invoice":
            sample = invoice(rng, n, variant, errors, extra)
        elif kind == "contract":
            sample = contract(rng, n, variant, errors)
        else:
            sample = policy(rng, n, variant, errors)
        labels.append(label(sample))
    (OUT / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(labels)} documents written to {OUT}")


if __name__ == "__main__":
    main()
