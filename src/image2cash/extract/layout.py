"""Label-anchored parser: OCR lines with boxes -> OrderDraft.

No fixed coordinates. A value is found relative to its printed label: the
nearest line(s) below the label whose x-span overlaps the label's column. The
item table is parsed by header columns. All thresholds are relative to the
median text height so the parser works at any resolution.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from statistics import median

from image2cash.extract.ocr import Line
from image2cash.models import Address, Debtor, Item, OrderDraft, PaidStatus, Payment


class ExtractionError(ValueError):
    pass


_LABELS = {
    "EXTERNAL REFERENCE",
    "ORDER DATE",
    "CUSTOMER ID",
    "CURRENCY",
    "COMPANY",
    "CUSTOMER ALIAS",
    "CONTACT NAME",
    "EMAIL",
    "PHONE",
    "BILLING ADDRESS",
    "DELIVERY ADDRESS",
    "PAYMENT METHOD",
    "PAID STATUS",
    "PAYMENT DATE",
    "NET TOTAL",
    "VAT TOTAL",
    "GROSS TOTAL",
}
_SECTIONS = {"ORDER", "CUSTOMER AND CONTACT", "ADDRESSES", "PAYMENT", "ITEMS"}


def _norm(text: str) -> str:
    return " ".join(text.upper().replace(":", " ").split())


class Page:
    def __init__(self, lines: list[Line]):
        self.all_lines = list(lines)  # includes text-less boxes (lost headers)
        self.lines = [l for l in lines if l.text]
        if not self.lines:
            raise ExtractionError("OCR returned no text")
        self.lh = median(l.h for l in self.lines)
        self.min_conf = min(l.conf for l in self.lines)
        self.boxes: dict[str, list[list[float]]] = {}  # field key -> source boxes (for the UI)

    def keep(self, key: str | None, lines: list[Line]) -> None:
        if key:
            self.boxes.setdefault(key, []).extend([l.x0, l.y0, l.x1, l.y1] for l in lines)

    def label(self, name: str) -> Line:
        hits = [l for l in self.lines if _norm(l.text) == name]
        if not hits:
            raise ExtractionError(f"label {name!r} not found")
        return min(hits, key=lambda l: (l.y0, l.x0))

    def _is_marker(self, line: Line) -> bool:
        n = _norm(line.text)
        return n in _LABELS or n in _SECTIONS

    def below(self, label: Line, max_lines: int = 1, max_gap: float = 3.0) -> list[Line]:
        """Lines under ``label`` in its column, stopping at the next label or a gap."""
        column = [
            l
            for l in self.lines
            if l.y0 > label.y0 + label.h * 0.5
            # Same column: the value covers at least half of the label's width
            # (handles left-aligned fields and centred total boxes alike).
            and l.overlaps_x(label) >= 0.5 * (label.x1 - label.x0)
        ]
        column.sort(key=lambda l: l.y0)
        out: list[Line] = []
        last = label.y1
        for l in column:
            if self._is_marker(l) or (l.y0 - last) > self.lh * max_gap:
                break
            out.append(l)
            last = l.y1
            if len(out) >= max_lines:
                break
        return out

    def value(self, name: str, key: str | None = None) -> str:
        found = self.below(self.label(name))
        if not found:
            raise ExtractionError(f"no value under {name!r}")
        self.keep(key, found[:1])
        return found[0].text.strip()


def _date(text: str, field: str) -> date:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        raise ExtractionError(f"{field}: {text!r} is not an ISO date")
    return date(int(m[1]), int(m[2]), int(m[3]))


def _dec(text: str, field: str) -> Decimal:
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ExtractionError(f"{field}: {text!r} is not a number") from exc


# 'Boston, MA 02118' / 'Toronto, ON M5V 2T6' first, then 'ZIP City' (German style).
_US_CA = re.compile(r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?|[A-Z]\d[A-Z] ?\d[A-Z]\d)$")
_ZIP_CITY = re.compile(r"^(\S+)\s+(.+)$")


def split_city_line(line: str) -> tuple[str, str]:
    """-> (zip, city). Fakturama has no state field, so the state stays on the city: 'Boston, MA'."""
    if m := _US_CA.match(line):
        return m[3], f"{m[1]}, {m[2]}"
    if m := _ZIP_CITY.match(line):
        return m[1], m[2]
    raise ExtractionError(f"cannot split ZIP/City from {line!r}")


def _address(page: Page, label: str, key: str) -> Address:
    found = page.below(page.label(label), max_lines=8, max_gap=4)  # the next section label ends the block anyway
    block = [l.text.strip() for l in found]
    if len(block) < 4:
        raise ExtractionError(f"{label}: expected >= 4 lines, got {block}")
    page.keep(key, found)
    name, street, *middle, zip_city, country = block
    zip_, city = split_city_line(zip_city)
    return Address(name=name, street=street, zip=zip_, city=city, country=country, extra_lines=middle)


_ITEM_HEADERS = {
    "#": "pos",
    "SKU": "sku",
    "DESCRIPTION": "description",
    "QTY": "qty",
    "UNIT": "unit",
    "DISC.": "discount",
    "DISC": "discount",
    "VAT": "vat",
}


def _items(page: Page) -> list[Item]:
    sku_head = page.label("SKU")
    band = [l for l in page.all_lines if abs(l.cy - sku_head.cy) < page.lh * 1.2]
    band.sort(key=lambda l: l.x0)
    columns: list[tuple[str, Line]] = []
    unnamed = 0
    for head in band:
        key = _ITEM_HEADERS.get(_norm(head.text))
        if key is None:
            # Two-line headers ("Unit net (EUR)", "Line net (EUR)") often come
            # back as a box with no text. Their order in the row is fixed:
            # the first is the unit price, the second the line total.
            key = ("unit_net", "line_net")[min(unnamed, 1)]
            unnamed += 1
        columns.append((key, head))
    names = [k for k, _ in columns]
    for needed in ("sku", "description", "qty", "unit_net", "vat", "line_net"):
        if needed not in names:
            raise ExtractionError(f"item table: column {needed!r} not found (have {names})")

    # Column x-spans: from midway to the previous header to midway to the next.
    spans = []
    for i, (key, head) in enumerate(columns):
        left = (columns[i - 1][1].x1 + head.x0) / 2 if i else head.x0 - page.lh * 4
        right = (head.x1 + columns[i + 1][1].x0) / 2 if i + 1 < len(columns) else head.x1 + page.lh * 4
        spans.append((key, left, right))

    stop = page.label("NET TOTAL").y0
    body = [l for l in page.lines if sku_head.y1 + page.lh * 0.5 < l.cy < stop]
    rows: list[list[Line]] = []
    for l in sorted(body, key=lambda l: l.cy):
        if rows and abs(rows[-1][0].cy - l.cy) < page.lh * 0.8:
            rows[-1].append(l)
        else:
            rows.append([l])

    items = []
    for row in rows:
        cells: dict[str, str] = {}
        where: dict[str, list[Line]] = {}
        for l in row:
            key = max(spans, key=lambda s: l.overlaps_x((s[1], s[2])))[0]
            cells[key] = (cells.get(key, "") + " " + l.text).strip()
            where.setdefault(key, []).append(l)
        if not cells.get("sku"):
            continue
        n = len(items) + 1
        pos = int(cells["pos"]) if cells.get("pos", "").isdigit() else n
        for col, ls in where.items():
            page.keep(f"items.{pos}.{col}", ls)
        items.append(
            Item(
                pos=pos,
                sku=cells["sku"],
                description=cells.get("description", ""),
                qty=_dec(cells.get("qty", ""), f"item {n} qty"),
                unit=cells.get("unit"),
                unit_net=_dec(cells.get("unit_net", ""), f"item {n} unit net"),
                discount_pct=_dec(cells.get("discount", "0"), f"item {n} discount"),
                vat_pct=_dec(cells.get("vat", ""), f"item {n} VAT"),
                line_net=_dec(cells.get("line_net", ""), f"item {n} line net"),
            )
        )
    if not items:
        raise ExtractionError("item table has no rows")
    return items


def parse(lines: list[Line], source: str | None = None, boxes: dict | None = None) -> OrderDraft:
    """``boxes``, if given, is filled with field key -> [[x0, y0, x1, y1], ...] in image pixels."""
    page = Page(lines)
    if boxes is not None:
        page.boxes = boxes
    contact = page.value("CONTACT NAME", "debtor.contact").split()
    if len(contact) < 2:
        raise ExtractionError(f"contact name {contact!r} has no first/last split")
    status_text = _norm(page.value("PAID STATUS", "payment.status"))
    status = {"PAID": PaidStatus.PAID, "UNPAID": PaidStatus.UNPAID, "OPEN": PaidStatus.UNPAID}.get(
        status_text, PaidStatus.OTHER
    )
    paid_date = None
    try:
        paid_date = _date(page.value("PAYMENT DATE", "payment.paid_date"), "payment date")
    except ExtractionError:
        if status is PaidStatus.PAID:
            raise

    def total(name: str) -> Decimal:
        return _dec(page.value(name, name.lower().replace(" ", "_")), name.lower())

    return OrderDraft(
        external_ref=page.value("EXTERNAL REFERENCE", "external_ref"),
        order_date=_date(page.value("ORDER DATE", "order_date"), "order date"),
        currency=page.value("CURRENCY", "currency").upper(),
        debtor=Debtor(
            customer_id=page.value("CUSTOMER ID", "debtor.customer_id"),
            company=page.value("COMPANY", "debtor.company"),
            first_name=contact[0],
            last_name=" ".join(contact[1:]),
            alias=page.value("CUSTOMER ALIAS", "debtor.alias"),
            email="".join(page.value("EMAIL", "debtor.email").split()),  # OCR inserts spaces; e-mails have none
            phone=page.value("PHONE", "debtor.phone"),
            billing=_address(page, "BILLING ADDRESS", "debtor.billing"),
            delivery=_address(page, "DELIVERY ADDRESS", "debtor.delivery"),
        ),
        payment=Payment(method=page.value("PAYMENT METHOD", "payment.method"), status=status, paid_date=paid_date),
        items=_items(page),
        net_total=total("NET TOTAL"),
        vat_total=total("VAT TOTAL"),
        gross_total=total("GROSS TOTAL"),
        source=source,
        engine="indra-ocr+layout",
        min_confidence=page.min_conf,
    )
