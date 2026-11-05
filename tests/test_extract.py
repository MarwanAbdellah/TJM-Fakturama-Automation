"""Extraction from a recorded OCR result (no OCR engine needed to run)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from image2cash.extract.layout import ExtractionError, parse
from image2cash.extract.ocr import Line, load
from image2cash.models import PaidStatus
from image2cash.validate import check_order

FIXTURE = Path(__file__).parent / "fixtures" / "order_WEB-2026-0714-A17.ocr.json"


@pytest.fixture
def order():
    return parse(load(FIXTURE), source="fixture")


def test_header_fields(order):
    assert order.external_ref == "WEB-2026-0714-A17"
    assert order.order_date == date(2026, 7, 14)
    assert order.currency == "EUR"


def test_debtor(order):
    d = order.debtor
    assert (d.company, d.first_name, d.last_name) == ("Northstar Office GmbH", "Marta", "Klein")
    assert d.alias == "NORTHSTAR-BERLIN"
    assert d.email == "marta.klein@example.test"
    assert d.phone == "+49 30 5550 1420"
    assert (d.billing.street, d.billing.zip, d.billing.city, d.billing.country) == (
        "Friedrichstrasse 88",
        "10117",
        "Berlin",
        "Germany",
    )
    assert d.delivery.name == "Northstar Office Warehouse"
    assert (d.delivery.street, d.delivery.zip) == ("Beusselstrasse 44", "10553")
    assert not d.same_delivery


def test_payment(order):
    assert order.payment.method == "Bank Transfer"
    assert order.payment.status is PaidStatus.PAID
    assert order.payment.paid_date == date(2026, 7, 18)


def test_items_including_textless_headers(order):
    # 'Unit net (EUR)' and 'Line net (EUR)' headers come back from OCR without
    # text; their columns are still recovered from the header boxes.
    a, b = order.items
    assert (a.sku, a.description, a.qty, a.unit_net, a.discount_pct, a.vat_pct, a.line_net) == (
        "CHR-ERG-01",
        "Ergonomic Desk Chair",
        Decimal(2),
        Decimal("250.00"),
        Decimal(10),
        Decimal(19),
        Decimal("450.00"),
    )
    assert (b.sku, b.qty, b.unit_net, b.discount_pct, b.line_net) == (
        "MAT-DESK-02",
        Decimal(3),
        Decimal("40.00"),
        Decimal(0),
        Decimal("120.00"),
    )


def test_totals_and_validation(order):
    assert (order.net_total, order.vat_total, order.gross_total) == (
        Decimal("570.00"),
        Decimal("108.30"),
        Decimal("678.30"),
    )
    assert check_order(order) == []


def test_resolution_independent(order):
    """Thresholds are relative to text height: a 2x scaled page parses the same."""
    scaled = [Line(l.text, l.conf, l.x0 * 2, l.y0 * 2, l.x1 * 2, l.y1 * 2) for l in load(FIXTURE)]
    assert parse(scaled).model_dump(exclude={"source"}) == order.model_dump(exclude={"source"})


def _swap(old: str, new: str):
    return [Line(new, l.conf, l.x0, l.y0, l.x1, l.y1) if l.text == old else l for l in load(FIXTURE)]


def test_misread_line_total_is_caught():
    problems = check_order(parse(_swap("450.00", "405.00")))
    assert any("CHR-ERG-01" in p for p in problems)


def test_misread_unit_price_breaks_line_and_totals():
    problems = check_order(parse(_swap("250.00", "260.00")))
    assert any("CHR-ERG-01" in p for p in problems)
    assert any("net total" in p for p in problems)


def test_missing_label_fails_loudly():
    lines = [l for l in load(FIXTURE) if l.text != "PAYMENT METHOD"]
    with pytest.raises(ExtractionError, match="PAYMENT METHOD"):
        parse(lines)


def test_boxes_recorded_per_field():
    boxes: dict = {}
    parse(load(FIXTURE), boxes=boxes)
    for key in (
        "external_ref",
        "debtor.company",
        "debtor.billing",
        "payment.method",
        "net_total",
        "items.1.sku",
        "items.2.line_net",
    ):
        assert boxes.get(key), key
    assert len(boxes["debtor.billing"]) == 4  # name, street, zip/city, country


@pytest.mark.parametrize(
    "line, expected",
    [
        ("Boston, MA 02118", ("02118", "Boston, MA")),
        ("Toronto, ON M5V 2T6", ("M5V 2T6", "Toronto, ON")),
        ("New York, NY 10001-2345", ("10001-2345", "New York, NY")),
        ("10117 Berlin", ("10117", "Berlin")),
    ],
)
def test_city_lines(line, expected):
    from image2cash.extract.layout import split_city_line

    assert split_city_line(line) == expected
