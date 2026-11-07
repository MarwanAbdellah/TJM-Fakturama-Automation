"""The two places that decide what is real: the select-or-create choice (no duplicates,
no wrong pick) and the post-save DB checks (the last line of defence). Fakturama and
the DB are stubbed; only the decisions are tested."""

from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest
from test_validate import make

from image2cash.steps import debtor, order, product
from image2cash.ui.core import AutomationError, ManualReview

# ---------------------------------------------------------------------------
# post-save checks (4.5 Order, 5.5 Invoice)


def _ctx(o):
    return NS(
        order=o,
        order_no="PO1",
        invoice_no="INV1",
        contact_id=7,
        ui=NS(nav=lambda *a: None, show_documents=lambda *a: None),
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
        receivers=[
            dict(
                type=role,
                contact_id=7,
                company=o.debtor.company,
                first_name=o.debtor.first_name,
                name=o.debtor.last_name,
                street=a.street,
                zip=a.zip,
                city=a.city,
                country="DE",
            )
            for role, a in ((3, o.debtor.billing), (5, o.debtor.delivery))
        ],
        payment={"name": o.payment.method},
    )


@pytest.fixture
def booked(monkeypatch):
    o = make()
    w = _world(o)
    monkeypatch.setattr(order.db, "documents_with_ref", lambda ref, dtype=None: w[dtype])
    monkeypatch.setattr(order.db, "document_items", lambda doc_id: w["items"])
    monkeypatch.setattr(order.db, "document_receivers", lambda doc_id: w["receivers"])
    monkeypatch.setattr(order.db, "document_receiver_details", lambda doc_id: w["receivers"])
    monkeypatch.setattr(order.db, "payment", lambda pid: w["payment"])
    return _ctx(o), w


def test_a_correct_booking_passes(booked):
    ctx, _ = booked
    order.verify_saved_order(ctx)


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
    "index, field, failed",
    [(0, key, "billing contact") for key in ("company", "first_name", "name")]
    + [
        (index, key, f"{role} address")
        for index, role in enumerate(("billing", "delivery"))
        for key in ("street", "zip", "city")
    ],
)
def test_saved_order_address_fields_must_match_source(booked, index, field, failed):
    ctx, w = booked
    records = []
    ctx.record = lambda *a, **k: records.append((a, k))
    w["receivers"][index][field] = "different"
    with pytest.raises(AutomationError, match=f"'{failed}'"):
        order.verify_saved_order(ctx)
    assert records[-1][0] == ("4.5 order in DB", "FAIL")
    assert failed in records[-1][1]["failed"]
    assert records[-1][1]["receivers"] == w["receivers"]


@pytest.mark.parametrize("role", [3, 5])
@pytest.mark.parametrize("duplicate", [False, True])
def test_saved_order_requires_unambiguous_address_roles(booked, role, duplicate):
    ctx, w = booked
    ctx.order.debtor.delivery = ctx.order.debtor.delivery.model_copy(update={"street": "Warehouse 2"})
    w["receivers"][1]["street"] = "Warehouse 2"
    row = next(r for r in w["receivers"] if r["type"] == role)
    if duplicate:
        w["receivers"].append(dict(row))
    else:
        w["receivers"].remove(row)
    with pytest.raises(AutomationError, match="billing address" if role == 3 else "delivery address"):
        order.verify_saved_order(ctx)


def test_saved_order_allows_absent_delivery_only_for_shared_address(booked):
    ctx, w = booked
    w["receivers"].pop(1)
    order.verify_saved_order(ctx)
    ctx.order.debtor.delivery = ctx.order.debtor.delivery.model_copy(update={"name": "Another site"})
    with pytest.raises(AutomationError, match="delivery address"):
        order.verify_saved_order(ctx)


@pytest.mark.parametrize(
    "source_zip, source_city, saved_zip, saved_city",
    [
        ("02116", "Boston, MA", "MA 02116", "Boston"),
        ("M5J 2S1", "Toronto, ON", "M5J 2S1", "Toronto"),
        ("L4W 4Y2", "Mississauga, ON", "L4W 4Y2", "Mississauga"),
    ],
)
def test_saved_order_reuses_postcode_normalization_and_ignores_site_name_and_country(
    booked,
    source_zip,
    source_city,
    saved_zip,
    saved_city,
):
    ctx, w = booked
    for field in ("billing", "delivery"):
        address = getattr(ctx.order.debtor, field)
        setattr(ctx.order.debtor, field, address.model_copy(update={"zip": source_zip, "city": source_city}))
    for row in w["receivers"]:
        row.update(zip=saved_zip, city=saved_city, country="US")
    ctx.order.debtor.delivery = ctx.order.debtor.delivery.model_copy(update={"name": "Receiving Warehouse"})
    order.verify_saved_order(ctx)
    w["receivers"][1]["zip"] = "2S1"
    with pytest.raises(AutomationError, match="delivery address"):
        order.verify_saved_order(ctx)


