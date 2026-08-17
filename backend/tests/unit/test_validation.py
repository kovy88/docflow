"""Validation engine and rule catalogue.

These are the tests that matter most for trust in the product: they encode what
"the data is wrong" means. They run in milliseconds because the validation layer
performs no I/O.
"""

from __future__ import annotations

import datetime as dt
from typing import ClassVar

import pytest

from docflow.domain.enums import ValidationSeverity
from docflow.schemas.fields import (
    is_plausible_vat_id,
    is_valid_czech_account_number,
    is_valid_iban,
    is_valid_ico,
    normalize_currency,
    parse_date,
    parse_date_detailed,
    parse_decimal,
)
from docflow.schemas.registry import get_registry
from docflow.validation.engine import RuleContext, ValidationEngine, validate_syntax

engine = ValidationEngine()


def validate(data: dict, type_key: str = "invoice", *, today: dt.date | None = None):
    spec = get_registry().resolve(type_key)
    normalized, syntax_issues = validate_syntax(spec, data)
    if normalized is None:
        return syntax_issues
    ctx = RuleContext(data=normalized, spec=spec, today=today or dt.date(2024, 6, 1))
    return [*syntax_issues, *engine.validate(ctx).issues]


def codes(issues, severity: ValidationSeverity | None = None) -> set[str]:
    return {i.code for i in issues if severity is None or i.severity is severity}


VALID_INVOICE = {
    "invoice_number": "2024-0412",
    "supplier": {"name": "ACME s.r.o.", "registration_id": "27074358", "vat_id": "CZ27074358"},
    "customer": {"name": "Beta Ltd"},
    "issue_date": "2024-03-14",
    "due_date": "2024-03-28",
    "currency": "CZK",
    "subtotal": "33000.00",
    "tax_amount": "6930.00",
    "tax_rate": "21",
    "total": "39930.00",
    "variable_symbol": "20240412",
    "bank_details": {
        "iban": "CZ6508000000192000145399",
        "account_number": "19-2000145399/0800",
    },
    "line_items": [
        {
            "description": "Consulting",
            "quantity": "10",
            "unit_price": "2500.00",
            "line_total": "25000.00",
        },
        {
            "description": "Licence",
            "quantity": "1",
            "unit_price": "8000.00",
            "line_total": "8000.00",
        },
    ],
}


class TestValidInvoice:
    def test_clean_invoice_has_no_issues(self):
        assert validate(VALID_INVOICE) == []

    def test_line_items_summing_to_subtotal_pass(self):
        issues = validate(VALID_INVOICE)
        assert "line_items_subtotal_mismatch" not in codes(issues)


class TestArithmetic:
    def test_total_not_matching_subtotal_plus_tax_is_an_error(self):
        bad = {**VALID_INVOICE, "total": "50000.00"}
        assert "totals_mismatch" in codes(validate(bad), ValidationSeverity.ERROR)

    def test_rounding_difference_is_tolerated(self):
        """A one-crown rounding difference is normal, not an error."""
        rounded = {**VALID_INVOICE, "total": "39930.50"}
        assert "totals_mismatch" not in codes(validate(rounded), ValidationSeverity.ERROR)

    def test_subtotal_greater_than_total_is_an_error(self):
        bad = {**VALID_INVOICE, "tax_amount": None, "subtotal": "50000.00"}
        assert "subtotal_exceeds_total" in codes(validate(bad), ValidationSeverity.ERROR)

    def test_line_total_not_matching_quantity_times_price_is_flagged(self):
        bad = {
            **VALID_INVOICE,
            "line_items": [
                {
                    "description": "X",
                    "quantity": "10",
                    "unit_price": "100.00",
                    "line_total": "5000.00",
                }
            ],
        }
        assert "line_total_mismatch" in codes(validate(bad))

    def test_declared_tax_rate_inconsistent_with_amounts_is_flagged(self):
        bad = {**VALID_INVOICE, "tax_rate": "15"}
        assert "tax_rate_mismatch" in codes(validate(bad))

    def test_partial_line_items_do_not_trigger_subtotal_comparison(self):
        """A sum over incomplete lines is guaranteed wrong; it must not be reported."""
        partial = {
            **VALID_INVOICE,
            "line_items": [
                {"description": "A", "line_total": "25000.00"},
                {"description": "B"},  # no line_total
            ],
        }
        assert "line_items_subtotal_mismatch" not in codes(validate(partial))


