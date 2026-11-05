"""Brief §3: select or create each Product (and its VAT), then complete the line."""

from __future__ import annotations

import time
from decimal import Decimal

from image2cash.models import Item
from image2cash.steps import selector as S
from image2cash.steps.context import Ctx
from image2cash.steps.order import read_totals
from image2cash.ui import grid as G
from image2cash.ui.core import AutomationError, ManualReview, read, wait_for
from image2cash.validate import gross_price, line_net, money, parse_amount, vat_name
from image2cash.verify import db

SELECT_HEADERS = ["Item No.", "Name", "Description", "Stock", "Price", "VAT"]
ITEM_HEADERS = ["Pos.", "Qty.", "Item No.", "Picture", "Name", "Description", "VAT", "U.Price", "Discount", "Price"]
TITLE = "Select a product"


def all_items(ctx: Ctx) -> None:
    for item in ctx.order.items:  # 3.1 / 3.17, in source order
        select_or_create(ctx, item)
        complete_line(ctx, item)


def select_or_create(ctx: Ctx, item: Item) -> None:
    rows = db.products_with_sku(item.sku)
    if len(rows) > 1:
        raise ManualReview(f"{len(rows)} products with SKU {item.sku}")
    before = _item_rows(ctx)
    dialog, icon = S.open_selector(ctx, "Items", TITLE)  # 3.2
    grid = S.search(ctx, dialog, item.sku, SELECT_HEADERS)  # 3.3
    shot = ctx.shot(f"product_selector_{item.sku}", [(icon, "select product")])
    if grid is None:
        if len(rows) != 1:
            raise ManualReview(f"selector auto-accepted a product for {item.sku} that is not an exact SKU match")
        how = "auto-accepted unique row"
    elif len(rows) == 1:
        S.pick(ctx, dialog, grid, lambda r: r.get("Item No.") == item.sku, "Item No.")
        how = "picked exact row"
    else:
        if any(r.get("Item No.") == item.sku for r in grid.rows):
            raise ManualReview(f"grid shows SKU {item.sku} but the DB has no live product with it")
        S.press(dialog, "Cancel")
        ctx.record(f"3.3 product {item.sku}", "missing", rows_seen=len(grid.rows), shot=shot)
        create(ctx, item)
        return reselect(ctx, item, before)
    _check_new_row(ctx, item, before)
    _check_master(ctx, item, rows[0])
    ctx.record(f"3.3 product {item.sku}", "ok", how=how, product=rows[0]["id"], shot=shot)


def reselect(ctx: Ctx, item: Item, before: int) -> None:
    """3.12: pick the newly saved product from the still-open Order."""
    rows = db.products_with_sku(item.sku)
    if len(rows) != 1:
        raise ManualReview(f"after saving, {len(rows)} products with SKU {item.sku}")
    dialog, _ = S.open_selector(ctx, "Items", TITLE)
    grid = S.search(ctx, dialog, item.sku, SELECT_HEADERS)
    if grid is not None:
        S.pick(ctx, dialog, grid, lambda r: r.get("Item No.") == item.sku, "Item No.")
    _check_new_row(ctx, item, before)
    ctx.record(f"3.12 product {item.sku} reselected", "ok", product=rows[0]["id"])


def _items_grid(ctx: Ctx):
    """The Order's item table: the pane to the right of the 'Items' label column."""
    ui = ctx.ui
    lab = ui.find("Items", "Text", root=ui.editor_pane())
    holder = lab.parent()
    hr = holder.rectangle()
    panes = [
        c
        for c in holder.parent().children()
        if c.element_info.control_type == "Pane" and c.rectangle().left >= hr.right - 2 and abs(c.rectangle().top - hr.top) < 6
    ]
    if not panes:
        raise AutomationError("Order item table not found")
    return panes[0]


def read_items(ctx: Ctx) -> G.Grid:
    ctx.back_to_order()
    pane = _items_grid(ctx)
    return G.read_grid(pane.rectangle(), ITEM_HEADERS, owner=ctx.ui.main)


def _item_rows(ctx: Ctx) -> int:
    return len([r for r in read_items(ctx).rows if r.get("Item No.")])


def _check_new_row(ctx: Ctx, item: Item, before: int) -> None:
    g = wait_for(
        lambda: (lambda g: g if len([r for r in g.rows if r.get("Item No.")]) == before + 1 else None)(read_items(ctx)),
        8,
        what=f"new item row for {item.sku}",
    )
    last = [r for r in g.rows if r.get("Item No.")][-1]
    if last.get("Item No.") != item.sku:
        raise ManualReview(f"the new Order line shows {last.get('Item No.')!r}, expected {item.sku}")


def _check_master(ctx: Ctx, item: Item, row: dict) -> None:
    """An existing product's VAT must equal the source VAT (lines inherit it)."""
    if row["vat_value"] is None or abs(Decimal(str(row["vat_value"])) * 100 - item.vat_pct) > Decimal("0.001"):
        raise ManualReview(f"product {item.sku} has VAT {row['vat_name']}, source says {item.vat_pct}% (line VAT override not automated)")


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
    ui.nav("New product")  # 3.7: only after the VAT exists
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
def complete_line(ctx: Ctx, item: Item) -> None:
    """3.13-3.16: Qty, U.Price, Discount on the item's row; verify by totals."""
    from pywinauto import mouse
    from pywinauto.keyboard import send_keys

    ui = ctx.ui
    g = read_items(ctx)
    rows = [r for r in g.rows if r.get("Item No.") == item.sku]
    if len(rows) != 1:
        raise ManualReview(f"expected one Order line for {item.sku}, found {len(rows)}")
    row = rows[0]

    def edit_cell(col: str, value: str) -> None:
        x = g.column_x(col)
        if x is None:
            raise AutomationError(f"item column {col!r} not located (have {g.headers})")
        ui.focus()
        mouse.double_click(coords=(int(x), int(row.y)))
        time.sleep(0.4)
        send_keys("^a")
        send_keys(value, pause=0.02)
        send_keys("{ENTER}")
        time.sleep(0.6)

    # Grid cells parse '.' whatever the UI locale (calibrated); form fields do not.
    edit_cell("Qty.", ui.amount_text(item.qty, sep="."))
    edit_cell("U.Price", ui.amount_text(item.unit_net, sep="."))  # 3.14 overwrite: never trust gross->net rounding
    edit_cell("Discount", ui.amount_text(item.discount_pct, sep="."))

    # 3.16 verified through the UIA totals: running net == sum of lines so far
    done = ctx.order.items[: ctx.order.items.index(item) + 1]
    want_net = money(sum((line_net(i.qty, i.unit_net, i.discount_pct) for i in done), Decimal(0)))
    got = wait_for(
        lambda: (lambda t: t if t.get("net") is not None and money(t["net"]) == want_net else None)(read_totals(ctx)),
        5,
        what=f"Total Net {want_net} after line {item.sku}",
    )
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
