"""Brief §4.6-§5: linked Invoice from the saved Order, payment status, verify."""

from __future__ import annotations

import time

from image2cash.models import PaidStatus
from image2cash.steps.context import Ctx
from image2cash.steps.order import read_totals
from image2cash.ui.core import AutomationError, ManualReview, _visible, read, wait_for
from image2cash.ui.datefield import set_date
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
    ui.learn_locale(read(ui.find("Total", "Edit", root=ed)))

    # 5.1 copied fields
    copied = {
        "cust_ref": read(ui.labelled("Cust.Ref.", "Edit", root=ed)),
        "totals": read_totals(ctx),
    }
    if copied["cust_ref"] != o.external_ref:
        raise ManualReview(f"invoice Cust.Ref. {copied['cust_ref']!r} != {o.external_ref!r}")
    if not close(copied["totals"]["total"], o.gross_total, len(o.items)):
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


def verify(ctx: Ctx) -> None:
    """5.5: Invoice persisted with state/total/link; source Order unchanged."""
    o = ctx.order
    inv = db.documents_with_ref(o.external_ref, "Invoice")
    orders = db.documents_with_ref(o.external_ref, "Order")
    if len(inv) != 1 or len(orders) != 1:
        raise AutomationError(f"expected 1 Invoice + 1 Order for {o.external_ref}, DB has {len(inv)} + {len(orders)}")
    i, so = inv[0], orders[0]
    pay = db.payment(i["payment_id"]) if i["payment_id"] else None
    paid = o.payment.status is PaidStatus.PAID
    checks = {
        "invoice number": i["number"] == ctx.invoice_no,
        "linked to order": i["src_id"] == so["id"],
        "invoice total": close(i["total"], o.gross_total, len(o.items)),
        "payment method": pay is not None and pay["name"] == o.payment.method,
        "paid flag": bool(i["paid"]) == paid,
        "pay date": (i["pay_date"] == o.payment.paid_date.isoformat()) if paid else True,
        "paid value": (money(i["paid_value"]) == money(i["total"])) if paid else True,
        "order total unchanged": money(so["total"]) == money(i["total"]),
        "order still open": not so["paid"],
        "order cust.ref": so["cust_ref"] == o.external_ref,
        "items copied": len(db.document_items(i["id"])) == len(o.items),
    }
    ctx.ui.nav("Documents")
    shot = ctx.shot("documents_final")
    failed = [k for k, v in checks.items() if not v]
    if failed:
        ctx.record("5.5 verify", "FAIL", failed=failed, invoice=i, order=so, payment=pay, shot=shot)
        raise AutomationError(f"final verification failed: {failed}")
    ctx.record("5.5 verify", "ok", invoice=i, order=so, payment=pay, shot=shot)
