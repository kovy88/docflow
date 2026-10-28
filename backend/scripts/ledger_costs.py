"""Cost accounts and a fixed/variable proposal from a general ledger or trial balance PDF,
for a broker preparing business interruption insurance (concierge pilot, no UI yet).

    uv run python scripts/ledger_costs.py hlavni_kniha.pdf -o naklady.xlsx
    uv run python scripts/ledger_costs.py --sample      # fictional trial balance in samples/

The XLSX lists every account with its page and verbatim line; check "posoudit" by hand."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.checks import cz_number  # noqa: E402
from app.ledger import parse_ledger, summarize, table  # noqa: E402
from app.pdf import read_pdf  # noqa: E402
from app.xlsx import workbook  # noqa: E402

SAMPLE = ROOT / "samples" / "obratova_predvaha.pdf"

# Fictional company, year 2025: (account, name, debit turnover, credit turnover).
SAMPLE_ACCOUNTS = [
    ("501100", "Spotřeba materiálu", 4_812_340.50, 12_400.00),
    ("502100", "Spotřeba energie", 1_204_880.00, 0.00),
    ("504100", "Prodané zboží", 2_150_000.00, 0.00),
    ("511100", "Opravy a udržování", 318_450.00, 0.00),
    ("512100", "Cestovné", 96_210.40, 0.00),
    ("518100", "Nájemné", 1_440_000.00, 0.00),
    ("518200", "Subdodávky", 2_870_500.00, 0.00),
    ("518300", "Software a IT služby", 214_300.00, 0.00),
    ("521100", "Mzdové náklady", 6_120_000.00, 0.00),
    ("524100", "Zákonné sociální pojištění", 2_068_560.00, 0.00),
    ("531100", "Daň silniční", 28_400.00, 0.00),
    ("538100", "Ostatní daně a poplatky", 12_150.00, 0.00),
    ("548100", "Pojištění majetku", 184_000.00, 0.00),
    ("551100", "Odpisy dlouhodobého majetku", 1_380_000.00, 0.00),
    ("562100", "Úroky z úvěru", 412_600.00, 0.00),
    ("563100", "Kurzové ztráty", 38_220.15, 0.00),
    ("568100", "Bankovní poplatky", 24_480.00, 0.00),
    ("591100", "Daň z příjmů splatná", 610_000.00, 0.00),
    ("601100", "Tržby za vlastní výrobky", 0.00, 21_850_000.00),
    ("602100", "Tržby z prodeje služeb", 0.00, 3_420_000.00),
    ("604100", "Tržby za zboží", 0.00, 2_980_000.00),
]


def money(x: float) -> str:
    whole, cents = f"{x:,.2f}".split(".")
    return whole.replace(",", " ") + "," + cents


def make_sample() -> Path:
    sys.path.insert(0, str(ROOT / "scripts"))
    from make_dataset import Doc

    pdf = Doc()
    pdf.para("Strojírna Vzor s.r.o., IČO 00000000 (fiktivní)", size=9)
    pdf.para("OBRATOVÁ PŘEDVAHA za období 1–12/2025", size=13, style="B")
    pdf.gap(4)
    widths = [18, 62, 30, 30, 34]
    pdf.set_font("A", "B", 8)
    for w, head in zip(
        widths, ["Účet", "Název účtu", "Obrat MD", "Obrat D", "Konečný stav"], strict=True
    ):
        pdf.cell(w, 6, head, border="B", align="L" if w in (18, 62) else "R")
    pdf.ln()
    pdf.set_font("A", "", 8)
    for account, name, debit, credit in SAMPLE_ACCOUNTS:
        balance = debit - credit
        for w, value in zip(
            widths, [account, name, money(debit), money(credit), money(balance)], strict=True
        ):
            pdf.cell(w, 5.2, value, align="L" if w in (18, 62) else "R")
        pdf.ln()
    SAMPLE.parent.mkdir(exist_ok=True)
    pdf.output(SAMPLE)
    return SAMPLE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", nargs="?", type=Path)
    parser.add_argument("-o", "--out", type=Path)
    parser.add_argument("--sample", action="store_true", help="generate and read the sample")
    args = parser.parse_args()
    if args.sample:
        args.pdf = make_sample()
    if args.pdf is None:
        parser.error("PDF is required (or --sample)")

    text = read_pdf(args.pdf.read_bytes())
    lines = parse_ledger(text.pages)
    if not lines:
        sys.exit("Žádné účty tříd 5 a 6 s částkami – jiný formát? Pošli PDF k úpravě parseru.")
    summary = summarize(lines)
    for category, amount in summary.costs.items():
        print(f"{category:>11}: {cz_number(amount)} Kč")
    print(f"{'výnosy':>11}: {cz_number(summary.revenues)} Kč")
    print(f"{'hrubý zisk':>11}: {cz_number(summary.gross_profit)} Kč (výnosy − variabilní)")
    print(f"Účtů: {len(lines)}, OCR stran: {text.ocr_pages or 'žádné'}")

    out = args.out or args.pdf.with_suffix(".xlsx")
    out.write_bytes(workbook(*table(lines)))
    print(f"Tabulka: {out}")


if __name__ == "__main__":
    main()
