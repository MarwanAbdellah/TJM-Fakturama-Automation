"""Post-save Invoice checks, with independent Order/Invoice DB snapshots."""

from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest
from test_validate import make

from image2cash.models import PaidStatus
from image2cash.steps import invoice
from image2cash.ui.core import AutomationError
from image2cash.verify import db


@pytest.fixture
def booked(monkeypatch):
    o = make()
    source = dict(
        id=1,
        number="PO1",
        dtype="Order",
        date=o.order_date.isoformat(),
        order_date=o.order_date.isoformat(),
        total=o.gross_total,
        paid=False,
        cust_ref=o.external_ref,
        src_id=None,
        payment_id=None,
        pay_date=None,
        paid_value=None,
        contact_id=None,
        delivery_contact_id=None,
    )
    saved = dict(
        source,
        id=2,
        number="INV1",
        dtype="Invoice",
        date="2026-11-06",
        src_id=1,
        payment_id=9,
        paid=True,
        pay_date=o.payment.paid_date.isoformat(),
        paid_value=o.gross_total,
    )
    lines = [
        dict(
            pos=i.pos,
            sku=i.sku,
            name=i.description,
            product_id=10 + i.pos,
            qty=i.qty,
            price=i.unit_net,
            rebate=-i.discount_pct / 100,
            vat_value=i.vat_pct / 100,
        )
        for i in o.items
    ]
    receiver = dict(
        type=3,
        company="Co",
        first_name="A",
        name="B",
        street="Main 1",
        zip="10117",
        city="Berlin",
        country="DE",
        contact_id=7,
        address_id=10,
        name_addon=None,
        city_addon=None,
        manual_address=None,
        description=None,
        title=None,
        email=None,
        phone=None,
    )
    receivers = [receiver, dict(receiver, type=5, street="Warehouse 2", address_id=11)]
    w = dict(
        Order=[source],
        Invoice=[saved],
        items={1: lines, 2: deepcopy(lines)},
        receivers={1: receivers, 2: [dict(receivers[1]), dict(receiver, type=0)]},
        fields={
            1: dict(service_date="2026-10-01", net_gross=1, no_vat_ref=None),
            2: dict(service_date="2026-11-05", net_gross=1, no_vat_ref=None),
        },
        payment={"name": o.payment.method},
    )
    records = []
    ctx = NS(
        order=o,
        order_no="PO1",
        invoice_no="INV1",
        contact_id=7,
        cache={"invoice_dates": dict(number="INV1", date="2026-11-06", service_date="2026-11-05")},
        ui=NS(nav=lambda *a: None, show_documents=lambda *a, **k: None),
        shot=lambda *a, **k: None,
        record=lambda *a, **k: records.append((a, k)),
    )
    monkeypatch.setattr(db, "documents_with_ref", lambda ref, dtype=None: w[dtype])
    monkeypatch.setattr(db, "document_items", lambda did: w["items"][did])
    monkeypatch.setattr(db, "document_receivers", lambda did: w["receivers"][did])
    monkeypatch.setattr(db, "document_receiver_details", lambda did: w["receivers"][did])
    monkeypatch.setattr(db, "document_copy_fields", lambda did: w["fields"][did])
    monkeypatch.setattr(db, "payment", lambda pid: w["payment"])
    return ctx, w, records


def test_correct_invoice_and_distinct_proposed_dates_pass(booked):
    ctx, _, records = booked
    invoice.verify(ctx)
    assert records[-1][0] == ("5.5 verify", "ok")


def assert_failed(booked, failed):
    ctx, _, records = booked
    with pytest.raises(AutomationError, match=f"'{failed}'"):
        invoice.verify(ctx)
    assert records[-1][0] == ("5.5 verify", "FAIL")
    assert failed in records[-1][1]["failed"]


@pytest.mark.parametrize(
    "spoil, failed",
    [
        (lambda w: w["Invoice"][0].update(number="INV2"), "invoice number"),
        (lambda w: w["Invoice"][0].update(src_id=3), "linked to order"),
        (lambda w: w["payment"].update(name="Cash"), "payment method"),
        (lambda w: w["Invoice"][0].update(paid=False), "paid flag"),
        (lambda w: w["Invoice"][0].update(pay_date="2026-07-19"), "pay date"),
        (lambda w: w["Invoice"][0].update(paid_value=D("1")), "paid value"),
        (lambda w: w["Invoice"][0].update(total=D("1")), "invoice total"),
        (lambda w: w["Order"][0].update(paid=True), "order still open"),
        (lambda w: w["Order"][0].update(total=D("1")), "order total unchanged"),
        (lambda w: w["Order"][0].update(cust_ref="other"), "order cust.ref"),
        (lambda w: w["Invoice"][0].update(cust_ref="other"), "invoice cust.ref"),
        (lambda w: w["receivers"][2].clear(), "invoice debtor"),
        (lambda w: w["Invoice"][0].update(order_date="2026-07-15"), "order date copied"),
        (lambda w: w["Order"][0].update(order_date="2026-07-15"), "order date copied"),
        (lambda w: w["Order"][0].update(date="2026-07-15"), "order date copied"),
        (lambda w: w["Invoice"][0].update(date="2026-07-14"), "invoice date unchanged"),
        (lambda w: w["fields"][2].update(service_date="2026-07-14"), "service date unchanged"),
        (lambda w: w["fields"][2].update(service_date=None), "service date unchanged"),
        (lambda w: w["fields"][2].update(net_gross=2), "invoice modes copied"),
        (lambda w: w["fields"][2].update(no_vat_ref=1), "invoice modes copied"),
        (lambda w: w["fields"].update({2: None}), "invoice modes copied"),
        (lambda w: w["Invoice"][0].update(contact_id=8), "document contacts copied"),
        (lambda w: w["Invoice"][0].update(delivery_contact_id=8), "document contacts copied"),
        (lambda w: w["items"][2].pop(), "items copied"),
        (lambda w: w["items"][1].pop(), "items copied"),
        (lambda w: w["items"][2][0].update(pos=2), "item positions"),
        (lambda w: w["items"][2].reverse(), "item positions"),
    ],
)
def test_saved_invoice_differences_stop_the_run(booked, spoil, failed):
    _, w, _ = booked
    spoil(w)
    assert_failed(booked, failed)


