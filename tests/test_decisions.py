"""The two places that decide what is real: the select-or-create choice (no duplicates,
no wrong pick) and the post-save DB checks (the last line of defence). Fakturama and
the DB are stubbed; only the decisions are tested."""

from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest
from test_validate import make

from image2cash.steps import debtor, invoice, order, product
from image2cash.ui.core import AutomationError, ManualReview

# ---------------------------------------------------------------------------
# post-save checks (4.5 Order, 5.5 Invoice)


def _ctx(o):
    return NS(
        order=o,
        order_no="PO1",
        invoice_no="INV1",
        contact_id=7,
        ui=NS(nav=lambda *a: None),
        shot=lambda *a, **k: None,
        record=lambda *a, **k: None,
    )


def _world(o):
    """DB rows exactly as Fakturama stores a correct booking of `o`."""
    doc = dict(
        id=1,
        number="PO1",
        dtype="Order",
        date=o.order_date.isoformat(),
        total=o.gross_total,
        paid=False,
        cust_ref=o.external_ref,
        src_id=None,
        payment_id=None,
        pay_date=None,
        paid_value=None,
    )
    inv = dict(
        id=2,
        number="INV1",
        dtype="Invoice",
        date=o.order_date.isoformat(),
        total=o.gross_total,
        paid=True,
        cust_ref=o.external_ref,
        src_id=1,
        payment_id=9,
        pay_date=o.payment.paid_date.isoformat(),
        paid_value=o.gross_total,
    )
    items = [
        dict(sku=i.sku, qty=i.qty, price=i.unit_net, rebate=-i.discount_pct / 100, vat_value=i.vat_pct / 100)
        for i in o.items
    ]
    return dict(
        Order=[doc],
        Invoice=[inv],
        items=items,
        receivers=[{"contact_id": 7}, {"contact_id": 7}],
        payment={"name": o.payment.method},
    )


@pytest.fixture
def booked(monkeypatch):
    o = make()
    w = _world(o)
    monkeypatch.setattr(order.db, "documents_with_ref", lambda ref, dtype=None: w[dtype])
    monkeypatch.setattr(order.db, "document_items", lambda doc_id: w["items"])
    monkeypatch.setattr(order.db, "document_receivers", lambda doc_id: w["receivers"])
    monkeypatch.setattr(order.db, "payment", lambda pid: w["payment"])
    return _ctx(o), w


def test_a_correct_booking_passes(booked):
    ctx, _ = booked
    order.verify_saved_order(ctx)
    invoice.verify(ctx)


@pytest.mark.parametrize(
    "spoil, failed",
    [
        (lambda w: w["Order"][0].update(number="PO2"), "number"),
        (lambda w: w["Order"][0].update(date="2026-07-15"), "date"),
        (lambda w: w["Order"][0].update(total=D("700")), "total"),
        (lambda w: w["items"].pop(), "items"),
        (lambda w: w["items"][0].update(sku="Z"), "item 1 sku"),
        (lambda w: w["items"][0].update(qty=D(3)), "item 1 qty"),
        (lambda w: w["items"][1].update(price=D("40.01")), "item 2 price"),
        (lambda w: w["items"][0].update(rebate=D("-10")), "item 1 discount"),  # 100x scale
        (lambda w: w["items"][0].update(vat_value=D("0.07")), "item 1 vat"),
        (lambda w: w["receivers"][1].update(contact_id=8), "debtor"),  # delivery from another contact
    ],
)
def test_saved_order_differences_stop_the_run(booked, spoil, failed):
    ctx, w = booked
    spoil(w)
    with pytest.raises(AutomationError, match=f"'{failed}'"):
        order.verify_saved_order(ctx)


@pytest.mark.parametrize(
    "spoil, failed",
    [
        (lambda w: w["Invoice"][0].update(number="INV2"), "invoice number"),
        (lambda w: w["Invoice"][0].update(src_id=3), "linked to order"),
        (lambda w: w["payment"].update(name="Cash"), "payment method"),
        (lambda w: w["Invoice"][0].update(paid=False), "paid flag"),
        (lambda w: w["Invoice"][0].update(pay_date="2026-07-19"), "pay date"),
        (lambda w: w["Invoice"][0].update(paid_value=D("1")), "paid value"),
        (lambda w: w["Order"][0].update(paid=True), "order still open"),
        (lambda w: w["receivers"].clear(), "invoice debtor"),
    ],
)
def test_saved_invoice_differences_stop_the_run(booked, spoil, failed):
    ctx, w = booked
    spoil(w)
    with pytest.raises(AutomationError, match=f"'{failed}'"):
        invoice.verify(ctx)


