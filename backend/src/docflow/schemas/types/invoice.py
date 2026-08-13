"""Invoice document type.

Field selection is driven by what an accounts-payable clerk actually retypes into
their accounting system, not by what is technically present on the page. Anything
that does not save keystrokes is noise that costs tokens and adds a field to review.

The Czech-specific fields (`variable_symbol`, `registration_id`/IČO) are first-class
rather than stuffed into a generic `metadata` bag: in the target market a payment
without the right variable symbol is an unmatched payment, which is precisely the
manual work this product exists to remove.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from docflow.schemas.base import (
    ClassificationHints,
    DocumentTypeSpec,
    FieldKind,
    derive_field_specs,
    spec_field,
)
from docflow.schemas.fields import CleanStr, CurrencyCode, FlexibleDate, Money


class Party(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Name", FieldKind.STRING, required=True, hint="Legal entity name"
        ),
    )
    address: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Address", FieldKind.TEXT)
    )
    vat_id: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "VAT ID",
            FieldKind.IDENTIFIER,
            hint="VAT/tax registration number, e.g. DIČ CZ12345678",
        ),
    )
    registration_id: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Company ID",
            FieldKind.IDENTIFIER,
            hint="Company registration number, e.g. IČO 12345678",
        ),
    )
    country: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Country", FieldKind.STRING)
    )


class BankDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iban: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field("IBAN", FieldKind.BANK_ACCOUNT, critical=True),
    )
    account_number: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Account number",
            FieldKind.BANK_ACCOUNT,
            critical=True,
            hint="Domestic account number including bank code, e.g. 19-2000145399/0800",
        ),
    )
    swift_bic: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("SWIFT/BIC", FieldKind.IDENTIFIER)
    )
    bank_name: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Bank", FieldKind.STRING)
    )


class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Description", FieldKind.STRING, required=True)
    )
    quantity: Money | None = Field(
        default=None, json_schema_extra=spec_field("Qty", FieldKind.NUMBER)
    )
    unit: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Unit", FieldKind.STRING)
    )
    unit_price: Money | None = Field(
        default=None, json_schema_extra=spec_field("Unit price", FieldKind.MONEY)
    )
    tax_rate: Money | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Tax rate", FieldKind.NUMBER, hint="Percentage, e.g. 21 for 21% VAT"
        ),
    )
    line_total: Money | None = Field(
        default=None, json_schema_extra=spec_field("Line total", FieldKind.MONEY)
    )


class Invoice(BaseModel):
    """Structured invoice.

    `extra="forbid"` is load-bearing: it turns a hallucinated field name into a
    schema validation error we can see and count, instead of a silent extra key that
    flows into an export and confuses a downstream system.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_number: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Invoice number",
            FieldKind.IDENTIFIER,
            required=True,
            critical=True,
            hint="The supplier's invoice identifier, exactly as printed",
        ),
    )
    supplier: Party | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Supplier", FieldKind.OBJECT, required=True, hint="Who issued the invoice"
        ),
    )
    customer: Party | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Customer", FieldKind.OBJECT, hint="Who is being billed"
        ),
    )

    issue_date: FlexibleDate | None = Field(
        default=None,
        json_schema_extra=spec_field("Issue date", FieldKind.DATE, required=True),
    )
    due_date: FlexibleDate | None = Field(
        default=None,
        json_schema_extra=spec_field("Due date", FieldKind.DATE, critical=True),
    )
    tax_point_date: FlexibleDate | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Tax point date", FieldKind.DATE, hint="Date of taxable supply (DUZP)"
        ),
    )

    currency: CurrencyCode | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Currency", FieldKind.CURRENCY, required=True, hint="ISO 4217 code"
        ),
    )
    subtotal: Money | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Subtotal", FieldKind.MONEY, hint="Net amount excluding tax"
        ),
    )
    tax_amount: Money | None = Field(
        default=None, json_schema_extra=spec_field("Tax amount", FieldKind.MONEY)
    )
    tax_rate: Money | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Tax rate", FieldKind.NUMBER, hint="Headline percentage if a single rate applies"
        ),
    )
    total: Money | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Total", FieldKind.MONEY, required=True, critical=True,
            hint="Gross amount payable including tax",
        ),
    )
    amount_due: Money | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Amount due", FieldKind.MONEY,
            hint="Outstanding amount if it differs from the total (deposits, credits)",
        ),
    )

    bank_details: BankDetails | None = Field(
        default=None, json_schema_extra=spec_field("Bank details", FieldKind.OBJECT)
    )
    variable_symbol: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Variable symbol",
            FieldKind.IDENTIFIER,
            critical=True,
            hint="Czech payment reference (variabilní symbol)",
        ),
    )
    payment_terms: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Payment terms", FieldKind.STRING)
    )
    purchase_order_number: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("PO number", FieldKind.IDENTIFIER)
    )

    line_items: list[InvoiceLineItem] = Field(
        default_factory=list, json_schema_extra=spec_field("Line items", FieldKind.LIST)
    )
    notes: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Notes", FieldKind.TEXT, groundable=False)
    )


INVOICE_SPEC = DocumentTypeSpec(
    key="invoice",
    name="Invoice",
    description="A supplier invoice or tax document requesting payment.",
    model=Invoice,
    version=1,
    fields=derive_field_specs(Invoice),
    classification=ClassificationHints(
        keywords={
            "invoice": 3.0,
            "faktura": 3.0,
            "tax invoice": 3.0,
            "daňový doklad": 3.0,
            "rechnung": 2.5,
            "amount due": 1.5,
            "total due": 1.5,
            "vat": 1.0,
            "dph": 1.2,
            "variabilní symbol": 2.0,
            "variable symbol": 1.8,
            "bill to": 1.0,
            "payment due": 1.2,
            "iban": 0.8,
        },
        patterns={
            r"invoice\s*(no|number|#|č)": 2.5,
            r"faktura\s*(č|číslo)": 2.5,
            r"due\s+date": 1.5,
            r"splatnost": 1.8,
        },
        negative_keywords={
            "purchase order": 2.0,
            "objednávka": 2.0,
            "this agreement": 2.5,
            "smlouva": 2.0,
        },
    ),
    rule_ids=(
        "required_fields",
        "invoice_totals_consistency",
        "invoice_date_order",
        "currency_supported",
        "iban_checksum",
        "czech_account_checksum",
        "ico_checksum",
        "vat_id_format",
        "line_items_sum",
        "positive_amounts",
        "date_sanity",
        "variable_symbol_format",
    ),
    day_first_dates=True,
    review_threshold=0.85,
    critical_field_threshold=0.92,
    extraction_guidance=(
        "Amounts: return numbers only, without currency symbols or thousands "
        "separators. Preserve the document's decimal value exactly; do not round, "
        "recompute or 'fix' figures that look inconsistent — inconsistencies are "
        "detected downstream and are themselves a useful signal.\n"
        "If the invoice shows several tax rates, leave the top-level `tax_rate` null "
        "and record the per-rate figures on the line items.\n"
        "`variable_symbol` is the Czech payment reference, usually labelled "
        "'variabilní symbol' or 'VS'. It is not the invoice number, although the two "
        "are often equal."
    ),
)
