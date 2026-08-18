"""Receipt document type.

Receipts are the OCR-heavy case: thermal-printed, photographed at an angle, often the
worst text quality in the corpus. They are included precisely because they stress the
parts of the pipeline that clean PDFs never touch — OCR routing, low-confidence
handling and review ergonomics.

The schema is deliberately smaller than the invoice schema. Expense reporting needs
merchant, date, total and tax; asking the model for a full party record off a crumpled
receipt manufactures low-confidence fields that a human then has to dismiss.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from docflow.schemas.base import (
    ClassificationHints,
    DocumentTypeSpec,
    FieldKind,
    derive_field_specs,
    spec_field,
)
from docflow.schemas.fields import CleanStr, CurrencyCode, FlexibleDate, Money


class ReceiptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Item", FieldKind.STRING)
    )
    quantity: Money | None = Field(
        default=None, json_schema_extra=spec_field("Qty", FieldKind.NUMBER)
    )
    price: Money | None = Field(
        default=None, json_schema_extra=spec_field("Price", FieldKind.MONEY)
    )


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_name: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field("Merchant", FieldKind.STRING, required=True, critical=True),
    )
    merchant_vat_id: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Merchant VAT ID", FieldKind.IDENTIFIER)
    )
    receipt_number: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Receipt number", FieldKind.IDENTIFIER)
    )

    purchase_date: FlexibleDate | None = Field(
        default=None,
        json_schema_extra=spec_field("Date", FieldKind.DATE, required=True, critical=True),
    )
    purchase_time: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Time", FieldKind.STRING)
    )

    currency: CurrencyCode | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Currency", FieldKind.CURRENCY, required=True, groundable=False
        ),
    )
    subtotal: Money | None = Field(
        default=None, json_schema_extra=spec_field("Subtotal", FieldKind.MONEY)
    )
    tax_amount: Money | None = Field(
        default=None, json_schema_extra=spec_field("Tax", FieldKind.MONEY)
    )
    total: Money | None = Field(
        default=None,
        json_schema_extra=spec_field("Total", FieldKind.MONEY, required=True, critical=True),
    )

    payment_method: (
        Literal["cash", "card", "bank_transfer", "voucher", "mobile", "other"] | None
    ) = Field(
        default=None,
        json_schema_extra=spec_field("Payment method", FieldKind.ENUM, groundable=False),
    )
    expense_category: (
        Literal[
            "travel",
            "meals",
            "accommodation",
            "office_supplies",
            "fuel",
            "software",
            "entertainment",
            "other",
        ]
        | None
    ) = Field(
        default=None,
        json_schema_extra=spec_field(
            "Category",
            FieldKind.ENUM,
            groundable=False,
            hint="Best-fit expense category inferred from the merchant and items",
        ),
    )

    items: list[ReceiptItem] = Field(
        default_factory=list, json_schema_extra=spec_field("Items", FieldKind.LIST)
    )


RECEIPT_SPEC = DocumentTypeSpec(
    key="receipt",
    name="Receipt",
    description="A proof of purchase, typically for expense reporting.",
    model=Receipt,
    version=1,
    fields=derive_field_specs(Receipt),
    classification=ClassificationHints(
        keywords={
            "receipt": 3.0,
            "účtenka": 3.0,
            "paragon": 3.0,
            "thank you for your purchase": 2.5,
            "cash": 1.0,
            "change": 1.2,
            "card payment": 1.5,
            "terminal": 1.2,
            "celkem": 1.0,
            "kvitance": 2.0,
        },
        patterns={
            r"total\s+\d+[.,]\d{2}": 1.0,
            r"vat\s*\d{1,2}\s*%": 0.8,
        },
        negative_keywords={
            "this agreement": 2.5,
            "purchase order": 2.0,
            "due date": 1.5,
            "splatnost": 1.5,
        },
    ),
    rule_ids=(
        "required_fields",
        "receipt_totals_consistency",
        "currency_supported",
        "positive_amounts",
        "date_not_future",
        "date_sanity",
    ),
    day_first_dates=True,
    # Receipts are OCR-heavy and low-value per document; a lower bar avoids sending
    # every coffee receipt to a human. The critical fields still gate on their own
    # stricter threshold.
    review_threshold=0.78,
    critical_field_threshold=0.88,
    extraction_guidance=(
        "Receipt text is frequently OCR output and may contain character errors. "
        "Prefer values printed next to an explicit label; when the total is "
        "illegible, return null rather than guessing — a null is reviewable, a "
        "plausible wrong number is not.\n"
        "`expense_category` is your inference, not a quotation from the receipt."
    ),
)
