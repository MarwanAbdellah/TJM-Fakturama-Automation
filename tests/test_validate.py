from datetime import date
from decimal import Decimal

import pytest

from image2cash.models import Address, Debtor, Item, OrderDraft, PaidStatus, Payment
from image2cash.validate import (
    check_order,
    gross_price,
    line_net,
    parse_amount,
    totals,
    vat_name,
)

D = Decimal


def make(**over) -> OrderDraft:
    addr = Address(name="Co", street="Main 1", zip="10117", city="Berlin", country="Germany")
    base = dict(
        external_ref="R-1",
        order_date=date(2026, 7, 14),
        currency="EUR",
        debtor=Debtor(company="Co", first_name="A", last_name="B", billing=addr, delivery=addr),
        payment=Payment(method="Bank Transfer", status=PaidStatus.PAID, paid_date=date(2026, 7, 18)),
        items=[
            Item(
                pos=1,
                sku="X",
                description="x",
                qty=D(2),
                unit_net=D("250"),
                discount_pct=D(10),
                vat_pct=D(19),
                line_net=D("450"),
            ),
            Item(
                pos=2,
                sku="Y",
                description="y",
                qty=D(3),
                unit_net=D("40"),
                discount_pct=D(0),
                vat_pct=D(19),
                line_net=D("120"),
            ),
        ],
        net_total=D("570.00"),
        vat_total=D("108.30"),
        gross_total=D("678.30"),
    )
    base.update(over)
    return OrderDraft(**base)


def test_brief_formulas():
    assert line_net(D(2), D(250), D(10)) == D("450.00")  # 3.16
    assert gross_price(D(250), D(19)) == D("297.50")  # 3.9, no line discount
    assert gross_price(D("89.90"), D(19)) == D("106.98")
    assert vat_name(D(19)) == "VAT 19%"
    assert vat_name(D("8.25")) == "VAT 8.25%"


def test_vat_rounded_per_rate_group():
    o = make(
        items=[
            Item(
                pos=1,
                sku="L",
                description="l",
                qty=D(4),
                unit_net=D(35),
                discount_pct=D(5),
                vat_pct=D(7),
                line_net=D(133),
            ),
            Item(
                pos=2,
                sku="C",
                description="c",
                qty=D(1),
                unit_net=D(250),
                discount_pct=D(0),
                vat_pct=D(19),
                line_net=D(250),
            ),
        ],
        net_total=D("383.00"),
        vat_total=D("56.81"),
        gross_total=D("439.81"),
    )
    assert totals(o) == (D("383.00"), D("56.81"), D("439.81"))
    assert check_order(o) == []


def test_valid_order_has_no_problems():
    assert check_order(make()) == []


@pytest.mark.parametrize(
    "over, needle",
    [
        ({"gross_total": D("678.31")}, "gross total"),
        ({"payment": Payment(method="Bank Transfer", status=PaidStatus.PAID)}, "no payment date"),
        ({"payment": Payment(method="Cheque", status=PaidStatus.UNPAID)}, "no payment-code mapping"),
        ({"currency": "Euro"}, "3-letter"),
        ({"payment": Payment(method="Bank Transfer", status=PaidStatus.OTHER)}, "neither PAID"),
    ],
)
def test_problems(over, needle):
    assert any(needle in p for p in check_order(make(**over)))


def test_duplicate_sku():
    o = make()
    o.items[1].sku = "X"
    assert any("twice" in p for p in check_order(o))


@pytest.mark.parametrize(
    "text, value",
    [
        ("۴۵۰٫۰۰ ؋", D("450.00")),  # ps/AF rendering
        ("۶۷٬۸۳۰٫۰۰", D("67830.00")),  # thousands sep
        ("1,234.56", D("1234.56")),
        ("1.234,56 EUR", D("1234.56")),
        ("-10.00 %", D("-10.00")),
        ("0%", D(0)),
        ("$450.00", D("450.00")),
        ("", None),
    ],
)
def test_parse_amount(text, value):
    assert parse_amount(text) == value
