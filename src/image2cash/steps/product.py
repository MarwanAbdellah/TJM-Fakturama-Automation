"""Brief §3: select or create each Product (and its VAT), then complete the lines.

All products are added first; then the item table is read once and the Qty column
and the Discount column are filled. Fakturama rebuilds the table after every cell
edit (selection back to line 1, scroll back to the top), so nothing here relies on
the highlight or the scroll position surviving an edit.
"""

from __future__ import annotations

import logging
import re
import time
from decimal import Decimal

from image2cash.models import Item
from image2cash.steps import selector as S
from image2cash.steps.context import Ctx
from image2cash.steps.order import read_totals
from image2cash.ui import grid as G
from image2cash.ui.core import AutomationError, ManualReview, read, wait_for
from image2cash.validate import CENT, close, gross_price, line_net, money, parse_amount, vat_name
from image2cash.verify import db

SELECT_HEADERS = ["Item No.", "Name", "Description", "Stock", "Price", "VAT"]
ITEM_HEADERS = ["Pos.", "Qty.", "Item No.", "Picture", "Name", "Description", "VAT", "U.Price", "Discount", "Price"]
TITLE = "Select a product"
log = logging.getLogger("image2cash.product")


def all_items(ctx: Ctx) -> None:
    for item in ctx.order.items:  # 3.1 / 3.17, in source order
        select_or_create(ctx, item)
    fill_lines(ctx)  # 3.13-3.16
    verify_lines(ctx)


def select_or_create(ctx: Ctx, item: Item) -> None:
    """Put the item on a new Order line (Qty 1, no discount)."""
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
    _added(ctx, item, pos, rows[0]["price"])
    ctx.record(f"3.3 product {item.sku}", "ok", how=how, product=rows[0]["id"], shot=shot)


def reselect(ctx: Ctx, item: Item, pos: int) -> None:
    """3.12: pick the newly saved product from the still-open Order."""
    rows = db.products_with_sku(item.sku)
    if len(rows) != 1:
        raise ManualReview(f"after saving, {len(rows)} products with SKU {item.sku}")
    dialog, _ = S.open_selector(ctx, "Items", TITLE)
    grid = S.search(ctx, dialog, item.sku, SELECT_HEADERS)
    if grid is not None:
        S.pick(ctx, dialog, grid, lambda r: sku_eq(r.get("Item No."), item.sku), "Item No.")
    _added(ctx, item, pos, rows[0]["price"])
    ctx.record(f"3.12 product {item.sku} reselected", "ok", product=rows[0]["id"])


def _added(ctx: Ctx, item: Item, pos: int, price) -> None:
    """Prove the add without OCR: the Order's Total Net rises by exactly the catalogue
    price (a new line has Qty 1 and no discount). Which row it landed on is checked
    once for all lines in `fill_lines`."""
    want = ctx.running_net + money(Decimal(str(price)))

    def added():
        net = read_totals(ctx).get("net")
        return net if net is not None and abs(money(net) - want) <= CENT else None

    try:
        ctx.running_net = money(wait_for(added, 4, what=f"Total Net {want} after adding {item.sku}"))
    except AutomationError as exc:
        raise ManualReview(
            f"adding {item.sku} as line {pos}: Order Total Net is {read_totals(ctx).get('net')}, expected {want}"
        ) from exc


def _items_label(ctx: Ctx):
    lab = ctx.cache.get("items_label")
    if lab is None or not lab.is_visible():
        lab = ctx.cache["items_label"] = ctx.ui.find("Items", "Text", root=ctx.ui.editor_pane())
    return lab


def _items_grid(ctx: Ctx):
    """The Order's item table: the pane to the right of the 'Items' label column."""
    lab = _items_label(ctx)
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


