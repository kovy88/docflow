from app.ledger import FIXED, REVIEW, VARIABLE, classify, parse_ledger, summarize

TRIAL_BALANCE = """OBRATOVÁ PŘEDVAHA za období 1–12/2025
Účet Název účtu Obrat MD Obrat D Konečný stav
501100 Spotřeba materiálu 4 812 340,50 12 400,00 4 799 940,50
518100 Nájemné 1 440 000,00 0,00 1 440 000,00
521100 Mzdové náklady 6.120.000,00 0,00 6.120.000,00
601100 Tržby za vlastní výrobky 0,00 21 850 000,00 -21 850 000,00
311000 Odběratelé 900 000,00 0,00 900 000,00"""

GENERAL_LEDGER = [
    """Účet 551100 Odpisy dlouhodobého majetku
31.01.2025 ID 25001 Odpis stroje 115 000,00
551100 Odpisy dlouhodobého majetku""",
    """28.02.2025 ID 25002 Odpis stroje 115 000,00
Celkem za účet 551100 230 000,00 0,00 230 000,00
562 Úroky 41 000,00
562100 Úroky z úvěru 41 000,00""",
]


def test_trial_balance_takes_closing_balance_of_cost_and_revenue_accounts():
    lines = parse_ledger([TRIAL_BALANCE])
    assert [(a.account, a.amount) for a in lines] == [
        ("501100", 4_799_940.50),
        ("518100", 1_440_000.00),
        ("521100", 6_120_000.00),
        ("601100", -21_850_000.00),
    ]
    assert lines[0].name == "Spotřeba materiálu"
    assert lines[0].line.startswith("501100 Spotřeba")


def test_general_ledger_keeps_summary_line_and_drops_synthetic_duplicate():
    lines = parse_ledger(GENERAL_LEDGER)
    by_account = {a.account: a for a in lines}
    assert set(by_account) == {"551100", "562100"}
    assert by_account["551100"].amount == 230_000.00
    assert by_account["551100"].page == 2
    assert by_account["551100"].name == "Odpisy dlouhodobého majetku"


def test_classification_and_summary():
    assert classify("501100")[0] == VARIABLE
    assert classify("521300")[0] == FIXED
    assert classify("518200")[0] == REVIEW
    summary = summarize(parse_ledger([TRIAL_BALANCE]))
    assert summary.costs[FIXED] == 6_120_000.00
    assert summary.costs[REVIEW] == 1_440_000.00
    assert summary.revenues == 21_850_000.00
    assert summary.gross_profit == 21_850_000.00 - 4_799_940.50
