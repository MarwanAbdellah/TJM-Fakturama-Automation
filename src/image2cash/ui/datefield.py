"""Set Fakturama's segmented date fields (Nebula CDateTime) through the keyboard.

Typing a whole date does not work: each segment (day, month, year) is edited
on its own, and where the caret lands depends on the click. Plain arrow-key
navigation was not deterministic in calibration either.

So the setter observes instead of assuming. It presses Up, sees which segment
changed, presses Down to undo, and then steps that segment to its target with
Up/Down. It moves to the next segment with Left/Right and checks again. Every
keystroke is followed by a read-back, so the result is verified, not hoped for.
"""

from __future__ import annotations

import re
import time
from datetime import date

from image2cash.ui.core import AutomationError, read

MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan",), ("feb",), ("mar",), ("apr",), ("may",), ("jun",),
            ("jul",), ("aug",), ("sep", "sept"), ("oct",), ("nov",), ("dec",),
        ]
    )
    for m in names
}


def parse_display(text: str) -> date | None:
    """Parse what the field shows, e.g. '14 Jul 2026', 'Jul 14, 2026', '14.07.2026'."""
    t = " ".join(text.strip().replace(",", " ").split())
    m = re.match(r"^(\d{1,2}) ([A-Za-z]+)\.? (\d{1,4})$", t)
    if m and m[2].lower()[:3] in MONTHS:
        return date(int(m[3]), MONTHS[m[2].lower()[:3]], int(m[1]))
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{1,2})\s+(\d{1,4})$", t)
    if m and m[1].lower()[:3] in MONTHS:
        return date(int(m[3]), MONTHS[m[1].lower()[:3]], int(m[2]))
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", t)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    return None


def _which_changed(a: date, b: date) -> str | None:
    if a.year != b.year:
        return "year"
    if a.month != b.month:
        return "month"
    if a.day != b.day:
        return "day"
    return None


def set_date(ui, ctrl, target: date, max_keys: int = 400) -> date:
    from pywinauto.keyboard import send_keys

    def now() -> date:
        value = parse_display(read(ctrl))
        if value is None:
            raise AutomationError(f"date field shows unparseable {read(ctrl)!r}")
        return value

    def key(k: str) -> date:
        send_keys(k, pause=0.02)
        time.sleep(0.12)
        return now()

    ui.focus()
    ctrl.click_input()
    time.sleep(0.2)
    budget = max_keys
    # Year first, then month, then day, so an intermediate value is never an
    # invalid date such as 31 Feb.
    for segment in ("year", "month", "day"):
        for _ in range(4):  # find the segment: probe, move, probe again
            before = now()
            after = key("{UP}")
            active = _which_changed(before, after)
            key("{DOWN}")
            if active == segment:
                break
            order = ["day", "month", "year"]
            # Layout order is unknown (d M y or M d y), so move toward it and re-probe.
            if active is None:
                key("{RIGHT}")
            elif order.index(segment) < order.index(active):
                key("{LEFT}")
            else:
                key("{RIGHT}")
        else:
            raise AutomationError(f"could not focus the {segment} segment")
        # Typing digits into the focused segment is fast; verify, then fall
        # back to Up/Down stepping for whatever is still off.
        want_text = {"year": f"{target.year:04d}", "month": str(target.month), "day": f"{target.day:02d}"}[segment]
        send_keys(want_text, pause=0.03)
        time.sleep(0.2)
        if segment == "month":
            # The month segment shows digits until the caret leaves it.
            key("{LEFT}")
            key("{RIGHT}")
        while True:
            current = now()
            have = getattr(current, segment)
            want = getattr(target, segment)
            if have == want:
                break
            budget -= 1
            if budget <= 0:
                raise AutomationError(f"date stepping exceeded budget at {current}")
            key("{UP}" if have < want else "{DOWN}")
    send_keys("{TAB}")
    time.sleep(0.3)
    result = now()
    if result != target:
        raise AutomationError(f"date field reads {result}, expected {target}")
    return result