class TestDates:
    def test_due_before_issue_is_an_error(self):
        bad = {**VALID_INVOICE, "due_date": "2024-03-01"}
        assert "date_order_violation" in codes(validate(bad), ValidationSeverity.ERROR)

    def test_implausible_year_is_an_error(self):
        bad = {**VALID_INVOICE, "issue_date": "2202-03-14", "due_date": "2202-03-28"}
        assert "implausible_date" in codes(validate(bad), ValidationSeverity.ERROR)

    def test_very_long_payment_term_is_a_warning(self):
        bad = {**VALID_INVOICE, "due_date": "2025-12-31"}
        assert "implausible_payment_term" in codes(validate(bad), ValidationSeverity.WARNING)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2024-03-14", dt.date(2024, 3, 14)),
            ("14.03.2024", dt.date(2024, 3, 14)),
            ("14/03/2024", dt.date(2024, 3, 14)),
            ("31.12.2023", dt.date(2023, 12, 31)),
            ("20240314", dt.date(2024, 3, 14)),
            ("14 March 2024", dt.date(2024, 3, 14)),
            ("1.2.24", dt.date(2024, 2, 1)),
        ],
    )
    def test_date_formats(self, text, expected):
        assert parse_date(text) == expected

    def test_unambiguous_day_wins_regardless_of_locale(self):
        """25/03 can only be day-first, so day_first=False must not flip it."""
        assert parse_date("25/03/2024", day_first=False) == dt.date(2024, 3, 25)

    def test_ambiguous_date_is_reported_as_ambiguous(self):
        result = parse_date_detailed("03/04/2024")
        assert result.value == dt.date(2024, 4, 3)
        assert result.ambiguous is True

    def test_iso_date_is_not_fuzzy(self):
        assert parse_date_detailed("2024-03-14").was_fuzzy is False


class TestChecksums:
    @pytest.mark.parametrize(
        "iban",
        [
            "CZ6508000000192000145399",
            "GB82 WEST 1234 5698 7654 32",
            "DE89370400440532013000",
            "SK3112000000198742637541",
        ],
    )
    def test_valid_ibans_pass(self, iban):
        assert is_valid_iban(iban) is True

    @pytest.mark.parametrize(
        "iban",
        [
            "CZ6508000000192000145398",  # last digit changed
            "CZ6408000000192000145399",  # check digits changed
            "GB82WEST12345698765433",
            "NOT-AN-IBAN",
            "",
        ],
    )
    def test_invalid_ibans_fail(self, iban):
        assert is_valid_iban(iban) is False

    def test_transposed_iban_digits_are_caught(self):
        """The failure mode this rule exists for: two digits swapped."""
        assert is_valid_iban("CZ6508000000192000145939") is False

    def test_invalid_iban_is_an_error_not_a_warning(self):
        bad = {**VALID_INVOICE, "bank_details": {"iban": "CZ6508000000192000145398"}}
        assert "invalid_iban" in codes(validate(bad), ValidationSeverity.ERROR)

    @pytest.mark.parametrize("ico", ["27074358", "45274649", "00006947"])
    def test_valid_ico_passes(self, ico):
        assert is_valid_ico(ico) is True

    @pytest.mark.parametrize("ico", ["27074359", "12345678", "1234567"])
    def test_invalid_ico_fails(self, ico):
        assert is_valid_ico(ico) is False

    @pytest.mark.parametrize(
        "account", ["19-2000145399/0800", "2000145399/0800", "000019-2000145399/0800"]
    )
    def test_valid_czech_accounts_pass(self, account):
        assert is_valid_czech_account_number(account) is True

    def test_invalid_czech_account_fails(self):
        assert is_valid_czech_account_number("19-2000145398/0800") is False

    def test_non_czech_account_format_is_not_checked(self):
        """A German account must not be run through the ČNB algorithm."""
        data = {**VALID_INVOICE, "bank_details": {"account_number": "1234567890"}}
        assert "invalid_account_number" not in codes(validate(data))

    def test_non_eight_digit_registration_id_skips_ico_check(self):
        data = {**VALID_INVOICE, "supplier": {"name": "GmbH", "registration_id": "HRB 12345"}}
        assert "invalid_registration_id" not in codes(validate(data))