@pytest.mark.parametrize(
    "field, value",
    [
        ("product_id", 99),
        ("sku", "Z"),
        ("name", "Other chair"),
        ("qty", D("2.001")),
        ("price", D("250.001")),
        ("rebate", D("-0.11")),
        ("vat_value", D("0.20")),
    ],
)
@pytest.mark.parametrize("document", [1, 2])
def test_each_line_field_must_equal_the_order_even_when_totals_match(booked, field, value, document):
    _, w, _ = booked
    w["items"][document][0][field] = value
    assert_failed(booked, f"item 1 {field} copied")


@pytest.mark.parametrize("role, index", [("invoice", 1), ("delivery", 0)])
@pytest.mark.parametrize(
    "field",
    [
        "contact_id",
        "address_id",
        "company",
        "first_name",
        "name",
        "street",
        "zip",
        "city",
        "country",
        "name_addon",
        "city_addon",
        "manual_address",
        "description",
        "title",
        "email",
        "phone",
    ],
)
def test_each_address_snapshot_field_must_be_copied(booked, role, index, field):
    _, w, _ = booked
    w["receivers"][2][index][field] = "different"
    assert_failed(booked, f"{role} address copied")


@pytest.mark.parametrize("role, index", [("invoice", 1), ("delivery", 0)])
@pytest.mark.parametrize("duplicate", [False, True])
def test_missing_or_duplicate_address_role_fails(booked, role, index, duplicate):
    _, w, _ = booked
    rows = w["receivers"][2]
    if duplicate:
        rows.append(dict(rows[index]))
    else:
        rows.pop(index)
    assert_failed(booked, f"{role} address copied")


def test_shared_address_without_separate_delivery_receiver_passes(booked):
    ctx, w, _ = booked
    w["receivers"][1].pop(1)
    w["receivers"][2].pop(0)
    invoice.verify(ctx)


def test_repeated_sku_lines_are_matched_by_position(booked):
    ctx, w, _ = booked
    for lines in w["items"].values():
        lines[1].update(sku=lines[0]["sku"], product_id=lines[0]["product_id"])
    invoice.verify(ctx)
    w["items"][2][0]["qty"], w["items"][2][1]["qty"] = w["items"][2][1]["qty"], w["items"][2][0]["qty"]
    assert_failed(booked, "item 1 qty copied")


def test_catalogue_price_remainder_is_not_rounded_for_copy_check(booked):
    ctx, w, _ = booked
    for lines in w["items"].values():
        lines[0]["price"] = D("9.901176470588236")
    invoice.verify(ctx)
    w["items"][2][0]["price"] = D("9.90")
    assert_failed(booked, "item 1 price copied")


@pytest.mark.parametrize("missing", [None, "date", "service_date", "both"])
def test_missing_proposed_dates_are_recorded_as_unverified(booked, missing):
    ctx, w, records = booked
    if missing is None:
        ctx.cache.clear()
    else:
        for key in ("date", "service_date") if missing == "both" else (missing,):
            ctx.cache["invoice_dates"][key] = None
    invoice.verify(ctx)
    assert records[-1][0] == ("5.5 verify", "ok")
    expected = {
        None: ["invoice date unchanged", "service date unchanged"],
        "both": ["invoice date unchanged", "service date unchanged"],
        "date": ["invoice date unchanged"],
        "service_date": ["service date unchanged"],
    }[missing]
    assert records[-1][1]["unverified"] == expected
    # Skipping one unavailable proposal must not disable the other date check.
    if missing == "date":
        w["fields"][2]["service_date"] = "2026-07-14"
        assert_failed(booked, "service date unchanged")
    elif missing == "service_date":
        w["Invoice"][0]["date"] = "2026-07-14"
        assert_failed(booked, "invoice date unchanged")


