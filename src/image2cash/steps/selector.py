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

import logging
import re
import time
from collections.abc import Callable

from image2cash.steps.context import Ctx
from image2cash.ui import grid as G
from image2cash.ui.core import AutomationError, ManualReview, wait_for
from image2cash.ui.core import paste as _paste

log = logging.getLogger("image2cash.selector")


def open_selector(ctx: Ctx, icon_label: str, title: str):
    ui = ctx.ui
    ctx.back_to_order()
    key = ("icon", icon_label)
    try:  # the icon does not move while the Order is open; finding it costs ~0.5 s
        icon = ctx.cache[key]
        ui.click(icon)
    except Exception:  # noqa: BLE001 - not cached yet, or the cached element went stale
        icon = ctx.cache[key] = ui.icon_beside(icon_label, 0, root=ui.editor_pane())  # upper icon, never the +
        ui.click(icon)
    return ui.dialog(title), icon


def search(ctx: Ctx, dialog, term: str, headers: list[str], auto_accepts: bool = True) -> G.Grid | None:
    """Paste ``term``; return the stabilized grid, or None if the dialog auto-accepted.

    ``auto_accepts=False`` skips the 1 s auto-accept wait: 'Select the address' never
    auto-accepted in 33 single-row runs (it needs a click + OK). Were it to, reading the
    closed dialog's grid fails and the run stops; it never picks wrongly."""
    edit = G.search_edit(dialog)
    dialog.set_focus()
    edit.click_input()
    _paste(term)
    title = dialog.window_text()
    log.debug("search %r in %r", term, title)
    if auto_accepts:
        try:  # a unique match auto-accepts and closes the dialog; poll instead of a fixed 1 s wait
            # by handle: a dialog found on the Desktop (title '') is not one of Fakturama's child
            # windows, so a by-title check saw it as closed and an unpicked search passed as accepted
            wait_for(lambda: _gone(dialog), 1.0, interval=0.1, what="selector auto-accept")
            return None
        except AutomationError:
            pass
    pane = G.grid_below_search(dialog)
    grid = G.read_stable(pane.rectangle(), headers, owner=dialog)
    # shortcut: no paging in selectors; an exact SKU/company search rarely fills the list. Page if it ever does.
    if grid.rows and grid.rows[-1].y > pane.rectangle().bottom - 30:
        raise ManualReview(f"{term!r} matches more rows than {dialog.window_text()!r} shows; narrow it by hand")
    return grid


def pick(ctx: Ctx, dialog, grid: G.Grid, is_target: Callable[[G.Row], bool], column: str) -> None:
    rows = [r for r in grid.rows if is_target(r)]
    if len(rows) != 1:
        raise ManualReview(
            f"expected one matching row in {dialog.window_text()!r},"
            f" OCR found {len(rows)}: {[r.cells for r in grid.rows]}"
        )
    x, y = rows[0].boxes.get(column) or next(iter(rows[0].boxes.values()))
    from pywinauto import mouse

    dialog.set_focus()
    mouse.click(coords=(int(x), int(y)))
    time.sleep(0.15)
    press(dialog, "OK")


def press(dialog, name: str) -> None:
    for b in dialog.descendants(control_type="Button"):
        if b.element_info.name == name:
            b.invoke()
            try:  # most buttons close the dialog: return as soon as it is gone (was a fixed 0.8 s)
                wait_for(lambda: _gone(dialog), 0.8, interval=0.1, what=f"{name!r} to close")
            except AutomationError:
                pass
            return
    raise AutomationError(f"{name!r} not in {dialog.window_text()!r}")


def _gone(dialog) -> bool:
    """The dialog's window is closed or hidden. Asked of Win32 by handle: the UIA wrapper of a
    closed SWT shell keeps answering 'visible', which made every press wait its full timeout."""
    import win32gui

    try:
        h = dialog.handle
    except Exception:  # noqa: BLE001
        return True
    return not (h and win32gui.IsWindow(h) and win32gui.IsWindowVisible(h))


def ocr_eq(seen: str | None, want: str) -> bool:
    """Equal up to OCR confusions (O/0, I/L/1, S/5) and stray punctuation such as a
    cell border read as '|'. Only used to find the row to click: identity is decided by the DB."""
    norm = lambda t: re.sub(r"[^A-Z0-9./-]", "", (t or "").upper()).translate(str.maketrans("OILS", "0115"))  # noqa: E731
    return bool(seen) and norm(seen) == norm(want)


def prefix_match(cell: str, full: str) -> bool:
    """A truncated grid cell ('Northstar Offic...') is consistent with ``full``."""
    c = cell.strip().rstrip(".…").strip()
    return bool(c) and full.replace(" ", "").startswith(c.replace(" ", ""))
