"""Generate fictional sample PDFs for demos and tests: uv run python scripts/make_samples.py"""

from pathlib import Path

from fpdf import FPDF

OUT = Path(__file__).resolve().parent.parent / "samples"
FONT = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")


def ico(prefix: str) -> str:
    """Append a valid check digit to a 7-digit prefix (fictional companies)."""
    remainder = sum(int(d) * w for d, w in zip(prefix, range(8, 1, -1), strict=True)) % 11
    return prefix + str((11 - remainder) % 10)


def write_pdf(name: str, title: str, lines: list[str]) -> None:
    pdf = FPDF()
    pdf.add_page()
    if FONT.exists():
        pdf.add_font("body", fname=str(FONT))
        family = "body"
    else:  # ASCII fallback outside macOS
        family = "helvetica"
        lines = [line.encode("ascii", "ignore").decode() for line in lines]
        title = title.encode("ascii", "ignore").decode()
    pdf.set_font(family, size=18)
    pdf.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, size=11)
    for line in lines:
        pdf.multi_cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(OUT / name)


def invoice(
    name: str, number: str, supplier_ico: str, issue: str, due: str, net: str, vat: str, total: str
):
    write_pdf(
        name,
        f"Faktura – daňový doklad č. {number}",
        [
            "Dodavatel: Modrá Kancelář s.r.o., Vinohradská 12, 120 00 Praha 2",
            f"IČO: {supplier_ico}   DIČ: CZ{supplier_ico}",
            "Odběratel: Pekárna U Mlýna a.s., Nádražní 5, 602 00 Brno",
            f"IČO: {ico('2648193')}",
            "",
            f"Datum vystavení: {issue}",
            "Datum zdanitelného plnění: " + issue,
            f"Datum splatnosti: {due}",
            "Forma úhrady: převodem   Bankovní účet: 2801234567/2010",
            "",
            "Položky:",
            "Kancelářské potřeby – 1 ks ........ 8 000,00 Kč",
            "Servis tiskáren – 1 ks ............ 2 000,00 Kč",
            "",
            f"Základ daně 21 %: {net} Kč",
            f"DPH 21 %: {vat} Kč",
            f"Celkem k úhradě: {total} Kč",
            "Měna: CZK",
        ],
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    invoice(
        "faktura_ok.pdf",
        "2026-0142",
        ico("2748961"),
        "3. 9. 2026",
        "17. 9. 2026",
        "10 000,00",
        "2 100,00",
        "12 100,00",
    )
    # Three planted problems: wrong IČO check digit, due date before issue date, total != net + VAT.
    bad_ico = ico("2748961")[:7] + str((int(ico("2748961")[7]) + 1) % 10)
    invoice(
        "faktura_chyby.pdf",
        "2026-0143",
        bad_ico,
        "20. 9. 2026",
        "6. 9. 2026",
        "10 000,00",
        "2 100,00",
        "12 010,00",
    )
    write_pdf(
        "smlouva_dilo.pdf",
        "Smlouva o dílo č. SD-2026-07",
        [
            f"Objednatel: Pekárna U Mlýna a.s., IČO: {ico('2648193')}, se sídlem Nádražní 5, Brno",
            f"Zhotovitel: Webové Studio Jih s.r.o., IČO: {ico('0571234')}, se sídlem Lidická 40, Brno",
            "",
            "Čl. 1 Předmět smlouvy",
            "Zhotovitel se zavazuje vytvořit pro objednatele e-shop včetně napojení na účetní systém.",
            "",
            "Čl. 2 Cena",
            "Cena díla činí 185 000,00 Kč bez DPH.",
            "",
            "Čl. 3 Doba trvání",
            "Smlouva nabývá účinnosti dnem 1. 10. 2026 a uzavírá se do 31. 3. 2027.",
            "Kterákoli strana může smlouvu vypovědět s výpovědní lhůtou 2 měsíce.",
            "",
            "V Brně dne 25. 9. 2026",
        ],
    )
    write_pdf(
        "pojistka.pdf",
        "Pojistná smlouva č. 8800123456",
        [
            "Pojistitel: Ilustrační pojišťovna a.s.",
            f"Pojistník: Pekárna U Mlýna a.s., IČO {ico('2648193')}",
            "Pojištění majetku a odpovědnosti podnikatele",
            "",
            "Počátek pojištění: 1. 1. 2026   Konec pojištění: 31. 12. 2026",
            "Limit pojistného plnění: 15 000 000 Kč",
            "Spoluúčast: 10 000 Kč",
            "Roční pojistné: 48 600 Kč",
        ],
    )
    print(f"Samples written to {OUT}")


if __name__ == "__main__":
    main()