# ---------------------------------------------------------------------------
# select or create: the DB count decides, the grid only locates


@pytest.mark.parametrize(
    "price, outcome", [("450,00 €", "ok"), ("450,02 €", "ok"), ("450,10 €", "review"), ("?", "unread")]
)
def test_final_line_price_is_parsed_and_checked(monkeypatch, price, outcome):
    o = make()
    o.items = o.items[:1]
    row = {"Item No.": "X", "Qty.": "2", "Discount": "10", "Price": price}
    monkeypatch.setattr(product, "_items_grid", lambda ctx: None)
    monkeypatch.setattr(product, "_walk", lambda *a: ([(0, {1: row})], None))
    ctx = NS(
        order=o,
        back_to_order=lambda: None,
        warnings=[],
        shot=lambda *a: None,
        record=lambda *a, **k: None,
    )
    if outcome == "review":
        with pytest.raises(ManualReview, match="line 1 Price shows.*expected 450.00"):
            product.verify_lines(ctx)
    else:
        product.verify_lines(ctx)
        assert ctx.warnings == (["final table read could not read: line 1 Price"] if outcome == "unread" else [])


class _Trace:
    """Stubs for the selector and the create path; records what the step did."""

    def __init__(self, monkeypatch, module, grid):
        self.calls = []
        S = module.S
        monkeypatch.setattr(S, "open_selector", lambda *a: ("dialog", "icon"))
        monkeypatch.setattr(S, "search", lambda *a, **k: grid)
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
    monkeypatch.setattr(debtor, "_adopt_stored_spelling", lambda ctx: None)
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


@pytest.mark.parametrize(
    "order, saved, same",
    [
        (("04101", "Portland, ME"), ("ME 04101", "Portland"), True),
        (("04101", "Portland"), ("ME 04101", "Portland"), True),
        (("S4P 2S7", "Regina"), ("S4P 2S7", "Regina, SK"), True),
        (("S4P 2S7", "Regina"), ("2S7", "Regina"), False),  # a letter-led postcode is kept whole
        (("04102", "Portland"), ("ME 04101", "Portland"), False),
        (("04101", "Bangor"), ("ME 04101", "Portland"), False),
    ],
)
def test_same_place(order, saved, same):
    from image2cash.validate import same_place

    assert same_place(*order, *saved) is same


def test_the_saved_spelling_of_the_same_address_is_used(monkeypatch):
    o = make()
    b = o.debtor.billing
    monkeypatch.setattr(debtor.db, "query", lambda *a: [(f"BE {b.zip}", b.city)])
    ctx = NS(order=o, warnings=[], record=lambda *a, **k: None)
    debtor._adopt_stored_spelling(ctx)
    assert ctx.order.debtor.billing.zip == f"BE {b.zip}" and ctx.warnings
    if o.debtor.delivery.key() != b.key():
        assert ctx.order.debtor.delivery == o.debtor.delivery  # another address is left alone


# ---------------------------------------------------------------------------
# 2.4 / 2.13 / 4.1: the Order's address boxes


def _address_ctx(o, texts):
    """Editor pane with a Delivery and an Invoice address tab showing `texts`."""
    tabs = []
    for name, text in zip(("Delivery address", "Invoice address"), texts):
        tab = NS(element_info=NS(name=name))
        tab.parent = lambda text=text: NS(descendants=lambda control_type=None: [NS(text=text)])
        tabs.append(tab)
    ui = NS(editor_pane=lambda: NS(descendants=lambda control_type=None: tabs), click=lambda c: None)
    return NS(
        order=o,
        ui=ui,
        back_to_order=lambda: None,
        shot=lambda *a, **k: None,
        record=lambda *a, **k: None,
        warnings=[],
    )


def _box(o, a):
    return "\n".join(
        [
            o.debtor.company,
            f"{o.debtor.first_name} {o.debtor.last_name}",
            a.street,
            f"{a.zip} {a.city}",
            a.country,
        ]
    )


def _no_ui(monkeypatch):
    monkeypatch.setattr(debtor, "_visible", lambda c: True)
    monkeypatch.setattr(debtor, "read", lambda c: c.text)
    monkeypatch.setattr(debtor, "wait_for", lambda pred, *a, **k: pred())


