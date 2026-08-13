"""Contract document type.

Contracts are the clearest case for why the platform is not "an invoice parser".
The extraction target is completely different — dates that create obligations,
renewal traps, liability caps — and so is the failure mode. A wrong invoice total is
caught at reconciliation within the month; a missed auto-renewal notice window is
caught a year later, after it has cost real money.

Which is why `auto_renewal` and `notice_period_days` are marked critical: the
business value of this document type is almost entirely "tell me before the renewal
window closes".
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


class ContractParty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Name", FieldKind.STRING, required=True)
    )
    role: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Role", FieldKind.STRING, hint="e.g. Supplier, Customer, Licensor, Landlord"
        ),
    )
    registration_id: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Company ID", FieldKind.IDENTIFIER)
    )
    address: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Address", FieldKind.TEXT)
    )


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field("Title", FieldKind.STRING, required=True),
    )
    contract_type: (
        Literal[
            "service_agreement",
            "nda",
            "employment",
            "lease",
            "license",
            "supply",
            "framework",
            "amendment",
            "other",
        ]
        | None
    ) = Field(
        default=None,
        json_schema_extra=spec_field(
            "Contract type", FieldKind.ENUM, required=True, groundable=False
        ),
    )
    parties: list[ContractParty] = Field(
        default_factory=list,
        json_schema_extra=spec_field("Parties", FieldKind.LIST, required=True),
    )

    effective_date: FlexibleDate | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Effective date", FieldKind.DATE, required=True, critical=True
        ),
    )
    expiration_date: FlexibleDate | None = Field(
        default=None,
        json_schema_extra=spec_field("Expiration date", FieldKind.DATE, critical=True),
    )
    signature_date: FlexibleDate | None = Field(
        default=None, json_schema_extra=spec_field("Signature date", FieldKind.DATE)
    )
    term_months: Money | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Term (months)", FieldKind.NUMBER, hint="Initial term length in months"
        ),
    )

    auto_renewal: bool | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Auto-renews",
            FieldKind.BOOLEAN,
            critical=True,
            groundable=False,
            hint="True if the contract renews automatically unless notice is given",
        ),
    )
    notice_period_days: Money | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Notice period (days)",
            FieldKind.NUMBER,
            critical=True,
            groundable=False,
            hint="Days of notice required to terminate or prevent renewal",
        ),
    )

    total_value: Money | None = Field(
        default=None,
        json_schema_extra=spec_field("Contract value", FieldKind.MONEY),
    )
    currency: CurrencyCode | None = Field(
        default=None, json_schema_extra=spec_field("Currency", FieldKind.CURRENCY)
    )
    payment_terms: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Payment terms", FieldKind.STRING)
    )

    governing_law: CleanStr | None = Field(
        default=None, json_schema_extra=spec_field("Governing law", FieldKind.STRING)
    )
    liability_cap: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Liability cap", FieldKind.STRING, hint="Quote the cap as written"
        ),
    )
    confidentiality: bool | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Confidentiality clause", FieldKind.BOOLEAN, groundable=False
        ),
    )
    termination_summary: CleanStr | None = Field(
        default=None,
        json_schema_extra=spec_field(
            "Termination",
            FieldKind.TEXT,
            groundable=False,
            hint="One or two sentences summarising how the contract can be ended",
        ),
    )


CONTRACT_SPEC = DocumentTypeSpec(
    key="contract",
    name="Contract",
    description="A legal agreement between two or more parties.",
    model=Contract,
    version=1,
    fields=derive_field_specs(Contract),
    classification=ClassificationHints(
        keywords={
            "this agreement": 3.0,
            "agreement": 1.5,
            "smlouva": 3.0,
            "contract": 2.0,
            "hereinafter": 2.5,
            "the parties": 2.0,
            "smluvní strany": 3.0,
            "governing law": 2.0,
            "termination": 1.5,
            "confidentiality": 1.5,
            "in witness whereof": 3.0,
            "whereas": 2.0,
            "shall be entitled": 1.5,
        },
        patterns={
            r"section\s+\d+\.\d+": 1.5,
            r"článek\s+[IVX0-9]+": 2.0,
            r"clause\s+\d+": 1.5,
        },
        negative_keywords={"invoice": 2.0, "faktura": 2.0, "amount due": 1.5},
    ),
    rule_ids=(
        "required_fields",
        "contract_date_order",
        "contract_parties_min",
        "contract_renewal_consistency",
        "currency_supported",
        "date_sanity",
    ),
    day_first_dates=True,
    review_threshold=0.80,
    critical_field_threshold=0.90,
    extraction_guidance=(
        "Return dates as they appear in the contract, not as computed offsets.\n"
        "`auto_renewal` must be true only when the text states that the term renews "
        "without positive action by a party. Silence is not auto-renewal — leave it "
        "null if the contract does not say.\n"
        "`liability_cap` should quote the contract's own wording rather than "
        "paraphrasing it, since the exact formulation is legally operative.\n"
        "Do not summarise clauses that are not asked for."
    ),
)
