"""Brief §1 (open the Order first) and §4 (complete and save the Order)."""

from __future__ import annotations

import time
from decimal import Decimal

from image2cash.steps.context import Ctx
from image2cash.ui.core import AutomationError, ManualReview, read, wait_for
from image2cash.ui.datefield import set_date
from image2cash.validate import close, money, parse_amount, totals
from image2cash.verify import db

# shortcut: sign -> ISO code for the locales seen so far; extend when another currency shows up.
CURRENCY_SIGNS = {"$": "USD", "€": "EUR", "£": "GBP", "؋": "AFN"}


def booked_currency(display: str) -> str | None:
    """ISO code of the currency sign Fakturama shows in an amount field, if known."""
    return next((CURRENCY_SIGNS[c] for c in display if c in CURRENCY_SIGNS), None)


def preflight(ctx: Ctx) -> None:
    """Idempotency: never create a second Order/Invoice for the same reference.

    A stopped run continues by itself: if exactly one saved Order exists for this
    reference and no Invoice, the Order is verified against the source (lines,
    totals, debtor) and the flow resumes at the Invoice step.
    """
    existing = db.documents_with_ref(ctx.order.external_ref)
    if not existing:
        ctx.record("preflight", "ok", existing=0)
        return
    orders = [d for d in existing if d["dtype"] == "Order"]
    others = [d for d in existing if d["dtype"] != "Order"]
    if len(orders) == 1 and not others:
        ctx.order_no = orders[0]["number"]
        ctx.resume_at = "invoice"
        ctx.record("preflight", "resume", order=ctx.order_no)
        return
    raise ManualReview(
        f"documents with Cust.Ref. {ctx.order.external_ref!r} already exist: "
        + ", ".join(f"{d['dtype']} {d['number']}" for d in existing)
    )


def open_order(ctx: Ctx) -> None:
    ui, o = ctx.ui, ctx.order
    ui.focus()
    ui.press("Create: New Order")  # brief 1.3
    wait_for(lambda: ui.active_editor().lstrip("*") == "New Order", 10, what="New Order editor")
    ed = ui.editor_pane()

    total = ui.labelled("Total", "Edit", root=ed)
    ui.learn_locale(read(total))
    booked = booked_currency(read(total))
    if booked and booked != o.currency:  # stop before typing anything into the new Order
        raise ManualReview(f"order is in {o.currency}, Fakturama books in {booked}")

    no = ui.labelled("No.", "Edit", root=ed)
    ctx.order_no = read(no)  # 1.4: leave the proposed number unchanged
    if not ctx.order_no:
        raise AutomationError("New Order has no proposed number")

    date_field = ui.labelled("Date", "Edit", root=ed)
    set_date(ui, date_field, o.order_date)  # 1.5

    ref = ui.labelled("Cust.Ref.", "Edit", root=ed)
    ui.set_text(ref, o.external_ref)  # 1.6

    mode = ui.labelled("Date", "ComboBox", root=ed)  # the Net/Gross combo sits right of Date
    if set(i.strip() for i in ui.combo_items(mode)) >= {"Net", "Gross"}:
        ui.combo_select(mode, "Net")  # 1.7
    else:
        raise AutomationError("price-mode combo not found next to Date")
    vat = ui.labelled("VAT", "ComboBox", root=ed)
    if ui.combo_value(vat).strip() != "With VAT":
        ui.combo_select(vat, "With VAT")

    # More rows visible before the item table needs its own scrollbar.
    maxi = [b for b in ui._editor_folder().descendants(control_type="Button") if b.element_info.name == "Maximize"]
    if maxi:
        maxi[0].invoke()
    ctx._order_pane = ui.editor_pane().handle  # this run's own tab; others may share its title
    ctx.record(
        "1 open order",
        "ok",
        number=ctx.order_no,
        date=read(date_field),
        cust_ref=read(ref),
        price_mode=ui.combo_value(mode),
        vat_mode=ui.combo_value(vat),
        decimal_sep=ui.decimal_sep,
        shot=ctx.shot(
            "order_header",
            [(no, "No. (unchanged)"), (date_field, "Date"), (ref, "Cust.Ref."), (mode, "Net"), (vat, "With VAT")],
        ),
    )


def read_totals(ctx: Ctx) -> dict[str, Decimal | None]:
    ui = ctx.ui
    ed = ui.editor_pane()
    # The fields do not move while an editor is open; finding all four costs ~0.75 s, so
    # they are found once per editor (keyed by its pane: the Invoice has its own totals).
    found = ctx.cache.setdefault(("totals", ed.handle), {})
    out = {}
    # The totals are UIA Edits named after their labels. Find them by name:
    # 'VAT' is also the label of the VAT-mode combo, so label anchoring is ambiguous.
    for key, label in (("net", "Total Net"), ("vat", "VAT"), ("total", "Total"), ("discount", "Discount")):
        try:
            ctrl = found.get(key)
            if ctrl is None:
                ctrl = found[key] = ui.find(label, "Edit", root=ed, timeout=2)
            out[key] = parse_amount(read(ctrl))
        except Exception:
            found.pop(key, None)  # stale or missing: find it again next time
            out[key] = None
    return out


