"""The Order's two selector dialogs ('Select the address', 'Select a product').

Calibrated behaviour this module is built around:
  * the result grid is custom-drawn (no UIA rows) -> read by OCR;
  * typing into Search filters on every keystroke, and the dialog
    *auto-accepts* as soon as exactly one row is left. Typing an SKU key by key
    could therefore auto-pick a different product on an intermediate prefix.
    We paste the whole term at once instead.
  * whether a match is *exact* is decided from the database (grid cells are
    truncated, OCR cannot prove absence). OCR only finds the row to click.
"""

from __future__ import annotations

import time
from typing import Callable

from image2cash.steps.context import Ctx
from image2cash.ui import grid as G
from image2cash.ui.core import AutomationError, ManualReview


def _paste(text: str) -> None:
    import win32clipboard as cb
    from pywinauto.keyboard import send_keys

    cb.OpenClipboard()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardText(text, cb.CF_UNICODETEXT)
    finally:
        cb.CloseClipboard()
    send_keys("^a^v")


def open_selector(ctx: Ctx, icon_label: str, title: str):
    ui = ctx.ui
    ctx.back_to_order()
    icon = ui.icon_beside(icon_label, 0, root=ui.editor_pane())  # the upper icon, never the green +
    ui.click(icon)
    return ui.dialog(title), icon


def search(ctx: Ctx, dialog, term: str, headers: list[str]) -> G.Grid | None:
    """Paste ``term``; return the stabilized grid, or None if the dialog auto-accepted."""
    ui = ctx.ui
    edit = G.search_edit(dialog)
    dialog.set_focus()
    edit.click_input()
    _paste(term)
    time.sleep(1.0)
    if dialog.window_text() not in ui.dialogs():
        return None
    grid = G.grid_below_search(dialog)
    return G.read_stable(grid.rectangle(), headers, owner=dialog)


def pick(ctx: Ctx, dialog, grid: G.Grid, is_target: Callable[[G.Row], bool], column: str) -> None:
    rows = [r for r in grid.rows if is_target(r)]
    if len(rows) != 1:
        raise ManualReview(f"expected one matching row in {dialog.window_text()!r}, OCR found {len(rows)}: {[r.cells for r in grid.rows]}")
    x, y = rows[0].boxes.get(column) or next(iter(rows[0].boxes.values()))
    from pywinauto import mouse

    dialog.set_focus()
    mouse.click(coords=(int(x), int(y)))
    time.sleep(0.4)
    press(dialog, "OK")


def press(dialog, name: str) -> None:
    for b in dialog.descendants(control_type="Button"):
        if b.element_info.name == name:
            b.invoke()
            time.sleep(0.8)
            return
    raise AutomationError(f"{name!r} not in {dialog.window_text()!r}")


def prefix_match(cell: str, full: str) -> bool:
    """A truncated grid cell ('Northstar Offic...') is consistent with ``full``."""
    c = cell.strip().rstrip(".…").strip()
    return bool(c) and full.replace(" ", "").startswith(c.replace(" ", ""))