class TestCurrency:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("CZK", "CZK"),
            ("Kč", "CZK"),
            ("kc", "CZK"),
            ("€", "EUR"),
            ("EURO", "EUR"),
            ("$", "USD"),
            ("usd", "USD"),
            ("zł", "PLN"),
        ],
    )
    def test_currency_normalisation(self, raw, expected):
        assert normalize_currency(raw) == expected

    def test_unsupported_currency_is_an_error(self):
        bad = {**VALID_INVOICE, "currency": "Bitcoin"}
        assert "unsupported_currency" in codes(validate(bad), ValidationSeverity.ERROR)


class TestRequiredFields:
    def test_missing_required_field_is_an_error(self):
        bad = {k: v for k, v in VALID_INVOICE.items() if k != "invoice_number"}
        assert "missing_required_field" in codes(validate(bad), ValidationSeverity.ERROR)

    def test_nested_required_is_skipped_when_parent_absent(self):
        """`customer.name` is required only if there is a customer at all."""
        without_customer = {k: v for k, v in VALID_INVOICE.items() if k != "customer"}
        issues = [
            i
            for i in validate(without_customer)
            if i.code == "missing_required_field" and (i.field_path or "").startswith("customer")
        ]
        assert issues == []

    def test_nested_required_is_enforced_when_parent_present(self):
        partial = {**VALID_INVOICE, "customer": {"address": "Praha"}}
        paths = {i.field_path for i in validate(partial) if i.code == "missing_required_field"}
        assert "customer.name" in paths


class TestVariableSymbol:
    def test_non_numeric_variable_symbol_is_flagged(self):
        bad = {**VALID_INVOICE, "variable_symbol": "ABC123"}
        assert "non_numeric_variable_symbol" in codes(validate(bad))

    def test_over_long_variable_symbol_is_flagged(self):
        bad = {**VALID_INVOICE, "variable_symbol": "123456789012"}
        assert "variable_symbol_too_long" in codes(validate(bad))


class TestMoneyParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1234.56", "1234.56"),
            ("1,234.56", "1234.56"),
            ("1 234,56", "1234.56"),
            ("1.234,56", "1234.56"),
            ("1234,56", "1234.56"),
            ("€ 1.234,56", "1234.56"),
            ("1 234,56 Kč", "1234.56"),
            ("(1 234,56)", "-1234.56"),
            ("-1234.56", "-1234.56"),
            ("1234", "1234"),
            ("1,234", "1234"),
        ],
    )
    def test_locale_tolerant_money_parsing(self, raw, expected):
        from decimal import Decimal

        assert parse_decimal(raw) == Decimal(expected)

    def test_thousands_separated_millions(self):
        from decimal import Decimal

        assert parse_decimal("1.234.567,89") == Decimal("1234567.89")
        assert parse_decimal("1,234,567.89") == Decimal("1234567.89")


