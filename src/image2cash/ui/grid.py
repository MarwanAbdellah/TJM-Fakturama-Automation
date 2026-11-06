"""Read and click custom-drawn NatTable grids through OCR.

UIA sees these grids as empty panes. Their rectangle still comes from UIA,
so the capture area is never hardcoded. Inside the capture, rows are rebuilt by
y-clustering OCR lines and columns are assigned by header x-spans.
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from image2cash.extract.ocr import Line, ocr_pil
from image2cash.ui.core import AutomationError, ManualReview

log = logging.getLogger("image2cash.grid")

_SCRATCH = Path(tempfile.gettempdir()) / "image2cash_grid.png"


@dataclass
class Row:
    y: float  # screen y of the row centre
    cells: dict[str, str]
    boxes: dict[str, tuple[float, float]]  # column -> screen (x, y) centre

    def get(self, col: str) -> str:
        return self.cells.get(col, "")


@dataclass
class Grid:
    rect: tuple[int, int, int, int]
    headers: list[str]
    rows: list[Row]
    raw: list[Line]
    columns: dict[str, tuple[float, float]] | None = None  # name -> screen (left, right)

    def column_x(self, name: str) -> float | None:
        span = (self.columns or {}).get(name)
        return None if span is None else (span[0] + span[1]) / 2

    def text(self) -> str:
        return " | ".join(" ".join(r.cells.values()) for r in self.rows)


def capture(rect, owner=None):
    """OCR a screen rectangle. Returns (lines in screen coords, the PIL image).

    ``owner`` (the dialog or main window) is brought to the foreground first:
    a screen capture only sees what is on top.
    """
    from PIL import ImageGrab

    if owner is not None:
        try:
            owner.set_focus()
            time.sleep(0.1)
        except Exception:
            pass

    box = (rect.left, rect.top, rect.right, rect.bottom)
    img = ImageGrab.grab(bbox=box, all_screens=True)
    lines = ocr_pil(img, _SCRATCH)
    return (
        [
            Line(l.text, l.conf, l.x0 + rect.left, l.y0 + rect.top, l.x1 + rect.left, l.y1 + rect.top)
            for l in lines
            if l.text
        ],
        img,
    )


def separators(img, y0: int, y1: int, coverage: float = 1.0) -> list[int]:
    """x positions of vertical grid lines inside the band [y0, y1].

    Each pixel is compared with the median of its own row (so a blue selected
    row or a gray header is handled), and a grid line must differ from it over
    ``coverage`` of the band height. Row padding above and below text means a
    glyph stroke never reaches that coverage.
    """
    gray = img.convert("L")
    w, h = gray.size
    y0, y1 = max(0, y0), min(h - 1, y1)
    if y1 < y0:
        return []
    px = gray.load()
    medians = {y: sorted(px[x, y] for x in range(w))[w // 2] for y in range(y0, y1 + 1)}
    need = coverage * (y1 - y0 + 1)
    out: list[int] = []
    for x in range(w):
        hits = sum(1 for y in range(y0, y1 + 1) if abs(px[x, y] - medians[y]) > 25)
        if hits >= need:
            if out and x - out[-1] <= 2:
                out[-1] = x
            else:
                out.append(x)
    return out


def _fill_missing(names: list[str | None], expected: list[str]) -> list[str | None]:
    """Name columns by position. OCR reads only some faint header captions, and
    sometimes partially ('U.Price' -> 'Price'). Each recognised caption votes for
    an offset between its column and its place in ``expected`` (screen order);
    a clear majority names every column. A tie keeps the OCR names."""
    from collections import Counter

    votes = Counter(i - expected.index(n) for i, n in enumerate(names) if n in expected)
    ranked = votes.most_common(2)
    if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        return list(names)
    off = ranked[0][0]
    return [expected[i - off] if 0 <= i - off < len(expected) else None for i in range(len(names))]


def _merge(xs: list[int], tol: int = 3) -> list[int]:
    out: list[int] = []
    for x in sorted(xs):
        if out and x - out[-1] <= tol:
            continue
        out.append(x)
    return out


def split_on_separators(line: Line, seps: list[int], margin: float = 3) -> list[Line]:
    """OCR sometimes joins neighbouring cells into one line
    ('Northstar Office ... 10117'). Split such a line at the grid lines it
    crosses, placing each word by its proportional position in the box."""
    inside = [s for s in seps if line.x0 + margin < s < line.x1 - margin]
    if not inside or not line.text:
        return [line]
    text = line.text
    per_char = (line.x1 - line.x0) / max(1, len(text))
    edges = [line.x0] + inside + [line.x1]
    parts: dict[int, list[tuple[float, float, str]]] = {}
    pos = 0
    for word in text.split():
        start = text.index(word, pos)
        pos = start + len(word)
        wx0 = line.x0 + start * per_char
        wx1 = line.x0 + pos * per_char
        mid = (wx0 + wx1) / 2
        seg = next(i for i in range(len(edges) - 1) if mid <= edges[i + 1] or i == len(edges) - 2)
        parts.setdefault(seg, []).append((wx0, wx1, word))
    return [
        Line(" ".join(w for _, _, w in ws), line.conf, ws[0][0], line.y0, ws[-1][1], line.y1)
        for _, ws in sorted(parts.items())
    ]


def read_grid(rect, header_names: list[str], owner=None) -> Grid:
    """OCR a grid. ``header_names`` are the expected header captions.

    The header row is the line band containing the most known captions. Body
    lines below it are clustered into rows and assigned to the header whose
    x-span overlaps them most.
    """
    aliases = {h.lower(): h for h in header_names}
    lines, img = capture(rect, owner)
    heads = [l for l in lines if l.text.strip().lower().rstrip(":") in aliases]
    if not heads:
        return Grid((rect.left, rect.top, rect.right, rect.bottom), [], [], lines)
    # header band = the y with most header hits
    band_y = max(heads, key=lambda h: sum(abs(o.cy - h.cy) < 6 for o in heads)).cy
    heads = sorted((h for h in heads if abs(h.cy - band_y) < 6), key=lambda h: h.x0)
    named = [(aliases[h.text.strip().lower().rstrip(":")], h) for h in heads]
    top = int(min(h.y0 for _, h in named)) - rect.top
    bottom = int(max(h.y1 for _, h in named)) - rect.top
    # Grid lines may be drawn in the header, in the body, or both.
    body_lines = [l for l in lines if l.cy - rect.top > bottom + 4]
    # only the first data row: lower areas may be outside the table
    first = min(body_lines, key=lambda l: l.cy, default=None)
    body_bottom = int(first.y1 - rect.top) if first else bottom
    seps = [
        x + rect.left
        for x in _merge(
            separators(img, top - 2, bottom + 2)
            + (separators(img, bottom + 5, body_bottom + 3, coverage=0.7) if body_lines else [])
        )
    ]

    spans = []
    if len(seps) >= 2:
        # Columns are the intervals between drawn grid lines; each takes the
        # name of the header caption centred inside it. Works for left-aligned
        # (Order items) and centred (selector dialogs) headers alike.
        edges = [rect.left] + seps + [rect.right]
        # Slivers (< 12 px) come from borders and picture edges, not columns.
        cols = [(l, r) for l, r in zip(edges, edges[1:]) if r - l >= 12]
        names: list[str | None] = [None] * len(cols)
        for n, h in named:  # each caption goes to the column it overlaps most
            best = max(range(len(cols)), key=lambda i: h.overlaps_x(cols[i]), default=None)
            if best is not None and h.overlaps_x(cols[best]) > 0 and names[best] is None:
                names[best] = n
        names = _fill_missing(names, header_names)
        spans = [(n, l, r) for n, (l, r) in zip(names, cols) if n]
    if not spans:
        # Fallback: headers are left-aligned at the column start.
        for i, (name, h) in enumerate(named):
            left = h.x0 - 8 if i else rect.left
            right = named[i + 1][1].x0 - 8 if i + 1 < len(named) else rect.right
            spans.append((name, left, right))
    if not spans:
        return Grid((rect.left, rect.top, rect.right, rect.bottom), [], [], lines)

    body = [part for l in lines if l.cy > band_y + 8 for part in split_on_separators(l, seps)]
    body.sort(key=lambda l: l.cy)
    clusters: list[list[Line]] = []
    for l in body:
        if clusters and abs(clusters[-1][0].cy - l.cy) < max(6, l.h * 0.6):
            clusters[-1].append(l)
        else:
            clusters.append([l])
    rows = []
    for cl in clusters:
        cells: dict[str, str] = {}
        boxes: dict[str, tuple[float, float]] = {}
        for l in sorted(cl, key=lambda l: l.x0):
            best = max(spans, key=lambda s: l.overlaps_x((s[1], s[2])))
            if l.overlaps_x((best[1], best[2])) <= 0:
                continue  # e.g. the unnamed row-number column
            name = best[0]
            cells[name] = (cells.get(name, "") + " " + l.text).strip()
            boxes.setdefault(name, (l.cx, l.cy))
        rows.append(Row(sum(l.cy for l in cl) / len(cl), cells, boxes))
    log.debug("grid read: columns %s, %d rows: %s", [n for n, _, _ in spans], len(rows), [r.cells for r in rows][:12])
    return Grid(
        (rect.left, rect.top, rect.right, rect.bottom),
        [n for n, _, _ in spans],
        rows,
        lines,
        {n: (l, r) for n, l, r in spans},
    )


def read_stable(rect, header_names, timeout: float = 8, owner=None) -> Grid:
    """Read until two consecutive captures agree: 'wait for the list to stabilize'."""
    last = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        g = read_grid(rect, header_names, owner)
        if last is not None and g.text() == last.text():
            return g
        last = g
        time.sleep(0.2)
    if last is None:
        raise AutomationError("grid could not be read")
    raise ManualReview(f"list did not stabilize within {timeout} seconds")


def grid_below_search(dialog):
    """The grid pane in a selector dialog: the largest pane under the Search bar."""
    search = [c for c in dialog.descendants(control_type="Text") if c.element_info.name == "Search:"]
    if not search:
        raise AutomationError("selector has no Search: label")
    sr = search[0].rectangle()
    panes = [
        c
        for c in dialog.descendants(control_type="Pane")
        if c.rectangle().top >= sr.bottom and c.rectangle().height() > 100 and c.rectangle().width() > 300
    ]
    if not panes:
        raise AutomationError("selector grid pane not found")
    # the innermost (smallest) pane that is still big: the grid itself
    return min(panes, key=lambda c: c.rectangle().width() * c.rectangle().height())


def search_edit(dialog):
    edits = [c for c in dialog.descendants(control_type="Edit")]
    label = [c for c in dialog.descendants(control_type="Text") if c.element_info.name == "Search:"]
    if not edits or not label:
        raise AutomationError("selector search field not found")
    lr = label[0].rectangle()
    return min(edits, key=lambda e: abs(e.rectangle().top - lr.top) + abs(e.rectangle().left - lr.right))