@pytest.mark.parametrize("dtype", ["Order", "Invoice"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_document_count_failure_is_recorded(booked, dtype, duplicate):
    ctx, w, records = booked
    w[dtype] = w[dtype] * 2 if duplicate else []
    with pytest.raises(AutomationError, match="expected 1 Invoice"):
        invoice.verify(ctx)
    assert records[-1][0] == ("5.5 verify", "FAIL")
    assert records[-1][1]["failed"] == ["document count"]


@pytest.mark.parametrize(
    "pay_date, paid_value, failed",
    [(None, None, None), (None, D(0), None), ("2026-07-18", None, "pay date"), (None, D("0.01"), "paid value")],
)
def test_unpaid_invoice_requires_no_payment_date_or_value(booked, pay_date, paid_value, failed):
    ctx, w, _ = booked
    ctx.order.payment.status = PaidStatus.UNPAID
    ctx.order.payment.paid_date = None
    w["Invoice"][0].update(paid=False, pay_date=pay_date, paid_value=paid_value)
    if failed:
        assert_failed(booked, failed)
    else:
        invoice.verify(ctx)


def test_unpaid_invoice_checks_copied_fields_without_inventing_payment(booked):
    ctx, w, _ = booked
    ctx.order.payment.status = PaidStatus.UNPAID
    ctx.order.payment.paid_date = None
    w["Invoice"][0].update(paid=False, pay_date=None, paid_value=None)
    invoice.verify(ctx)
    w["items"][2][0]["qty"] = D(3)
    assert_failed(booked, "item 1 qty copied")


@pytest.mark.parametrize("label", ["Date", "Service date"])
@pytest.mark.parametrize("shown", ["5 Nov 2026", "unreadable", "31.02.2026", None])
def test_capture_dates_only_reads_and_records(monkeypatch, label, shown):
    records, reads = [], []
    displayed = {"Date": "06.11.2026", "Service date": "5 Nov 2026", label: shown}

    def labelled(label, control_type, root):
        reads.append((label, control_type, root))
        if displayed[label] is None:
            raise AutomationError(f"label {label!r} not found")
        return displayed[label]

    ctx = NS(
        invoice_no="INV1",
        cache={"invoice_dates": {"stale": True}},
        warnings=[],
        ui=NS(labelled=labelled),
        record=lambda *a, **k: records.append((a, k)),
    )
    monkeypatch.setattr(invoice, "read", lambda c: c)
    invoice._remember_proposed_dates(ctx, "editor")
    expected = dict(number="INV1", date="2026-11-06", service_date="2026-11-05")
    key = "date" if label == "Date" else "service_date"
    if shown == "5 Nov 2026":
        expected[key] = "2026-11-05"
        assert records[-1][0] == ("5.1 proposed dates", "ok")
        assert ctx.warnings == []
    else:
        expected[key] = None
        assert records[-1][0] == ("5.1 proposed dates", "warn")
        assert len(ctx.warnings) == 1
        assert f"cannot capture proposed invoice {label}" in ctx.warnings[0]
        assert "unverified" in ctx.warnings[0]
        assert records[-1][1]["warnings"] == ctx.warnings
    assert ctx.cache["invoice_dates"] == expected
    assert records[-1][1][key] == expected[key]
    assert reads == [("Date", "Edit", "editor"), ("Service date", "Edit", "editor")]


def test_header_query_filters_deleted_and_normalizes_date(monkeypatch):
    def query(sql, params):
        assert sql.startswith('SELECT "SERVICEDATE", "NETGROSS", "FK_NOVATREF"')
        assert '"ID" = ?' in sql and db.live() in sql
        assert params == (2,)
        return [(date(2026, 11, 5), 1, None)]

    monkeypatch.setattr(db, "query", query)
    assert db.document_copy_fields(2) == dict(service_date="2026-11-05", net_gross=1, no_vat_ref=None)
    monkeypatch.setattr(db, "query", lambda *a: [])
    assert db.document_copy_fields(2) is None
    monkeypatch.setattr(db, "query", lambda *a: [(None, 1, None)])
    assert db.document_copy_fields(2)["service_date"] is None


def test_receiver_query_reads_snapshot_identity_and_full_address(monkeypatch):
    row = (
        0,
        7,
        10,
        "Co",
        "A",
        "B",
        "Main 1",
        "10117",
        "Berlin",
        "DE",
        "Annex",
        "District",
        "Override",
        "Specification",
        "Dr",
        "a@example.com",
        "123",
    )

    def query(sql, params):
        assert sql.startswith("SELECT ") and 'FROM "FKT_DOCUMENTRECEIVER"' in sql
        assert '"FK_DOCUMENT" = ?' in sql and db.live() in sql
        assert params == (2,)
        return [row]

    monkeypatch.setattr(db, "query", query)
    assert db.document_receiver_details(2) == [
        dict(
            type=0,
            contact_id=7,
            address_id=10,
            company="Co",
            first_name="A",
            name="B",
            street="Main 1",
            zip="10117",
            city="Berlin",
            country="DE",
            name_addon="Annex",
            city_addon="District",
            manual_address="Override",
            description="Specification",
            title="Dr",
            email="a@example.com",
            phone="123",
        )
    ]
