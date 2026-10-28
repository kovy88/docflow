"""Cost accounts from a Czech general ledger or trial balance (hlavní kniha, obratová
předvaha) for business interruption insurance: which costs keep running when operations stop.

Deterministic on purpose: every amount is read from one line of the PDF and keeps its page,
so a broker can check it. The fixed/variable split is a proposal by synthetic account of the
Czech chart of accounts; the broker decides, especially for "posoudit"."""

import re
from dataclasses import dataclass

from app.checks import parse_number

FIXED = "fixní"
VARIABLE = "variabilní"
REVIEW = "posoudit"
EXCLUDED = "mimo"

# Synthetic account (or its two-digit group) → (category, reason). Longest prefix wins.
RULES: dict[str, tuple[str, str]] = {
    "501": (VARIABLE, "spotřeba materiálu roste s výrobou"),
    "502": (REVIEW, "energie: část běží i bez provozu (vytápění, osvětlení)"),
    "503": (VARIABLE, "ostatní neskladovatelné dodávky"),
    "504": (VARIABLE, "prodané zboží"),
    "51": (FIXED, "služby"),
    "511": (FIXED, "opravy a udržování"),
    "512": (FIXED, "cestovné"),
    "513": (FIXED, "reprezentace"),
    "518": (REVIEW, "ostatní služby: nájem a IT běží dál, subdodávky ne"),
    "52": (FIXED, "osobní náklady"),
    "53": (FIXED, "daně a poplatky"),
    "54": (REVIEW, "ostatní provozní náklady"),
    "541": (EXCLUDED, "zůstatková cena prodaného majetku – jednorázové"),
    "542": (EXCLUDED, "prodaný materiál"),
    "543": (EXCLUDED, "dary"),
    "544": (EXCLUDED, "smluvní pokuty a úroky z prodlení"),
    "546": (EXCLUDED, "odpis pohledávky"),
    "548": (FIXED, "ostatní provozní náklady (pojištění, členské příspěvky)"),
    "549": (EXCLUDED, "manka a škody"),
    "55": (REVIEW, "rezervy a opravné položky"),
    "551": (FIXED, "odpisy"),
    "56": (REVIEW, "finanční náklady"),
    "562": (FIXED, "úroky"),
    "563": (EXCLUDED, "kurzové ztráty"),
    "568": (FIXED, "bankovní poplatky"),
    "59": (EXCLUDED, "daň z příjmů a převodové účty"),
}

# "518100", "518.100", "518 100" or "518" at the start of a line, optionally after a
# summary label ("Celkem za účet 518100").
ACCOUNT = re.compile(
    r"^\s*(?:(?:celkem\s+(?:za\s+)?)?účet\s+|celkem\s+)?([56]\d{2})(?:[ .]?(\d{1,3}))?(?![\d,])\s+(.*)$",
    re.IGNORECASE,
)
# Ledger amounts carry two decimals: "12 345,67", "12.345,67", "-1 234,00".
AMOUNT = re.compile(r"-?\d{1,3}(?:[  .]\d{3})*,\d{2}(?!\d)")


@dataclass
class AccountLine:
    account: str  # "518100" or "518"
    name: str
    amount: float  # last amount on the line: closing balance / turnover of the period
    page: int  # 1-based
    line: str  # verbatim source line

    @property
    def synthetic(self) -> str:
        return self.account[:3]


def classify(account: str) -> tuple[str, str]:
    for length in (3, 2):
        if account[:length] in RULES:
            return RULES[account[:length]]
    return REVIEW, "neznámý účet"


def parse_ledger(pages: list[str]) -> list[AccountLine]:
    """Account lines of classes 5 (costs) and 6 (revenues) that carry amounts. When an
    account appears more than once (transactions, then a summary line), the last line
    wins: ledgers print the account total after its movements."""
    found: dict[str, AccountLine] = {}
    names: dict[str, str] = {}  # from account headers, for summary lines without a name
    for page_no, text in enumerate(pages, start=1):
        for raw in text.splitlines():
            match = ACCOUNT.match(raw)
            if match is None:
                continue
            account = match[1] + (match[2] or "")
            rest = match[3]
            amounts = AMOUNT.findall(rest)
            name = (rest[: rest.find(amounts[0])] if amounts else rest).strip(" .:-")
            if name:
                names.setdefault(account, name)
            if not amounts:
                continue
            found.pop(account, None)
            found[account] = AccountLine(
                account=account,
                name=name or names.get(account, ""),
                amount=parse_number(amounts[-1]),
                page=page_no,
                line=raw.strip(),
            )
    # A synthetic total ("518  Ostatní služby  …") next to its analytic accounts would
    # count twice; keep the analytic ones.
    analytic = {a.synthetic for a in found.values() if len(a.account) > 3}
    lines = [a for a in found.values() if len(a.account) > 3 or a.account not in analytic]
    return sorted(lines, key=lambda a: a.account)


@dataclass
class Summary:
    revenues: float
    costs: dict[str, float]  # category → amount

    @property
    def gross_profit(self) -> float:
        """Revenues minus variable costs: the usual base of the insured amount for
        business interruption (fixed costs + profit)."""
        return self.revenues - self.costs.get(VARIABLE, 0.0)


def summarize(lines: list[AccountLine]) -> Summary:
    costs = {FIXED: 0.0, VARIABLE: 0.0, REVIEW: 0.0, EXCLUDED: 0.0}
    revenues = 0.0
    for a in lines:
        if a.account.startswith("6"):
            revenues += abs(a.amount)
        else:
            costs[classify(a.account)[0]] += a.amount
    return Summary(revenues=revenues, costs=costs)


def table(lines: list[AccountLine]) -> tuple[list[str], list[list[object]]]:
    headers = ["Účet", "Název", "Částka", "Návrh", "Důvod", "Strana", "Řádek v PDF"]
    rows: list[list[object]] = []
    for a in lines:
        category, reason = (
            ("výnos", "třída 6") if a.account.startswith("6") else classify(a.account)
        )
        rows.append([a.account, a.name, a.amount, category, reason, a.page, a.line])
    return headers, rows
