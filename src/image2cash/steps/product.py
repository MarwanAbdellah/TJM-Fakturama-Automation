"""Brief §3: select or create each Product (and its VAT), then complete the line."""

from __future__ import annotations

import logging
import time
from decimal import Decimal

from image2cash.models import Item
from image2cash.steps import selector as S
from image2cash.steps.context import Ctx
from image2cash.steps.order import read_totals
from image2cash.ui import grid as G
from image2cash.ui.core import AutomationError, ManualReview, read, wait_for
from image2cash.validate import close, gross_price, line_net, money, parse_amount, vat_name
from image2cash.verify import db

SELECT_HEADERS = ["Item No.", "Name", "Description", "Stock", "Price", "VAT"]
ITEM_HEADERS = ["Pos.", "Qty.", "Item No.", "Picture", "Name", "Description", "VAT", "U.Price", "Discount", "Price"]
TITLE = "Select a product"
log = logging.getLogger("image2cash.product")


def all_items(ctx: Ctx) -> None:
    for item in ctx.order.items:  # 3.1 / 3.17, in source order
        found = select_or_create(ctx, item)
        complete_line(ctx, item, found)


def select_or_create(ctx: Ctx, item: Item) -> tuple[G.Grid, G.Row]:
    """Put the item on a new Order line; returns the grid read and row that proved it."""
    rows = db.products_with_sku(item.sku)
    if len(rows) > 1:
        raise ManualReview(f"{len(rows)} products with SKU {item.sku}")
    if rows:
        _check_master(ctx, item, rows[0])  # before the line exists: a mismatch leaves no dirty line
    pos = ctx.order.items.index(item) + 1  # the Order is new, so line n has Pos. n
    dialog, icon = S.open_selector(ctx, "Items", TITLE)  # 3.2
    grid = S.search(ctx, dialog, item.sku, SELECT_HEADERS)  # 3.3
    shot = ctx.shot(f"product_selector_{item.sku}", [(icon, "select product")])
    if grid is None:
        if len(rows) != 1:
            raise ManualReview(f"selector auto-accepted a product for {item.sku} that is not an exact SKU match")
        how = "auto-accepted unique row"
    elif len(rows) == 1:
        S.pick(ctx, dialog, grid, lambda r: sku_eq(r.get("Item No."), item.sku), "Item No.")
        how = "picked exact row"
    else:
        if any(sku_eq(r.get("Item No."), item.sku) for r in grid.rows):
            raise ManualReview(f"grid shows SKU {item.sku} but the DB has no live product with it")
        S.press(dialog, "Cancel")
        ctx.record(f"3.3 product {item.sku}", "missing", rows_seen=len(grid.rows), shot=shot)
        create(ctx, item)
        return reselect(ctx, item, pos)
    found = _check_new_row(ctx, item, pos)
    ctx.record(f"3.3 product {item.sku}", "ok", how=how, product=rows[0]["id"], shot=shot)
    return found


def reselect(ctx: Ctx, item: Item, pos: int) -> tuple[G.Grid, G.Row]:
    """3.12: pick the newly saved product from the still-open Order."""
    rows = db.products_with_sku(item.sku)
    if len(rows) != 1:
        raise ManualReview(f"after saving, {len(rows)} products with SKU {item.sku}")
    dialog, _ = S.open_selector(ctx, "Items", TITLE)
    grid = S.search(ctx, dialog, item.sku, SELECT_HEADERS)
    if grid is not None:
        S.pick(ctx, dialog, grid, lambda r: sku_eq(r.get("Item No."), item.sku), "Item No.")
    found = _check_new_row(ctx, item, pos)
    ctx.record(f"3.12 product {item.sku} reselected", "ok", product=rows[0]["id"])
    return found


def _items_grid(ctx: Ctx):
    """The Order's item table: the pane to the right of the 'Items' label column."""
    ui = ctx.ui
    lab = ui.find("Items", "Text", root=ui.editor_pane())
    holder = lab.parent()
    hr = holder.rectangle()
    panes = [
        c
        for c in holder.parent().children()
        if c.element_info.control_type == "Pane"
        and c.rectangle().left >= hr.right - 2
        and abs(c.rectangle().top - hr.top) < 6
    ]
    if not panes:
        raise AutomationError("Order item table not found")
    return panes[0]


sku_eq = S.ocr_eq


