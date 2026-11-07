"""Brief §4.6-§5: linked Invoice from the saved Order, payment status, verify."""

from __future__ import annotations

import time

from image2cash.models import PaidStatus
from image2cash.steps.context import Ctx
from image2cash.steps.order import read_totals
from image2cash.ui.core import AutomationError, ManualReview, _visible, read, wait_for
from image2cash.ui.datefield import parse_display, set_date
from image2cash.validate import close, money
from image2cash.verify import db


def create_and_pay(ctx: Ctx) -> None:
    ui, o = ctx.ui, ctx.order
    ctx.back_to_order()
    ed = ui.editor_pane()
    group = ui.find("Create a follow-up document", "Group", root=ed)
    btn = ui.find("Invoice", "Button", root=group)  # 4.6: follow-up area, not the toolbar
    shot = ctx.shot("followup_invoice", [(btn, "follow-up: Invoice")])
    btn.invoke()
    wait_for(lambda: ui.active_editor().lstrip("*") == "New Invoice", 10, what="New Invoice editor")  # 4.7
    ed = ui.editor_pane()
    ctx.invoice_no = read(ui.labelled("No.", "Edit", root=ed))  # 5.1: unchanged
    _remember_proposed_dates(ctx, ed)
    ui.learn_locale(read(ui.find("Total", "Edit", root=ed)))

    # 5.1 copied fields
    copied = {
        "cust_ref": read(ui.labelled("Cust.Ref.", "Edit", root=ed)),
        "totals": read_totals(ctx),
    }
    if copied["cust_ref"] != o.external_ref:
        raise ManualReview(f"invoice Cust.Ref. {copied['cust_ref']!r} != {o.external_ref!r}")
    if not close(copied["totals"]["total"], o.gross_total, o.items):
        raise ManualReview(f"invoice total {copied['totals']['total']} != {o.gross_total}")
    ctx.record("5.1 invoice opened", "ok", number=ctx.invoice_no, shot=shot, **copied)

    # 5.2 payment method on the invoice
    pay_rows = db.payments_named(o.payment.method)
    if len(pay_rows) != 1:
        raise ManualReview(f"payment method {o.payment.method!r} not uniquely available")
    pay_text = (pay_rows[0]["description"] or pay_rows[0]["name"]).strip()
    paid_box = ui.find("paid", "CheckBox", root=ed)
    pr = paid_box.rectangle()
    row_combos = sorted(
        [c for c in ed.descendants(control_type="ComboBox") if _visible(c) and abs(c.rectangle().top - pr.top) < 12],
        key=lambda c: c.rectangle().left,
    )
    if not row_combos:
        raise AutomationError("payment-method combo next to 'paid' not found")
    pay_combo = row_combos[0]
    options = [i.strip() for i in ui.combo_items(pay_combo)]
    current = ui.combo_value(pay_combo).strip()
    if current not in (pay_text, o.payment.method):
        target = pay_text if pay_text in options else (o.payment.method if o.payment.method in options else None)
        if target is None:
            raise ManualReview(f"invoice offers no {o.payment.method!r}: {options}")
        ui.combo_select(pay_combo, target)

    # 5.3 paid status
    if o.payment.status is PaidStatus.PAID:
        if not paid_box.get_toggle_state():
            paid_box.toggle()
        wait_for(lambda: paid_box.get_toggle_state() == 1, 3, what="paid checked")
        time.sleep(0.8)  # the date and Value fields appear after checking
        ed = ui.editor_pane()
        date_edit = _paid_date_field(ctx, ed, pr)
        set_date(ui, date_edit, o.payment.paid_date)
        total = read_totals(ctx)["total"]
        value = ui.labelled("Value", "Edit", root=ed)
        ui.set_amount(value, money(total))
        ctx.record(
            "5.3 paid",
            "ok",
            method=ui.combo_value(pay_combo),
            date=read(date_edit),
            value=read(value),
            shot=ctx.shot(
                "invoice_paid", [(paid_box, "paid"), (pay_combo, "payment"), (date_edit, "date"), (value, "Value")]
            ),
        )
    else:
        if paid_box.get_toggle_state():
            paid_box.toggle()
        ctx.record("5.3 not paid", "ok", paid_status=o.payment.status.value)

    from image2cash.steps.order import _select_address_tab

    _select_address_tab(ctx, "Invoice address")  # same 2.2.0 save bug as the Order
    ui.save()  # 5.4
    wait_for(
        lambda: not ui.active_editor().startswith("*") and ui.active_editor() != "New Invoice", 10, what="invoice saved"
    )
    verify(ctx)


def _paid_date_field(ctx: Ctx, ed, paid_rect):
    """The date edit on the 'paid' row (unnamed); it shows a parseable date."""
    from image2cash.ui.datefield import parse_display

    cands = [
        c
        for c in ed.descendants(control_type="Edit")
        if _visible(c) and abs(c.rectangle().top - paid_rect.top) < 12 and c.rectangle().left > paid_rect.right
    ]
    cands.sort(key=lambda c: c.rectangle().left)
    for c in cands:
        if parse_display(read(c)) is not None:
            return c
    raise AutomationError(f"payment date field not found next to 'paid': {[read(c) for c in cands]}")