def _scroll(ctx: Ctx, pane, dist: int) -> None:
    """The item table gets its own scrollbar after ~7 rows (calibrated). Its scrollbar
    buttons ignore UIA Invoke; the mouse wheel over the grid scrolls the table itself.
    Guard: if the 'Items' label moved, the outer editor scrolled instead."""
    from pywinauto import mouse

    r = pane.rectangle()
    label = lambda: _items_label(ctx).rectangle().top  # noqa: E731
    before = label()
    ctx.ui.focus()
    mouse.scroll(coords=((r.left + r.right) // 2, (r.top + r.bottom) // 2), wheel_dist=dist)
    time.sleep(0.2)
    if label() != before:
        raise AutomationError("mouse wheel scrolled the Order editor, not the item table")


def _table_rect(ctx: Ctx, pane):
    r = pane.rectangle()
    r.bottom = min(r.bottom, ctx.ui._editor_folder().rectangle().bottom)  # the table may extend below the editor
    return r


def _row_pos(row: G.Row) -> int | None:
    return int(row.get("Pos.")) if row.get("Pos.").isdigit() else None


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
    value = ui.labelled("Value", "Edit", root=ed)
    # The percent field shows '0%' (no separator to learn) and need not share the price fields'
    # separator: '6,25' read back as 625%. Try '.', then ','; the read-back decides.
    try:
        ui.set_amount(value, pct, sep=".")
    except AutomationError:
        ui.set_amount(value, pct, sep=",")
    shot = ctx.shot(f"vat_new_{name}", [(code, "S (Standard rate)")])
    ui.save()
    wait_for(lambda: len(db.vats_named(name)) == 1, 10, what="VAT saved")
    r = db.vats_named(name)[0]
    if abs(r["value"] * 100 - pct) > Decimal("0.0001") or r["code"] != "S":
        raise AutomationError(f"saved VAT differs: {r}")
    ctx.created.append(f"VAT {name}")
    ctx.record(f"3.6 VAT {name}", "created", id=r["id"], value=r["value"], code=r["code"], shot=shot)
    ctx.close_saved_tab()
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
    ctx.close_saved_tab()


# ---------------------------------------------------------------------------
TOP = 60  # wheel notches up: more than any order needs to get back to line 1
PAGE = 2  # wheel notches per page while walking the table; 1 if 2 ever skips lines
Rows = dict[int, G.Row]


def walk(read_page, scroll_down, n_lines: int, max_pages: int = 200) -> list[tuple[int, Rows]]:
    """Read the item table page by page from the top: [(notches below the top, rows by
    line number)]. ``read_page()`` OCRs the current view; ``scroll_down(k)`` turns the
    wheel k notches down. Pages overlap; if a page skips lines the walk backs up and
    uses 1 notch from then on. Stops when every line is seen or the table stops moving."""
    views = [(0, read_page())]
    seen = set(views[0][1])
    clicks, step = 0, PAGE
    for _ in range(max_pages):
        if all(n in seen for n in range(1, n_lines + 1)):
            break
        scroll_down(step)
        rows = read_page()
        if step > 1 and rows and seen and min(rows) > max(seen) + 1:  # lines skipped
            scroll_down(1 - step)  # back up to one notch past the last page
            step = 1
            rows = read_page()
        if not rows or set(rows) <= seen:
            break  # end of the table: the wheel no longer moves it
        clicks += step
        views.append((clicks, rows))
        seen |= set(rows)
    return views


def _walk(ctx: Ctx, pane) -> tuple[list[tuple[int, Rows]], G.Grid]:
    """`walk` on the live table; also returns the first page's grid (for column positions)."""
    first = []

    def read_page() -> Rows:
        r = _table_rect(ctx, pane)
        g = G.read_grid(r, ITEM_HEADERS, owner=ctx.ui.main)
        first.append(g)
        return _numbered(g, r.bottom, top=r.top + 28, total=len(ctx.order.items))  # no half-clipped rows

    _scroll(ctx, pane, TOP)
    return walk(read_page, lambda k: _scroll(ctx, pane, -k), len(ctx.order.items)), first[0]


def _view(ctx: Ctx, pane, clicks: int) -> None:
    """Show the page `clicks` notches below the top (every edit throws the view to the top)."""
    _scroll(ctx, pane, TOP)
    if clicks:
        _scroll(ctx, pane, -clicks)


def locate(views: list[tuple[int, Rows]], items: list[Item]) -> dict[int, tuple[int, float]]:
    """Line number -> (page, screen y), from the numbered pages of `walk`; a line seen on
    two pages keeps the first. Every line must be found and show its own SKU, or the run stops."""
    at: dict[int, tuple[int, float]] = {}
    for view, rows in views:
        for n, row in rows.items():
            if n in at or not 1 <= n <= len(items):
                continue
            if not sku_eq(row.get("Item No."), items[n - 1].sku):
                raise ManualReview(f"Order line {n} shows {row.get('Item No.')!r}, expected {items[n - 1].sku}")
            at[n] = (view, row.y)
    missing = [n for n in range(1, len(items) + 1) if n not in at]
    if missing:
        raise ManualReview(f"Order lines {missing} not found in the item table")
    return at


def column_net(items: list[Item], with_discount: bool) -> Decimal:
    """The Order's Total Net once the Qty column (and, if asked, the Discount column) is filled."""
    return money(
        sum((line_net(i.qty, i.unit_net, i.discount_pct if with_discount else Decimal(0)) for i in items), Decimal(0))
    )


def fill_lines(ctx: Ctx) -> None:
    """3.13-3.16: read the finished table once, page by page (`walk`), then type every
    Qty, then every Discount, straight into the known cells. U.Price is the catalogue's
    (confirmed in _check_master, never typed).

    Rows keep their y when values change (their height follows the product name), but
    every edit throws the view back to the top, so each edit first replays its page's
    scroll from the top. Total Net is checked after each column; `verify_lines`
    re-reads every line before the Order is saved."""
    from pywinauto import mouse
    from pywinauto.keyboard import send_keys

    ui, items = ctx.ui, ctx.order.items
    ctx.back_to_order()
    pane = _items_grid(ctx)
    views, g = _walk(ctx, pane)
    cols = {c: (g.columns or {}).get(c) for c in ("Qty.", "Discount")}
    if None in cols.values():
        raise AutomationError(f"item columns not located (have {g.headers})")
    at = locate(views, items)

    scrolled = True  # `walk` left the view on its last page
    for col, field, default in (("Qty.", "qty", Decimal(1)), ("Discount", "discount_pct", Decimal(0))):
        for n, it in enumerate(items, 1):
            value = getattr(it, field)
            if value == default:  # new lines already show Qty 1 and Discount 0
                continue
            clicks, y = at[n]
            if clicks or scrolled:  # else page 1 is still showing: skip the wheel (it parks the mouse mid-table)
                _view(ctx, pane, clicks)
            scrolled = bool(clicks)
            left, right = cols[col]
            ui.focus()
            mouse.double_click(coords=(int((left + right) / 2), int(y)))
            time.sleep(0.15)
            send_keys("^a")
            send_keys(ui.amount_text(value, sep="."), pause=0.02)  # grid cells take '.' whatever the locale
            send_keys("{ENTER}")
            time.sleep(0.2)
        with_disc = col == "Discount"
        want = column_net(items, with_disc)

        def net_ok(want=want):
            t = read_totals(ctx)
            return t if close(t.get("net"), want, items) else None

        try:
            got = wait_for(net_ok, 5, what=f"Total Net {want} after the {col} column")
        except AutomationError as exc:
            raise ManualReview(
                f"after filling {col}: Order Total Net is {read_totals(ctx).get('net')}, expected {want}"
            ) from exc
        if with_disc and money(got["net"]) != want:
            ctx.warnings.append(f"Total Net {got['net']} vs image {want} (catalogue rounding)")
        ctx.record(f"3.16 {col} column", "ok", lines=len(items), net=got["net"], shot=ctx.shot(f"column_{col}"))


# --- one full read before saving -----------------------------------------------
def _num(text: str) -> Decimal | None:
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return None if m is None else money(Decimal(m.group()))


def _unmerge(row: G.Row) -> G.Row:
    """OCR can join the right-aligned Qty. with the Item No. beside it
    ('120.00 NDC-0409-4888' and no Qty.): give the number back to Qty."""
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s+(\S+)", row.get("Item No.") or "")
    if m and not row.get("Qty."):
        row.cells["Qty."], row.cells["Item No."] = m.groups()
    return row


def _numbered(g: G.Grid, bottom: float, top: float = float("-inf"), total: int | None = None) -> Rows:
    """Rows of one read keyed by line number, without rows cut off at either edge.
    Pos. is unreadable on the blue selected row, so rows are numbered from the most
    common (Pos. - index) offset."""
    rows = [_unmerge(r) for r in g.rows if r.get("Item No.") and top < r.y < bottom - 8]
    offsets = [_row_pos(r) - i for i, r in enumerate(rows) if _row_pos(r) is not None]
    # A Pos. read that puts a row outside 1..total is a misread ('3.00' bleeding into '1').
    offsets = [o for o in offsets if o >= 1 and (total is None or o + len(rows) - 1 <= total)]
    if not offsets:
        # Pos. unreadable or misread: a page with as many rows as the order has lines
        # shows the whole table, line 1 first.
        return {i + 1: r for i, r in enumerate(rows)} if total and len(rows) == total else {}
    off = max(set(offsets), key=offsets.count)
    return {i + off: r for i, r in enumerate(rows)}


def verify_lines(ctx: Ctx) -> None:
    """Read the whole item table page by page and compare every line's SKU, Qty,
    Discount and Price with the order. Runs once, before the Order is saved."""
    items = ctx.order.items
    ctx.back_to_order()
    seen: Rows = {}
    for _, rows in _walk(ctx, _items_grid(ctx))[0]:
        seen = {**rows, **seen}  # first page a line appears on wins
    problems, unread = [], []
    for n, it in enumerate(items, 1):
        row = seen.get(n)
        if row is None:
            problems.append(f"line {n} not found in the item table")
            continue
        if not sku_eq(row.get("Item No."), it.sku):
            problems.append(f"line {n} shows {row.get('Item No.')!r}, expected {it.sku}")
        for col, want in (("Qty.", money(it.qty)), ("Discount", money(it.discount_pct))):
            got = _num(row.get(col))
            if got is None:
                unread.append(f"line {n} {col}")
            elif got != want:
                problems.append(f"line {n} {col} shows {row.get(col)!r}, expected {want}")
        got = parse_amount(row.get("Price"))
        want = line_net(it.qty, it.unit_net, it.discount_pct)
        if got is None:
            unread.append(f"line {n} Price")
        elif not close(got, want, [it]):
            problems.append(f"line {n} Price shows {row.get('Price')!r}, expected {want}")
    if unread:  # OCR could not read the cell; the Total Net check and the DB check after save still cover it
        ctx.warnings.append("final table read could not read: " + ", ".join(unread))
    shot = ctx.shot("item_table")
    if problems:
        ctx.record("3.17 all lines", "REVIEW", problems=problems, shot=shot)
        raise ManualReview("item table differs from the order: " + "; ".join(problems))
    ctx.record("3.17 all lines", "ok", lines=len(items), shot=shot)
