"""Purchase order document type.

Structurally close to an invoice, deliberately kept separate. Merging them behind a
`direction` flag looked tempting and would have been wrong: the *rules* differ (a PO
has no due date and no payment reference, but does have a delivery date and shipping
terms), and the downstream systems differ (POs go to procurement, invoices to AP).
Two specs and zero conditionals beats one spec and a dozen.
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
from docflow.schemas.types.invoice import Party


class PurchaseOrderLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_number: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Line", FieldKind.STRING)
    )
    description: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Description", FieldKind.STRING, required=True)
    )
    sku: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("SKU", FieldKind.IDENTIFIER)
    )
    quantity: Money | None = Field(
        default=None, json_schema_extra=spec_field("Qty", FieldKind.NUMBER, required=True)
    )
    unit: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Unit", FieldKind.STRING)
    )
    unit_price: Money | None = Field(
        default=None, json_schema_extra=spec_field("Unit price", FieldKind.MONEY)
    )
    line_total: Money | None = Field(
        default=None, json_schema_extra=spec_field("Line total", FieldKind.MONEY)
    )
    requested_delivery_date: FlexibleDate | None = Field(
        default=None, json_schema_extra=spec_field("Requested delivery", FieldKind.DATE)
    )


class PurchaseOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    po_number: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "PO number", FieldKind.IDENTIFIER, required=True, critical=True
        ),
    )
    buyer: Party | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Buyer", FieldKind.OBJECT, required=True, hint="Organisation placing the order"
        ),
    )
    supplier: Party | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Supplier", FieldKind.OBJECT, required=True, hint="Organisation fulfilling the order"
        ),
    )

    order_date: FlexibleDate | None = Field(
        default=None, json_schema_extra=spec_field("Order date", FieldKind.DATE, required=True)
    )
    requested_delivery_date: FlexibleDate | None = Field(
        default=None,
        json_schema_extra=spec_field("Delivery date", FieldKind.DATE, critical=True),
    )
    delivery_address: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Delivery address", FieldKind.TEXT)
    )

    currency: CurrencyCode | None = Field(
        default=None,
        json_schema_extra=spec_field("Currency", FieldKind.CURRENCY, required=True),
    )
    subtotal: Money | None = Field(
        default=None, json_schema_extra=spec_field("Subtotal", FieldKind.MONEY)
    )
    tax_amount: Money | None = Field(
        default=None, json_schema_extra=spec_field("Tax amount", FieldKind.MONEY)
    )
    total: Money | None = Field(
        default=None,
        json_schema_extra=spec_field("Total", FieldKind.MONEY, required=True, critical=True),
    )

    payment_terms: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Payment terms", FieldKind.STRING)
    )
    shipping_terms: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Shipping terms", FieldKind.STRING, hint="Incoterms such as DAP, EXW, FOB"
        ),
    )
    requester: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Requester", FieldKind.STRING)
    )
    cost_center: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Cost centre", FieldKind.IDENTIFIER)
    )

    line_items: list[PurchaseOrderLineItem] = Field(
        default_factory=list,
        json_schema_extra=spec_field("Line items", FieldKind.LIST, required=True),
    )
    notes: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Notes", FieldKind.TEXT, groundable=False)
    )


PURCHASE_ORDER_SPEC = DocumentTypeSpec(
    key="purchase_order",
    name="Purchase order",
    description="An order placed by a buyer with a supplier.",
    model=PurchaseOrder,
    version=1,
    fields=derive_field_specs(PurchaseOrder),
    classification=ClassificationHints(
        keywords={
            "purchase order": 3.5,
            "objednávka": 3.0,
            "po number": 3.0,
            "ship to": 1.8,
            "deliver to": 1.8,
            "requested delivery": 2.0,
            "bestellung": 2.5,
            "order confirmation": 1.5,
            "incoterms": 1.5,
        },
        patterns={
            r"p\.?o\.?\s*(no|number|#)": 3.0,
            r"order\s*(no|number|#)": 2.0,
            r"číslo\s+objednávky": 3.0,
        },
        negative_keywords={
            "amount due": 2.0,
            "payment due": 2.0,
            "this agreement": 2.0,
            "variabilní symbol": 2.0,
        },
    ),
    rule_ids=(
        "required_fields",
        "po_totals_consistency",
        "po_date_order",
        "currency_supported",
        "line_items_sum",
        "positive_amounts",
        "ico_checksum",
        "date_sanity",
    ),
    day_first_dates=True,
    review_threshold=0.85,
    extraction_guidance=(
        "A purchase order is issued by the buyer. If the document requests payment "
        "rather than goods or services, it is an invoice, not a purchase order.\n"
        "Return every ordered line, including zero-priced and free-of-charge lines."
    ),
)