# ---------------------------------------------------------------------------
# select or create: the DB count decides, the grid only locates


class _Trace:
    """Stubs for the selector and the create path; records what the step did."""

    def __init__(self, monkeypatch, module, grid):
        self.calls = []
        S = module.S
        monkeypatch.setattr(S, "open_selector", lambda *a: ("dialog", "icon"))
        monkeypatch.setattr(S, "search", lambda *a: grid)
        monkeypatch.setattr(S, "pick", lambda *a: self.calls.append("pick"))
        monkeypatch.setattr(S, "press", lambda d, name: self.calls.append(name))
        monkeypatch.setattr(module, "create", lambda *a: self.calls.append("create"))
        monkeypatch.setattr(module, "reselect", lambda *a: self.calls.append("reselect"))


def _grid(*skus):
    return NS(rows=[NS(get=lambda k, s=s: s, cells={"Item No.": s}) for s in skus])


@pytest.mark.parametrize(
    "db_rows, grid, outcome",
    [
        (2, None, "review"),  # two products with the SKU: never guess
        (1, None, ["added"]),  # unique and auto-accepted
        (0, None, "review"),  # auto-accepted, but the DB has no such product
        (1, _grid("X"), ["pick", "added"]),
        (0, _grid("X"), "review"),  # grid shows it, DB does not: OCR or DB problem
        (0, _grid("XY"), ["Cancel", "create", "reselect"]),  # only a near SKU: create
    ],
)
def test_product_select_or_create(monkeypatch, db_rows, grid, outcome):
    o = make()
    t = _Trace(monkeypatch, product, grid)
    monkeypatch.setattr(
        product.db, "products_with_sku", lambda sku: [{"id": i, "price": "250"} for i in range(db_rows)]
    )
    monkeypatch.setattr(product, "_check_master", lambda *a: None)
    monkeypatch.setattr(product, "_added", lambda *a: t.calls.append("added"))
    ctx = NS(order=o, shot=lambda *a, **k: None, record=lambda *a, **k: None)
    if outcome == "review":
        with pytest.raises(ManualReview):
            product.select_or_create(ctx, o.items[0])
        assert "create" not in t.calls
    else:
        product.select_or_create(ctx, o.items[0])
        assert t.calls == outcome


@pytest.mark.parametrize(
    "matches, near, grid, target_visible, outcome",
    [
        (2, [], None, False, "review"),  # two exact debtors
        (0, [("CUST1", "Boston")], None, False, "review"),  # same company + ZIP, other city
        (1, [], None, False, []),  # unique and auto-accepted
        (0, [], None, False, "review"),  # auto-accepted, but the DB has no exact match
        (1, [], _grid("Co"), True, ["pick"]),
        (0, [], _grid("Co"), True, "review"),  # grid shows a match the DB does not confirm
        (0, [], _grid("Other"), False, ["Cancel", "create", "reselect"]),
    ],
)
def test_debtor_select_or_create(monkeypatch, matches, near, grid, target_visible, outcome):
    t = _Trace(monkeypatch, debtor, grid)
    monkeypatch.setattr(debtor, "_exact", lambda ctx: [NS(id=10 + i) for i in range(matches)])
    monkeypatch.setattr(debtor.db, "query", lambda *a: near)
    monkeypatch.setattr(debtor, "_row_is_target", lambda ctx, contact=None: lambda r: target_visible)
    ctx = NS(order=make(), contact_id=None, shot=lambda *a, **k: None, record=lambda *a, **k: None)
    if outcome == "review":
        with pytest.raises(ManualReview):
            debtor.select_or_create(ctx)
        assert "create" not in t.calls
    else:
        debtor.select_or_create(ctx)
        assert t.calls == outcome
        if "create" not in outcome:
            assert ctx.contact_id == 10  # the pick is remembered for the post-save checks