def complete_and_save(ctx: Ctx) -> None:
    """Brief 4.1-4.5."""
    ui, o = ctx.ui, ctx.order
    ctx.back_to_order()
    ed = ui.editor_pane()

    # 4.2 order-level discount 0 % and free shipping unless the image says otherwise
    disc = ui.labelled("Discount", "Edit", root=ed)
    if parse_amount(read(disc)) != 0:
        ui.set_text(disc, "0", check=lambda v: parse_amount(v) == 0)
    ship = ui.labelled("Shipping", "ComboBox", root=ed)
    if ui.combo_value(ship).strip() != "Free of shipping costs":
        ui.combo_select(ship, "Free of shipping costs")

    # 4.3 totals must match the source
    net, vat, gross = totals(o)
    got = read_totals(ctx)
    problems = [
        f"{k}: UI {got[k]} vs source {want}"
        for k, want in (("net", o.net_total), ("vat", o.vat_total), ("total", o.gross_total))
        if not close(got[k], want, o.items)
    ]
    shot = ctx.shot("order_complete")
    if problems:
        ctx.record("4.3 totals", "FAIL", problems=problems, shot=shot)
        raise ManualReview("order totals differ: " + "; ".join(problems))
    ctx.record("4.3 totals", "ok", ui=got, computed=(net, vat, gross), shot=shot)

    # Calibrated Fakturama 2.2.0 bug: saving while the dynamically added
    # 'Delivery address' tab is the active address tab throws an NPE in
    # DocumentEditor.doSave ("Failed to persist contents of part"). Select the
    # Invoice address tab first.
    from image2cash.steps.debtor import verify_order_addresses

    verify_order_addresses(ctx)  # 2.4; leaves the Invoice address tab active, as the save bug needs
    ui.save()  # 4.4
    wait_for(lambda: ui.active_editor() == ctx.order_no, 10, what="order saved (tab title = number, no '*')")
    verify_saved_order(ctx)


def _select_address_tab(ctx: Ctx, name: str) -> None:
    from image2cash.ui.core import _visible

    ed = ctx.ui.editor_pane()
    tabs = [c for c in ed.descendants(control_type="TabItem") if c.element_info.name == name and _visible(c)]
    if tabs:
        ctx.ui.click(tabs[0])
        time.sleep(0.4)


def discount_ok(rebate, pct: Decimal) -> bool:
    """ITEMREBATE is a negative fraction (5 % is stored as -0.05, seen in the test DB).
    One scale only, so a 100x error cannot pass."""
    return abs(abs(rebate or Decimal(0)) * 100 - pct) < Decimal("0.01")


def verify_saved_order(ctx: Ctx) -> None:
    """4.5: the saved Order as persisted (also the entry check when resuming)."""
    ui, o = ctx.ui, ctx.order
    docs = db.documents_with_ref(o.external_ref, "Order")
    if len(docs) != 1:
        raise AutomationError(f"expected one saved Order, DB has {len(docs)}")
    d = docs[0]
    items = db.document_items(d["id"])
    from image2cash.steps.debtor import is_debtor_of

    checks = {
        "number": d["number"] == ctx.order_no,
        "debtor": is_debtor_of(ctx, d["id"]),
        "date": d["date"] == o.order_date.isoformat(),
        "total": close(d["total"], o.gross_total, o.items),
        "items": len(items) == len(o.items),
    }
    for src, it in zip(o.items, items):
        checks[f"item {src.pos} sku"] = it["sku"] == src.sku
        checks[f"item {src.pos} qty"] = money(it["qty"]) == money(src.qty)
        checks[f"item {src.pos} price"] = abs(it["price"] - src.unit_net) < Decimal("0.005")
        checks[f"item {src.pos} discount"] = discount_ok(it["rebate"], src.discount_pct)
        checks[f"item {src.pos} vat"] = abs(it["vat_value"] * 100 - src.vat_pct) < Decimal("0.001")
    failed = [k for k, v in checks.items() if not v]
    ui.nav("Documents")
    shot = ctx.shot("documents_after_order")
    if failed:
        ctx.record("4.5 order in DB", "FAIL", failed=failed, db=d, items=items, shot=shot)
        raise AutomationError(f"saved Order differs from source: {failed}")
    ctx.record("4.5 order in DB", "ok", id=d["id"], number=d["number"], total=d["total"], items=len(items), shot=shot)