def _scroll_to_bottom(ctx: Ctx, pane) -> None:
    """The item table gets its own scrollbar after ~7 rows (calibrated). Its scrollbar
    buttons ignore UIA Invoke; the mouse wheel over the grid scrolls the table itself.
    Guard: if the 'Items' label moved, the outer editor scrolled instead."""
    from pywinauto import mouse

    r = pane.rectangle()
    label = lambda: ctx.ui.find("Items", "Text", root=ctx.ui.editor_pane()).rectangle().top  # noqa: E731
    before = label()
    ctx.ui.focus()
    mouse.scroll(coords=((r.left + r.right) // 2, (r.top + r.bottom) // 2), wheel_dist=-10)
    time.sleep(0.4)
    if label() != before:
        raise AutomationError("mouse wheel scrolled the Order editor, not the item table")


def last_line(ctx: Ctx) -> tuple[G.Grid, G.Row]:
    """The bottom-most fully visible item row (the newest line)."""
    ctx.back_to_order()
    pane = _items_grid(ctx)
    _scroll_to_bottom(ctx, pane)
    r = pane.rectangle()
    r.bottom = min(r.bottom, ctx.ui._editor_folder().rectangle().bottom)  # the table may extend below the editor

    def probe():  # right after an Enter the table can repaint mid-capture: retry
        g = G.read_grid(r, ITEM_HEADERS, owner=ctx.ui.main)
        rows = [x for x in g.rows if x.get("Item No.") and x.y < r.bottom - 8]  # drop a half-clipped bottom row
        return (g, rows[-1]) if rows else None

    return wait_for(probe, 6, what="item rows")


def _row_pos(row: G.Row) -> int | None:
    return int(row.get("Pos.")) if row.get("Pos.").isdigit() else None


def is_line(pos: int, seen: int | None, above: int | None, has_above: bool) -> bool:
    """Is the bottom row line ``pos``? Pos. on the blue selected row is sometimes
    unreadable; then the row above must be pos-1 (or there is none for line 1)."""
    if seen is not None:
        return seen == pos
    return (pos == 1 and not has_above) or (has_above and above == pos - 1)


def _check_new_row(ctx: Ctx, item: Item, pos: int) -> tuple[G.Grid, G.Row]:
    def probe():
        g, row = last_line(ctx)
        above = [x for x in g.rows if x.get("Item No.") and x.y < row.y]
        ok = is_line(pos, _row_pos(row), _row_pos(above[-1]) if above else None, bool(above))
        return (g, row) if ok else None

    g, row = wait_for(probe, 8, what=f"Order line {pos} for {item.sku}")
    if not sku_eq(row.get("Item No."), item.sku):
        raise ManualReview(f"Order line {pos} shows {row.get('Item No.')!r}, expected {item.sku}")
    return g, row


def _check_master(ctx: Ctx, item: Item, row: dict) -> None:
    """Brief 3.14 'confirm': the catalogue owns price and VAT. The order line inherits
    both and is never overwritten; a different image price stops for a human, who
    changes the product in Fakturama if the price really changed."""
    if row["vat_value"] is None or abs(Decimal(str(row["vat_value"])) * 100 - item.vat_pct) > Decimal("0.001"):
        raise ManualReview(
            f"product {item.sku} has VAT {row['vat_name']}, source says {item.vat_pct}%"
            " (line VAT override not automated)"
        )
    catalogue = None if row["price"] is None else money(Decimal(str(row["price"])))
    if catalogue != item.unit_net:
        raise ManualReview(
            f"product {item.sku}: catalogue net price {catalogue} differs from the order's {item.unit_net}."
            " Prices live in the product catalogue: update the product in Fakturama, or correct the order."
        )


# ---------------------------------------------------------------------------
def ensure_vat(ctx: Ctx, pct: Decimal) -> str:
    """3.4-3.6: reuse an exact VAT row or create it."""
    ui = ctx.ui
    name = vat_name(pct)
    rows = db.vats_named(name)
    if len(rows) > 1:
        raise ManualReview(f"{len(rows)} VAT rows named {name!r}")
    if rows:
        r = rows[0]
        if abs(r["value"] * 100 - pct) > Decimal("0.0001") or (r["code"] or "") != "S":
            raise ManualReview(f"VAT {name!r} exists with value {r['value']} / code {r['code']!r}")
        ctx.record(f"3.5 VAT {name}", "exists", id=r["id"])
        return name
    ui.nav("VATs")
    ui.press("Create a new tax rate")
    wait_for(lambda: ui.active_editor().lstrip("*") == "New TAX Rate", 10, what="VAT editor")
    ed = ui.editor_pane()
    ui.set_text(ui.labelled("Name", "Edit", root=ed), name)
    ui.set_text(ui.labelled("Description", "Edit", root=ed), name)
    code = ui.labelled("VAT code (E-Invoice)", "ComboBox", root=ed)
    if ui.combo_value(code).strip() != "S (Standard rate)":
        ui.combo_select(code, "S (Standard rate)")
    ui.set_amount(ui.labelled("Value", "Edit", root=ed), pct)
    shot = ctx.shot(f"vat_new_{name}", [(code, "S (Standard rate)")])
    ui.save()
    wait_for(lambda: len(db.vats_named(name)) == 1, 10, what="VAT saved")
    r = db.vats_named(name)[0]
    if abs(r["value"] * 100 - pct) > Decimal("0.0001") or r["code"] != "S":
        raise AutomationError(f"saved VAT differs: {r}")
    ctx.created.append(f"VAT {name}")
    ctx.record(f"3.6 VAT {name}", "created", id=r["id"], value=r["value"], code=r["code"], shot=shot)
    return name


def create(ctx: Ctx, item: Item) -> None:
    """3.7-3.11."""
    ui = ctx.ui
    vname = ensure_vat(ctx, item.vat_pct)
    # 3.7, only after the VAT exists. Toolbar, not the nav link: in 2.2.0 the link (CallEditor) throws a
    # ClassCastException when the last selection was a debtor address from the selector.
    ui.press("Create a new product")
    wait_for(lambda: ui.active_editor().lstrip("*") == "New product", 10, what="product editor")
    ed = ui.editor_pane()
    ui.set_text(ui.labelled("Item Number", "Edit", root=ed), item.sku)  # 3.8
    ui.set_text(ui.labelled("Name", "Edit", root=ed), item.description)
    ui.set_text(ui.labelled("Description", "Edit", root=ed), item.description, leave="none")
    gross = gross_price(item.unit_net, item.vat_pct)  # 3.9, no line discount
    vat = ui.labelled("VAT", "ComboBox", root=ed)
    ui.combo_select(vat, vname)  # select VAT before the price so gross->net uses it
    ui.set_amount(ui.labelled("Price (gross)", "Edit", root=ed), gross)
    cost = ui.labelled("cost price (net)", "Edit", root=ed)  # 3.10
    if parse_amount(read(cost)) != 0:
        ui.set_amount(cost, 0)
    stock = ui.labelled("Stock", "Edit", root=ed)
    if parse_amount(read(stock)) != 0:
        ui.set_amount(stock, 0)
    shot = ctx.shot(f"product_new_{item.sku}", [(vat, vname)])
    ui.save()  # 3.11
    wait_for(lambda: len(db.products_with_sku(item.sku)) == 1, 10, what="product saved")
    row = db.products_with_sku(item.sku)[0]
    net_back = Decimal(str(row["price"]))
    checks = {
        "name": row["name"] == item.description,
        "description": (row["description"] or "").strip() == item.description,
        "vat": row["vat_name"] == vname,
        # PRICE1 holds net; it must round back to the source unit net price
        "net price": money(net_back) == item.unit_net,
    }
    if not all(checks.values()):
        raise AutomationError(f"saved product {item.sku} differs: {checks} {row}")
    ctx.created.append(f"Product {item.sku}")
    ctx.record(f"3.11 product {item.sku}", "created", id=row["id"], gross=gross, net_stored=row["price"], shot=shot)


# ---------------------------------------------------------------------------
def complete_line(ctx: Ctx, item: Item, found: tuple[G.Grid, G.Row] | None = None) -> None:
    """3.13-3.16: Qty and Discount on the item's row; U.Price is the catalogue's (confirmed
    in _check_master, never typed). Verified by the running Total Net.

    Speed: a cell that already shows the wanted value is not edited (new lines start at
    Qty 1 and Discount 0), the first edit reuses the read that proved the line, and the
    row is re-read (with its SKU) only before a second edit.
    """
    from pywinauto import mouse
    from pywinauto.keyboard import send_keys

    ui = ctx.ui
    done = ctx.order.items[: ctx.order.items.index(item) + 1]
    want_net = money(sum((line_net(i.qty, i.unit_net, i.discount_pct) for i in done), Decimal(0)))
    edits = [("Qty.", item.qty, Decimal(1)), ("Discount", item.discount_pct, Decimal(0))]
    edits = [(col, value) for col, value, default in edits if value != default]

    ctx.back_to_order()
    for n, (col, value) in enumerate(edits):
        g, row = found if (n == 0 and found) else last_line(ctx)
        if not sku_eq(row.get("Item No."), item.sku):
            raise ManualReview(f"bottom Order line shows {row.get('Item No.')!r}, expected {item.sku}")
        x = g.column_x(col)
        if x is None:
            raise AutomationError(f"item column {col!r} not located (have {g.headers})")
        ui.focus()
        mouse.double_click(coords=(int(x), int(row.y)))
        time.sleep(0.3)
        send_keys("^a")
        send_keys(ui.amount_text(value, sep="."), pause=0.02)  # grid cells take '.' whatever the locale
        send_keys("{ENTER}")
        time.sleep(0.5)

    def net_ok():
        t = read_totals(ctx)
        return t if close(t.get("net"), want_net, len(done)) else None

    try:
        got = wait_for(net_ok, 5, what=f"Total Net {want_net} after line {item.sku}")
    except AutomationError as exc:
        raise ManualReview(
            f"line {item.sku}: Order Total Net is {read_totals(ctx).get('net')}, expected {want_net}"
        ) from exc
    if money(got["net"]) != want_net:
        ctx.warnings.append(f"after line {item.sku}: Total Net {got['net']} vs image {want_net} (catalogue rounding)")
    ctx.record(
        f"3.16 line {item.sku}",
        "ok",
        qty=item.qty,
        unit_net=item.unit_net,
        discount=item.discount_pct,
        line_net=line_net(item.qty, item.unit_net, item.discount_pct),
        running_net=got["net"],
        shot=ctx.shot(f"line_{item.sku}"),
    )
