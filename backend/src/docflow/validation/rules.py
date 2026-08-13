"""The rule catalogue.

Every rule is a small pure function registered under a stable id that document type
specs reference by name. Adding a rule to a document type is a one-line change to its
spec; adding a rule to the catalogue never touches the engine.

**Severity is a product decision, not a technical one.** The guiding question is
"would a human want to look at this before the data leaves the system?":

    ERROR    the data is definitely wrong or unusable — blocks approval
    WARNING  the data is suspicious — routes to review but can be approved as-is
    INFO     worth recording, does not affect routing

Tolerances follow the same logic. Rounding differences of a crown or two on a VAT
calculation are normal and must not create work; a total that is off by 15% is a
misread digit and must.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Any

from docflow.domain.enums import ValidationSeverity as Sev
from docflow.schemas.fields import (
    SUPPORTED_CURRENCIES,
    is_plausible_vat_id,
    is_valid_czech_account_number,
    is_valid_iban,
    is_valid_ico,
    normalize_currency,
    parse_date,
    parse_decimal,
)
from docflow.validation.engine import Issue, RuleContext, registry
from docflow.validation.paths import MISSING, expand, get_path

# --------------------------------------------------------------------------- utils

# Exact-match band: below this the figures agree.
EXACT_TOLERANCE = Decimal("0.02")
# Rounding band: differences here are normal VAT/line rounding, reported as INFO.
ROUNDING_ABSOLUTE = Decimal("1.00")
ROUNDING_RELATIVE = Decimal("0.005")
# Beyond this the numbers genuinely disagree.
ERROR_RELATIVE = Decimal("0.02")


def _dec(value: Any) -> Decimal | None:
    if value is None or value is MISSING:
        return None
    try:
        return parse_decimal(value)
    except Exception:
        return None


def _is_blank(value: Any) -> bool:
    if value is MISSING or value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, list | dict) and not value


def _amount_agreement(expected: Decimal, actual: Decimal) -> tuple[Sev | None, Decimal]:
    """Compare two money figures. Returns `(severity_or_None, absolute_difference)`."""
    diff = abs(expected - actual)
    if diff <= EXACT_TOLERANCE:
        return None, diff
    scale = max(abs(expected), abs(actual), Decimal("1"))
    if diff <= max(ROUNDING_ABSOLUTE, scale * ROUNDING_RELATIVE):
        return Sev.INFO, diff
    if diff <= scale * ERROR_RELATIVE:
        return Sev.WARNING, diff
    return Sev.ERROR, diff


def _fmt(value: Decimal) -> str:
    return f"{value:,.2f}"


# ------------------------------------------------------------------ required fields


@registry.register("required_fields")
def required_fields(ctx: RuleContext) -> Iterator[Issue]:
    """Every field the spec marks required must be present and non-empty.

    Nested required fields are conditional on their parent being present. `supplier.
    name` is required *if there is a supplier*; reporting it as missing on a document
    that has no supplier block at all would double-report the same gap and clutter
    the review queue.
    """
    for spec_field in ctx.spec.fields:
        if not spec_field.required:
            continue

        parent = _nearest_present_parent(ctx, spec_field.path)
        if parent is False:
            continue

        found_any = False
        for concrete_path, value in expand(ctx.data, spec_field.path):
            found_any = True
            if _is_blank(value):
                yield Issue(
                    rule_id="required_fields",
                    code="missing_required_field",
                    severity=Sev.ERROR,
                    message=f"{spec_field.label} is required but was not found",
                    field_path=concrete_path,
                    context={"label": spec_field.label},
                )
        if not found_any:
            yield Issue(
                rule_id="required_fields",
                code="missing_required_field",
                severity=Sev.ERROR,
                message=f"{spec_field.label} is required but was not found",
                field_path=spec_field.path,
                context={"label": spec_field.label},
            )


def _nearest_present_parent(ctx: RuleContext, path: str) -> bool:
    """False when an ancestor container is absent, so the child check should be skipped."""
    if "." not in path:
        return True
    parent = path.rsplit(".", 1)[0]
    if parent.endswith("[]"):
        parent = parent[:-2]
    if not parent:
        return True
    values = list(expand(ctx.data, parent))
    if not values:
        return False
    return any(not _is_blank(v) for _, v in values)


# ---------------------------------------------------------------------- arithmetic


def _totals_consistency(
    ctx: RuleContext,
    rule_id: str,
    *,
    subtotal_path: str = "subtotal",
    tax_path: str = "tax_amount",
    total_path: str = "total",
) -> Iterator[Issue]:
    subtotal = _dec(get_path(ctx.data, subtotal_path))
    tax = _dec(get_path(ctx.data, tax_path))
    total = _dec(get_path(ctx.data, total_path))

    if total is None:
        return
    if subtotal is None and tax is None:
        return

    if subtotal is not None and tax is not None:
        expected = subtotal + tax
        severity, diff = _amount_agreement(expected, total)
        if severity is not None:
            yield Issue(
                rule_id=rule_id,
                code="totals_mismatch",
                severity=severity,
                message=(
                    f"Total {_fmt(total)} does not match subtotal {_fmt(subtotal)} "
                    f"plus tax {_fmt(tax)} (= {_fmt(expected)}, off by {_fmt(diff)})"
                ),
                field_path=total_path,
                context={
                    "subtotal": str(subtotal),
                    "tax_amount": str(tax),
                    "total": str(total),
                    "expected_total": str(expected),
                    "difference": str(diff),
                },
            )
        return

    # Only one component present. A total that is *smaller* than its own net amount,
    # or a tax component larger than the gross, is definitely wrong regardless of the
    # missing figure.
    if subtotal is not None and subtotal > total + EXACT_TOLERANCE:
        yield Issue(
            rule_id=rule_id,
            code="subtotal_exceeds_total",
            severity=Sev.ERROR,
            message=f"Subtotal {_fmt(subtotal)} is greater than total {_fmt(total)}",
            field_path=subtotal_path,
        )
    if tax is not None and tax > total + EXACT_TOLERANCE:
        yield Issue(
            rule_id=rule_id,
            code="tax_exceeds_total",
            severity=Sev.ERROR,
            message=f"Tax {_fmt(tax)} is greater than total {_fmt(total)}",
            field_path=tax_path,
        )


@registry.register("invoice_totals_consistency")
def invoice_totals_consistency(ctx: RuleContext) -> Iterator[Issue]:
    yield from _totals_consistency(ctx, "invoice_totals_consistency")

    # The declared headline VAT rate should agree with the implied one.
    subtotal = _dec(get_path(ctx.data, "subtotal"))
    tax = _dec(get_path(ctx.data, "tax_amount"))
    declared_rate = _dec(get_path(ctx.data, "tax_rate"))
    if subtotal and tax is not None and declared_rate is not None and subtotal != 0:
        implied = (tax / subtotal) * Decimal("100")
        if abs(implied - declared_rate) > Decimal("1.0"):
            yield Issue(
                rule_id="invoice_totals_consistency",
                code="tax_rate_mismatch",
                severity=Sev.WARNING,
                message=(
                    f"Declared tax rate {declared_rate}% does not match the implied "
                    f"rate {implied:.1f}% (tax {_fmt(tax)} on subtotal {_fmt(subtotal)})"
                ),
                field_path="tax_rate",
                context={"declared": str(declared_rate), "implied": f"{implied:.2f}"},
            )

    amount_due = _dec(get_path(ctx.data, "amount_due"))
    total = _dec(get_path(ctx.data, "total"))
    if amount_due is not None and total is not None and amount_due > total + EXACT_TOLERANCE:
        yield Issue(
            rule_id="invoice_totals_consistency",
            code="amount_due_exceeds_total",
            severity=Sev.WARNING,
            message=f"Amount due {_fmt(amount_due)} is greater than the total {_fmt(total)}",
            field_path="amount_due",
        )


@registry.register("po_totals_consistency")
def po_totals_consistency(ctx: RuleContext) -> Iterator[Issue]:
    yield from _totals_consistency(ctx, "po_totals_consistency")


@registry.register("receipt_totals_consistency")
def receipt_totals_consistency(ctx: RuleContext) -> Iterator[Issue]:
    yield from _totals_consistency(ctx, "receipt_totals_consistency")


@registry.register("line_items_sum")
def line_items_sum(ctx: RuleContext) -> Iterator[Issue]:
    """Line totals should sum to the subtotal, and each line should be qty × price.

    This is the rule that most often catches a genuinely misread digit: a single
    wrong line total is invisible against the invoice total but obvious against its
    own quantity and unit price.
    """
    rows = get_path(ctx.data, "line_items")
    if not isinstance(rows, list) or not rows:
        return

    line_totals: list[Decimal] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        qty = _dec(row.get("quantity"))
        unit_price = _dec(row.get("unit_price"))
        line_total = _dec(row.get("line_total"))

        if line_total is not None:
            line_totals.append(line_total)

        if qty is not None and unit_price is not None and line_total is not None:
            expected = qty * unit_price
            severity, diff = _amount_agreement(expected, line_total)
            if severity in (Sev.WARNING, Sev.ERROR):
                yield Issue(
                    rule_id="line_items_sum",
                    code="line_total_mismatch",
                    severity=Sev.WARNING,
                    message=(
                        f"Line {index + 1}: {qty} × {_fmt(unit_price)} = "
                        f"{_fmt(expected)}, but the line total is {_fmt(line_total)}"
                    ),
                    field_path=f"line_items.{index}.line_total",
                    context={"expected": str(expected), "actual": str(line_total)},
                )

    subtotal = _dec(get_path(ctx.data, "subtotal"))
    if subtotal is None or len(line_totals) != len(rows) or not line_totals:
        # Only compare against the subtotal when every line contributed a figure;
        # a partial sum is guaranteed to disagree and would be a false alarm.
        return

    summed = sum(line_totals, Decimal("0"))
    severity, diff = _amount_agreement(summed, subtotal)
    if severity in (Sev.WARNING, Sev.ERROR):
        yield Issue(
            rule_id="line_items_sum",
            code="line_items_subtotal_mismatch",
            severity=severity,
            message=(
                f"Line items sum to {_fmt(summed)} but the subtotal is "
                f"{_fmt(subtotal)} (off by {_fmt(diff)})"
            ),
            field_path="subtotal",
            context={"line_sum": str(summed), "subtotal": str(subtotal)},
        )


@registry.register("positive_amounts")
def positive_amounts(ctx: RuleContext) -> Iterator[Issue]:
    """Money fields should not be negative.

    Credit notes legitimately carry negative totals, so this is a WARNING: a rule
    that blocks a real business document is worse than one that flags it.
    """
    money_paths = [f.path for f in ctx.spec.fields if f.kind.value in {"money", "number"}]
    for template in money_paths:
        for concrete_path, value in expand(ctx.data, template):
            amount = _dec(value)
            if amount is not None and amount < 0:
                yield Issue(
                    rule_id="positive_amounts",
                    code="negative_amount",
                    severity=Sev.WARNING,
                    message=f"{concrete_path} is negative ({_fmt(amount)})",
                    field_path=concrete_path,
                    context={"value": str(amount)},
                )


# ---------------------------------------------------------------------------- dates


def _date_at(ctx: RuleContext, path: str) -> dt.date | None:
    raw = get_path(ctx.data, path)
    if raw is MISSING or raw is None:
        return None
    return parse_date(raw, day_first=ctx.spec.day_first_dates)


def _date_order(
    ctx: RuleContext,
    rule_id: str,
    earlier_path: str,
    later_path: str,
    *,
    earlier_label: str,
    later_label: str,
    severity: Sev = Sev.ERROR,
) -> Iterator[Issue]:
    earlier = _date_at(ctx, earlier_path)
    later = _date_at(ctx, later_path)
    if earlier is None or later is None:
        return
    if later < earlier:
        yield Issue(
            rule_id=rule_id,
            code="date_order_violation",
            severity=severity,
            message=f"{later_label} ({later}) is before {earlier_label.lower()} ({earlier})",
            field_path=later_path,
            context={"earlier": earlier.isoformat(), "later": later.isoformat()},
        )


@registry.register("invoice_date_order")
def invoice_date_order(ctx: RuleContext) -> Iterator[Issue]:
    yield from _date_order(
        ctx,
        "invoice_date_order",
        "issue_date",
        "due_date",
        earlier_label="Issue date",
        later_label="Due date",
    )
    # A payment term beyond a year is nearly always a misparsed year digit.
    issue = _date_at(ctx, "issue_date")
    due = _date_at(ctx, "due_date")
    if issue and due and (due - issue).days > 365:
        yield Issue(
            rule_id="invoice_date_order",
            code="implausible_payment_term",
            severity=Sev.WARNING,
            message=f"Payment term of {(due - issue).days} days is unusually long",
            field_path="due_date",
            context={"days": (due - issue).days},
        )


@registry.register("po_date_order")
def po_date_order(ctx: RuleContext) -> Iterator[Issue]:
    yield from _date_order(
        ctx,
        "po_date_order",
        "order_date",
        "requested_delivery_date",
        earlier_label="Order date",
        later_label="Requested delivery date",
    )


@registry.register("contract_date_order")
def contract_date_order(ctx: RuleContext) -> Iterator[Issue]:
    yield from _date_order(
        ctx,
        "contract_date_order",
        "effective_date",
        "expiration_date",
        earlier_label="Effective date",
        later_label="Expiration date",
    )
    yield from _date_order(
        ctx,
        "contract_date_order",
        "signature_date",
        "expiration_date",
        earlier_label="Signature date",
        later_label="Expiration date",
        severity=Sev.WARNING,
    )

    # Cross-check the stated term length against the actual date span.
    effective = _date_at(ctx, "effective_date")
    expiration = _date_at(ctx, "expiration_date")
    term_months = _dec(get_path(ctx.data, "term_months"))
    if effective and expiration and term_months:
        span_months = Decimal((expiration - effective).days) / Decimal("30.44")
        if abs(span_months - term_months) > Decimal("1.5"):
            yield Issue(
                rule_id="contract_date_order",
                code="term_span_mismatch",
                severity=Sev.WARNING,
                message=(
                    f"Stated term of {term_months} months does not match the "
                    f"{span_months:.1f} months between the effective and expiration dates"
                ),
                field_path="term_months",
            )


@registry.register("date_sanity")
def date_sanity(ctx: RuleContext) -> Iterator[Issue]:
    """Catch dates that are impossible for a business document.

    Nearly always a misparsed year (`2202` for `2022`) or a page number picked up as
    a date. Cheap, deterministic and catches a whole class of silent corruption.
    """
    lower = dt.date(1990, 1, 1)
    upper = ctx.today + dt.timedelta(days=365 * 10)
    date_paths = [f.path for f in ctx.spec.fields if f.kind.value == "date"]
    for template in date_paths:
        for concrete_path, raw in expand(ctx.data, template):
            if raw is None:
                continue
            parsed = parse_date(raw, day_first=ctx.spec.day_first_dates)
            if parsed is None:
                yield Issue(
                    rule_id="date_sanity",
                    code="unparseable_date",
                    severity=Sev.ERROR,
                    message=f"{concrete_path} is not a recognisable date ({raw!r})",
                    field_path=concrete_path,
                )
                continue
            if parsed < lower or parsed > upper:
                yield Issue(
                    rule_id="date_sanity",
                    code="implausible_date",
                    severity=Sev.ERROR,
                    message=f"{concrete_path} ({parsed}) is outside a plausible range",
                    field_path=concrete_path,
                    context={"value": parsed.isoformat()},
                )


@registry.register("date_not_future")
def date_not_future(ctx: RuleContext) -> Iterator[Issue]:
    """A receipt cannot document a purchase that has not happened yet.

    One day of slack absorbs timezone differences between the till and our clock.
    """
    for path in ("purchase_date",):
        value = _date_at(ctx, path)
        if value and value > ctx.today + dt.timedelta(days=1):
            yield Issue(
                rule_id="date_not_future",
                code="future_date",
                severity=Sev.WARNING,
                message=f"{path} ({value}) is in the future",
                field_path=path,
            )


# ------------------------------------------------------------------------- currency


@registry.register("currency_supported")
def currency_supported(ctx: RuleContext) -> Iterator[Issue]:
    for template in (f.path for f in ctx.spec.fields if f.kind.value == "currency"):
        for concrete_path, raw in expand(ctx.data, template):
            if raw is None:
                continue
            code = normalize_currency(raw)
            if code is None:
                yield Issue(
                    rule_id="currency_supported",
                    code="unsupported_currency",
                    severity=Sev.ERROR,
                    message=(
                        f"{raw!r} is not a supported currency. Supported: "
                        f"{', '.join(sorted(SUPPORTED_CURRENCIES))}"
                    ),
                    field_path=concrete_path,
                    context={"value": str(raw)},
                )


# ------------------------------------------------------------------- identifiers


@registry.register("iban_checksum")
def iban_checksum(ctx: RuleContext) -> Iterator[Issue]:
    """An IBAN that fails MOD-97 is wrong — no judgement required.

    ERROR rather than WARNING: paying a supplier on a transposed account number is
    the most expensive mistake this system can make, and unlike a wrong date it is
    not obvious to the person approving it.
    """
    for path in ("bank_details.iban", "iban"):
        raw = get_path(ctx.data, path)
        if raw is MISSING or not raw:
            continue
        if not is_valid_iban(raw):
            yield Issue(
                rule_id="iban_checksum",
                code="invalid_iban",
                severity=Sev.ERROR,
                message=f"IBAN {raw!r} fails its checksum and cannot be correct",
                field_path=path,
            )


@registry.register("czech_account_checksum")
def czech_account_checksum(ctx: RuleContext) -> Iterator[Issue]:
    for path in ("bank_details.account_number", "account_number"):
        raw = get_path(ctx.data, path)
        if raw is MISSING or not raw:
            continue
        text = str(raw)
        if not re.search(r"/\d{4}\s*$", text):
            # Not in Czech domestic format; nothing to check here.
            continue
        if not is_valid_czech_account_number(text):
            yield Issue(
                rule_id="czech_account_checksum",
                code="invalid_account_number",
                severity=Sev.ERROR,
                message=f"Account number {raw!r} fails the ČNB checksum",
                field_path=path,
            )


@registry.register("ico_checksum")
def ico_checksum(ctx: RuleContext) -> Iterator[Issue]:
    for template in (
        "supplier.registration_id",
        "customer.registration_id",
        "buyer.registration_id",
        "parties[].registration_id",
    ):
        for concrete_path, raw in expand(ctx.data, template):
            if not raw:
                continue
            digits = re.sub(r"\D", "", str(raw))
            # Only apply the Czech checksum to 8-digit identifiers; a German or
            # Polish registration number is not an IČO and must not be flagged.
            if len(digits) != 8:
                continue
            if not is_valid_ico(digits):
                yield Issue(
                    rule_id="ico_checksum",
                    code="invalid_registration_id",
                    severity=Sev.WARNING,
                    message=f"Company ID {raw!r} fails the IČO checksum",
                    field_path=concrete_path,
                )


@registry.register("vat_id_format")
def vat_id_format(ctx: RuleContext) -> Iterator[Issue]:
    for template in ("supplier.vat_id", "customer.vat_id", "buyer.vat_id", "merchant_vat_id"):
        for concrete_path, raw in expand(ctx.data, template):
            if not raw:
                continue
            if not is_plausible_vat_id(raw):
                yield Issue(
                    rule_id="vat_id_format",
                    code="implausible_vat_id",
                    severity=Sev.WARNING,
                    message=f"VAT ID {raw!r} does not look like a valid VAT number",
                    field_path=concrete_path,
                )


@registry.register("variable_symbol_format")
def variable_symbol_format(ctx: RuleContext) -> Iterator[Issue]:
    """Czech variable symbol: up to 10 digits, nothing else.

    Banks silently drop non-numeric characters, so a variable symbol containing
    letters means the payment will not be matched by the recipient.
    """
    raw = get_path(ctx.data, "variable_symbol")
    if raw is MISSING or not raw:
        return
    text = str(raw).strip()
    if not text.isdigit():
        yield Issue(
            rule_id="variable_symbol_format",
            code="non_numeric_variable_symbol",
            severity=Sev.WARNING,
            message=f"Variable symbol {raw!r} contains non-numeric characters",
            field_path="variable_symbol",
        )
    elif len(text) > 10:
        yield Issue(
            rule_id="variable_symbol_format",
            code="variable_symbol_too_long",
            severity=Sev.WARNING,
            message=f"Variable symbol {raw!r} is longer than the 10-digit maximum",
            field_path="variable_symbol",
        )


# ------------------------------------------------------------------------ contracts


@registry.register("contract_parties_min")
def contract_parties_min(ctx: RuleContext) -> Iterator[Issue]:
    parties = get_path(ctx.data, "parties")
    count = len(parties) if isinstance(parties, list) else 0
    if count < 2:
        yield Issue(
            rule_id="contract_parties_min",
            code="insufficient_parties",
            severity=Sev.ERROR,
            message=f"A contract needs at least two parties; {count} were extracted",
            field_path="parties",
            context={"count": count},
        )


@registry.register("contract_renewal_consistency")
def contract_renewal_consistency(ctx: RuleContext) -> Iterator[Issue]:
    """An auto-renewing contract without a notice period is the expensive case.

    If we tell a customer their contract auto-renews but cannot tell them by when
    they must act, we have given them anxiety instead of information.
    """
    auto_renewal = get_path(ctx.data, "auto_renewal")
    notice = _dec(get_path(ctx.data, "notice_period_days"))

    if auto_renewal is True and notice is None:
        yield Issue(
            rule_id="contract_renewal_consistency",
            code="missing_notice_period",
            severity=Sev.WARNING,
            message=(
                "The contract is marked as auto-renewing but no notice period was "
                "found. Confirm the deadline for giving notice."
            ),
            field_path="notice_period_days",
        )
    if notice is not None and (notice < 0 or notice > 730):
        yield Issue(
            rule_id="contract_renewal_consistency",
            code="implausible_notice_period",
            severity=Sev.WARNING,
            message=f"Notice period of {notice} days is outside the plausible range",
            field_path="notice_period_days",
        )
    if auto_renewal is False and notice is not None and notice > 0:
        yield Issue(
            rule_id="contract_renewal_consistency",
            code="notice_without_renewal",
            severity=Sev.INFO,
            message="A notice period was found although the contract is not marked auto-renewing",
            field_path="auto_renewal",
        )


def all_rule_ids() -> list[str]:
    return registry.ids()


def _unused() -> Iterable[Any]:  # pragma: no cover - keeps imports honest
    return ()