class TestBaselineAmountExtraction:
    """Regression coverage for the rule-based extractor's label-anchored amounts.

    Found via `docflow-calibrate`: a non-breaking space thousands separator
    (`\xa0`, what Czech documents actually use — pdfplumber preserves it
    verbatim) isn't in `AMOUNT_RE`'s character class, so it split `78\xa0287,00`
    into two regex matches (`78`, `287,00`); "take the last figure on the line"
    then silently kept only the tail, worth 287.00 instead of 78287.00. This
    extractor backs both the `fixture` provider and the production baseline
    cross-check confidence signal (`docflow.domain.confidence`), so the bug
    wasn't confined to eval numbers — it also affected the corroboration signal
    real LLM extractions get on the majority-Czech corpus this product targets.
    """

    def test_non_breaking_space_thousands_separator_is_not_truncated(self):
        from docflow.extraction.baseline import extract_baseline

        text = "Objednávka\nCelkem: 78\xa0287,00 CZK\n"
        result = extract_baseline(text, "purchase_order")
        assert result.data["total"] == "78287.00"

    def test_multiple_non_breaking_space_groups_in_one_amount(self):
        from docflow.extraction.baseline import extract_baseline

        text = "Smlouva\nHodnota smlouvy: 1\xa0060\xa0000 CZK\n"
        result = extract_baseline(text, "contract")
        assert result.data["total_value"] == "1060000"


class TestContractRules:
    VALID_CONTRACT: ClassVar[dict] = {
        "title": "Service Agreement",
        "contract_type": "service_agreement",
        "parties": [{"name": "ACME"}, {"name": "Beta"}],
        "effective_date": "2024-02-01",
        "expiration_date": "2025-01-31",
        "auto_renewal": True,
        "notice_period_days": "90",
        "term_months": "12",
    }

    def test_valid_contract_passes(self):
        assert validate(self.VALID_CONTRACT, "contract") == []

    def test_single_party_is_an_error(self):
        bad = {**self.VALID_CONTRACT, "parties": [{"name": "ACME"}]}
        assert "insufficient_parties" in codes(validate(bad, "contract"), ValidationSeverity.ERROR)

    def test_auto_renewal_without_notice_period_warns(self):
        bad = {**self.VALID_CONTRACT, "notice_period_days": None}
        assert "missing_notice_period" in codes(validate(bad, "contract"))

    def test_expiry_before_effective_is_an_error(self):
        bad = {**self.VALID_CONTRACT, "expiration_date": "2023-01-01"}
        assert "date_order_violation" in codes(validate(bad, "contract"), ValidationSeverity.ERROR)

    def test_term_months_inconsistent_with_dates_warns(self):
        bad = {**self.VALID_CONTRACT, "term_months": "36"}
        assert "term_span_mismatch" in codes(validate(bad, "contract"))


class TestEngineRobustness:
    def test_a_raising_rule_does_not_fail_the_document(self):
        """One broken rule must not take down eleven working ones."""
        from docflow.validation.engine import Issue, RuleRegistry

        registry = RuleRegistry()

        @registry.register("explodes")
        def _explodes(ctx):
            raise RuntimeError("boom")

        @registry.register("works")
        def _works(ctx):
            return [
                Issue(
                    rule_id="works",
                    code="ok",
                    severity=ValidationSeverity.INFO,
                    message="fine",
                )
            ]

        from dataclasses import replace

        spec = get_registry().resolve("invoice")
        spec = replace(spec, rule_ids=("explodes", "works"))

        result = ValidationEngine(registry).validate(RuleContext(data={}, spec=spec))
        assert "rule_execution_failed" in {i.code for i in result.issues}
        assert "ok" in {i.code for i in result.issues}

    def test_unknown_rule_id_is_reported_not_crashed(self):
        from dataclasses import replace

        spec = replace(get_registry().resolve("invoice"), rule_ids=("no_such_rule",))
        result = engine.validate(RuleContext(data={}, spec=spec))
        assert "rule_not_found" in {i.code for i in result.issues}


class TestVatId:
    @pytest.mark.parametrize("vat", ["CZ27074358", "SK1234567890", "DE123456789"])
    def test_plausible_vat_ids(self, vat):
        assert is_plausible_vat_id(vat) is True

    @pytest.mark.parametrize("vat", ["CZ123", "12345678", ""])
    def test_implausible_vat_ids(self, vat):
        assert is_plausible_vat_id(vat) is False