@pytest.mark.parametrize("drop", ["street", "zip", "city"])
def test_order_address_box_must_show_street_zip_city(monkeypatch, drop):
    o = make()
    a = o.debtor.billing
    lines = {
        "company": o.debtor.company,
        "name": f"{o.debtor.first_name} {o.debtor.last_name}",
        "street": a.street,
        "zip": a.zip,
        "city": a.city,
        "country": a.country,
    }
    ctx = _address_ctx(o, [_box(o, a), "\n".join(v for k, v in lines.items() if k != drop)])
    records = []
    ctx.record = lambda *a, **k: records.append((a, k))
    _no_ui(monkeypatch)
    with pytest.raises(ManualReview, match="lacks"):
        debtor.verify_order_addresses(ctx)
    assert records[-1][0] == ("2.4 order addresses", "FAIL")


def test_order_address_box_allows_a_title_and_country_code_prefix(monkeypatch):
    o = make()
    ctx = _address_ctx(o, ["Co\nDr. A B\nMain 1\nDE 10117 Berlin\nGermany"] * 2)
    _no_ui(monkeypatch)
    debtor.verify_order_addresses(ctx)


@pytest.mark.parametrize(
    "zip_, city, street, site",
    [
        ("02116", "Boston, MA", "214 Tremont Street", "Harborview Pharmacy LLC"),
        ("L4W 4Y2", "Mississauga, ON", "5600 Explorer Drive", "Northgate Receiving Warehouse"),
        ("60612", "Chicago, IL", "1700 West Van Buren Street", "Lakeshore Inpatient Pharmacy"),
    ],
)
def test_order_address_box_allows_city_before_zip_and_hidden_country(monkeypatch, zip_, city, street, site):
    o = make()
    o.debtor.delivery = o.debtor.delivery.model_copy(update={"name": site, "street": street, "zip": zip_, "city": city})
    ctx = _address_ctx(o, [f"{site}\n{street}\n{city} {zip_}", "Main 1\n10117 Berlin"])
    _no_ui(monkeypatch)
    debtor.verify_order_addresses(ctx)


# ---------------------------------------------------------------------------
# 2.10.3 / 2.10.5: the new payment's blank fields


_GOOD_PAYMENT = ("Bank Transfer", "Bank Transfer", None, 0.0, 0, 0, None, "", None)


@pytest.mark.parametrize(
    "row, ok",
    [
        (_GOOD_PAYMENT, True),
        (("Bank", "Bank Transfer", None, 0.0, 0, 0, None, "", None), False),
        (("Bank Transfer", "Bank", None, 0.0, 0, 0, None, "", None), False),
        (("Bank Transfer", "Bank Transfer", 3, 0.0, 0, 0, None, "", None), False),
        (("Bank Transfer", "Bank Transfer", None, 0.5, 0, 0, None, "", None), False),
        (("Bank Transfer", "Bank Transfer", None, 0.0, 2, 0, None, "", None), False),
        (("Bank Transfer", "Bank Transfer", None, 0.0, 0, 30, None, "", None), False),
        (("Bank Transfer", "Bank Transfer", None, 0.0, 0, 0, "x", "", None), False),
        (("Bank Transfer", "Bank Transfer", None, 0.0, 0, 0, None, "x", None), False),
        (("Bank Transfer", "Bank Transfer", None, 0.0, 0, 0, None, "", "x"), False),
        (None, False),  # saved row not found
    ],
)
def test_new_payment_fields_are_checked(monkeypatch, row, ok):
    records = []
    ctx = NS(order=make(), record=lambda *a, **k: records.append((a, k)))
    monkeypatch.setattr(debtor.db, "query", lambda *a: [] if row is None else [row])
    if ok:
        debtor._check_new_payment(ctx, 3)
    else:
        with pytest.raises(AutomationError):
            debtor._check_new_payment(ctx, 3)
    assert records[-1][0][1] == ("ok" if ok else "FAIL")


def test_a_clearly_read_customer_number_picks_the_confirmed_debtor():
    """A smaller screen: the selector cut off City and OCR missed First Name (Northstar, CUST000001)."""
    ctx = NS(order=make())
    contact = NS(number="CUST000001")
    seen = NS(
        get=lambda k: {"No.": "CUST000001", "Name": "Klein", "Company": "Northstar Offi...", "ZIP": "10117"}.get(k, "")
    )
    assert debtor._row_is_target(ctx, contact)(seen)
    other = NS(get=lambda k: {"No.": "CUST000002"}.get(k, ""))
    assert not debtor._row_is_target(ctx, contact)(other)  # another contact's row is never picked
    assert not debtor._row_is_target(ctx)(seen)  # without a DB-confirmed contact, every field must match
