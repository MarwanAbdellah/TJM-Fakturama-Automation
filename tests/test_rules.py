"""Business rules that guard identity, currency and idempotency (no Fakturama needed)."""

import pytest

from image2cash.steps import order
from image2cash.steps.order import booked_currency
from image2cash.steps.product import is_line, sku_eq
from image2cash.ui.core import ManualReview


@pytest.mark.parametrize(
    "seen, sku, same",
    [
        ("OTC-GAU-4X4", "OTC-GAU-4X4", True),
        ("| OTC-GAU-4X4", "OTC-GAU-4X4", True),  # cell border read as '|'
        ("SAMPLO3", "SAMPL03", True),  # O/0 confusion
        ("OTC-ALC-PAD", "OTC-GAU-4X4", False),
        ("SAMPL04", "SAMPL03", False),
        ("", "X", False),
        ("M5J 251", "M5J 2S1", True),  # Canadian postal code, S read as 5
        ("CUSTO00006", "CUST000006", True),
    ],
)
def test_sku_eq(seen, sku, same):
    assert sku_eq(seen, sku) is same


@pytest.mark.parametrize(
    "pos, seen, above, has_above, ok",
    [
        (8, 8, 7, True, True),
        (8, 7, 6, True, False),  # bottom row is still the previous line
        (8, None, 7, True, True),  # Pos. unreadable on the selected row
        (8, None, 6, True, False),
        (1, None, None, False, True),
        (2, None, None, False, False),
    ],
)
def test_is_line(pos, seen, above, has_above, ok):
    assert is_line(pos, seen, above, has_above) is ok


def test_booked_currency():
    assert booked_currency("$0.00") == "USD"
    assert booked_currency("0,00 €") == "EUR"
    assert booked_currency("۰٫۰۰ ؋") == "AFN"
    assert booked_currency("0.00") is None


class _Ctx:
    def __init__(self, resume):
        self.resume, self.resume_at, self.order_no = resume, None, ""
        self.order = type("O", (), {"external_ref": "RX-1"})()

    def record(self, *a, **k):
        pass


@pytest.mark.parametrize(
    "docs, resume, outcome",
    [
        ([], False, "ok"),
        ([{"dtype": "Order", "number": "PO7"}], True, "resume"),
        ([{"dtype": "Order", "number": "PO7"}], False, "review"),
        ([{"dtype": "Order", "number": "PO7"}, {"dtype": "Invoice", "number": "INV7"}], True, "review"),
    ],
)
def test_preflight_never_duplicates(monkeypatch, docs, resume, outcome):
    monkeypatch.setattr(order.db, "documents_with_ref", lambda ref, dtype=None: docs)
    ctx = _Ctx(resume)
    if outcome == "review":
        with pytest.raises(ManualReview):
            order.preflight(ctx)
    else:
        order.preflight(ctx)
        assert (ctx.resume_at == "invoice") is (outcome == "resume")


def test_close_allows_one_cent_per_line():
    from decimal import Decimal as D

    from image2cash.validate import close

    assert close(D("348.52"), D("348.48"), 8)  # 4 cents over 8 lines
    assert not close(D("348.52"), D("348.48"), 2)
    assert not close(None, D("1"), 1)


def test_catalogue_price_is_confirmed_not_overwritten():
    from decimal import Decimal as D

    from image2cash.models import Item
    from image2cash.steps.product import _check_master

    item = Item(
        pos=1, sku="OTC-GLV-N100", description="x", qty=D(40), unit_net=D("9.90"), vat_pct=D("6.25"), line_net=D("396")
    )
    row = {"vat_value": 0.0625, "vat_name": "VAT 6.25%", "price": 9.901176470588235}  # entered as gross 10.52
    _check_master(None, item, row)  # rounds to 9.90: same price
    with pytest.raises(ManualReview, match="catalogue net price 10.50"):
        _check_master(None, item, {**row, "price": 10.5})