def _remember_proposed_dates(ctx: Ctx, ed) -> None:
    """Read the proposed dates once, before payment edits; never set either field."""
    ctx.cache.pop("invoice_dates", None)
    dates = {}
    warnings = []
    for key, label in (("date", "Date"), ("service_date", "Service date")):
        dates[key] = None
        try:
            shown = read(ctx.ui.labelled(label, "Edit", root=ed))
            value = parse_display(shown)
            if value is None:
                raise AutomationError(f"invoice {label} is unreadable: {shown!r}")
            dates[key] = value.isoformat()
        except (AutomationError, ValueError) as exc:
            message = f"cannot capture proposed invoice {label}; unchanged date will be unverified: {exc}"
            ctx.warnings.append(message)
            warnings.append(message)
    ctx.cache["invoice_dates"] = dict(number=ctx.invoice_no, **dates)
    ctx.record("5.1 proposed dates", "warn" if warnings else "ok", number=ctx.invoice_no, warnings=warnings, **dates)


def _copied_receivers(invoice: list[dict], order: list[dict]) -> dict[str, bool]:
    # FKT_DOCUMENTRECEIVER.BILLINGTYPE follows the document kind: Order=3,
    # Invoice=0. A separate delivery snapshot has type=5 on both documents.
    checks = {
        "address roles": all(r["type"] in (0, 5) for r in invoice) and all(r["type"] in (3, 5) for r in order),
    }
    for label, inv_type, order_type in (("invoice address", 0, 3), ("delivery address", 5, 5)):
        inv_rows = [{k: v for k, v in r.items() if k != "type"} for r in invoice if r["type"] == inv_type]
        src_rows = [{k: v for k, v in r.items() if k != "type"} for r in order if r["type"] == order_type]
        # A shared billing/delivery address may have only the primary receiver.
        checks[f"{label} copied"] = (
            len(inv_rows) == len(src_rows) == 1 or (inv_type == 5 and not inv_rows and not src_rows)
        ) and inv_rows == src_rows
    return checks


def verify(ctx: Ctx) -> None:
    """5.5: Invoice persisted with state/total/link; source Order unchanged."""
    o = ctx.order
    inv = db.documents_with_ref(o.external_ref, "Invoice")
    orders = db.documents_with_ref(o.external_ref, "Order")
    if len(inv) != 1 or len(orders) != 1:
        ctx.record("5.5 verify", "FAIL", failed=["document count"], invoice=inv, order=orders)
        raise AutomationError(f"expected 1 Invoice + 1 Order for {o.external_ref}, DB has {len(inv)} + {len(orders)}")
    i, so = inv[0], orders[0]
    inv_items, order_items = db.document_items(i["id"]), db.document_items(so["id"])
    inv_receivers = db.document_receiver_details(i["id"])
    order_receivers = db.document_receiver_details(so["id"])
    inv_fields, order_fields = db.document_copy_fields(i["id"]), db.document_copy_fields(so["id"])
    proposed = ctx.cache.get("invoice_dates", {})
    pay = db.payment(i["payment_id"]) if i["payment_id"] else None
    paid = o.payment.status is PaidStatus.PAID
    from image2cash.steps.debtor import is_debtor_of

    checks = {
        "invoice number": i["number"] == ctx.invoice_no,
        "invoice cust.ref": i["cust_ref"] == so["cust_ref"] == o.external_ref,
        "invoice debtor": is_debtor_of(ctx, i["id"]),
        "linked to order": i["src_id"] == so["id"],
        "invoice total": close(i["total"], o.gross_total, o.items),
        "payment method": pay is not None and pay["name"] == o.payment.method,
        "paid flag": bool(i["paid"]) == paid,
        "pay date": (i["pay_date"] == o.payment.paid_date.isoformat()) if paid else i["pay_date"] is None,
        "paid value": (money(i["paid_value"]) == money(i["total"])) if paid else i["paid_value"] in (None, 0),
        "order total unchanged": money(so["total"]) == money(i["total"]),
        "order still open": not so["paid"],
        "order cust.ref": so["cust_ref"] == o.external_ref,
        "order date copied": i["order_date"] == so["order_date"] == so["date"] == o.order_date.isoformat(),
        "invoice modes copied": inv_fields is not None
        and order_fields is not None
        and all(inv_fields[k] == order_fields[k] for k in ("net_gross", "no_vat_ref")),
        "document contacts copied": all(i[k] == so[k] for k in ("contact_id", "delivery_contact_id")),
        "items copied": len(inv_items) == len(order_items) == len(o.items),
        "item positions": [r["pos"] for r in inv_items] == [r["pos"] for r in order_items]
        and len({r["pos"] for r in order_items}) == len(order_items),
    }
    unverified = []
    for key, check, saved in (
        ("date", "invoice date unchanged", i["date"]),
        ("service_date", "service date unchanged", inv_fields["service_date"] if inv_fields else None),
    ):
        if proposed.get(key) is None:
            unverified.append(check)
        else:
            checks[check] = proposed.get("number") == i["number"] and saved == proposed[key]
    for src, it in zip(order_items, inv_items):
        for key in ("product_id", "sku", "name", "qty", "price", "rebate", "vat_value"):
            # Compare persisted values exactly, including catalogue price remainders.
            checks[f"item {src['pos']} {key} copied"] = src[key] is not None and it[key] == src[key]
    checks.update(_copied_receivers(inv_receivers, order_receivers))
    ctx.ui.show_documents("Invoices")
    shot = ctx.shot("documents_final")
    details = dict(
        invoice=i,
        order=so,
        payment=pay,
        invoice_items=inv_items,
        order_items=order_items,
        invoice_receivers=inv_receivers,
        order_receivers=order_receivers,
        invoice_fields=inv_fields,
        order_fields=order_fields,
        proposed_dates=proposed,
        unverified=unverified,
        shot=shot,
    )
    failed = [k for k, v in checks.items() if not v]
    if failed:
        ctx.record("5.5 verify", "FAIL", failed=failed, **details)
        raise AutomationError(f"final verification failed: {failed}")
    ctx.record("5.5 verify", "ok", **details)
