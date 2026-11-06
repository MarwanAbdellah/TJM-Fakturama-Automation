"""The contract between extraction, validation and the UI flow.

Money is Decimal in the order's currency. Percentages are plain numbers
(19 means 19 %). Nothing here is ever filled with a guessed value: a field the
source does not show stays ``None`` and the validator decides whether that is
acceptable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator


class PaidStatus(str, Enum):
    PAID = "PAID"
    UNPAID = "UNPAID"
    OTHER = "OTHER"


class Address(BaseModel):
    name: str  # first line of the block (company or site name)
    street: str
    zip: str
    city: str
    country: str
    extra_lines: list[str] = []

    def key(self) -> tuple[str, str, str, str, str]:
        return (self.name, self.street, self.zip, self.city, self.country)


class Debtor(BaseModel):
    customer_id: str | None = None  # source id; Fakturama proposes its own
    company: str
    first_name: str
    last_name: str
    alias: str | None = None
    email: str | None = None
    phone: str | None = None
    billing: Address
    delivery: Address

    @property
    def same_delivery(self) -> bool:
        return self.billing.key() == self.delivery.key()


# The entries of Fakturama's payment-code dropdown.
PaymentCode = Literal["Credit transfer", "Credit card", "SEPA direct debit"]
PAYMENT_CODE_LABELS: tuple[str, ...] = get_args(PaymentCode)

# Brief 2.10.4: payment method name -> Fakturama payment code.
PAYMENT_CODES: dict[str, str] = {
    "Bank Transfer": "Credit transfer",
    "Credit Card": "Credit card",
    "SEPA Direct Debit": "SEPA direct debit",
}


class Payment(BaseModel):
    method: str  # any name; a missing one is created in Fakturama
    status: PaidStatus
    paid_date: date | None = None
    code: PaymentCode | None = None  # picked in review; None = the brief's mapping of method

    @field_validator("method")
    @classmethod
    def _brief_spelling(cls, v: str) -> str:
        # 'bank transfer' is the brief's 'Bank Transfer', not a second method in Fakturama
        v = v.strip()
        return next((m for m in PAYMENT_CODES if m.casefold() == v.casefold()), v)

    @property
    def payment_code(self) -> str | None:
        return self.code or PAYMENT_CODES.get(self.method)


class Item(BaseModel):
    pos: int
    sku: str
    description: str
    qty: Decimal
    unit: str | None = None
    unit_net: Decimal
    discount_pct: Decimal = Decimal("0")
    vat_pct: Decimal
    line_net: Decimal  # as printed on the source


class OrderDraft(BaseModel):
    external_ref: str
    order_date: date
    currency: str
    debtor: Debtor
    payment: Payment
    items: list[Item] = Field(min_length=1)
    net_total: Decimal
    vat_total: Decimal
    gross_total: Decimal

    source: str | None = None
    engine: str | None = None
    min_confidence: float = 1.0
