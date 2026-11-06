"""Pure checks and calculations. No I/O, no UI.

Every function here is deterministic and unit-tested. The flow refuses to touch
Fakturama while ``check_order`` returns problems.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from image2cash.models import PAYMENT_CODES, OrderDraft, PaidStatus  # noqa: F401 - re-exported

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def drift(items) -> Decimal:
    """How far a booked total may differ from the image. A catalogue price matches the
    image only to the cent, so it may be off by up to half a cent per unit, and that
    is multiplied by the quantity (qty 40 x 0.4 cent = 16 cents). Per line: 1 cent of
    rounding plus 0.6 cent per unit (half a cent, with up to 20 % VAT on top)."""
    return sum((CENT + Decimal("0.006") * abs(i.qty) for i in items), Decimal(0))


def close(a: Decimal | None, b: Decimal, items) -> bool:
    """Equal within `drift(items)`. Catalogue prices entered as rounded gross (brief 3.9)
    store a net with a small remainder, so booked totals can drift by a few cents."""
    return a is not None and abs(money(Decimal(a)) - money(Decimal(b))) <= max(CENT, drift(items))


def line_net(qty: Decimal, unit_net: Decimal, discount_pct: Decimal) -> Decimal:
    """Brief 3.16: quantity x unit net price x (1 - discount / 100)."""
    return money(qty * unit_net * (1 - discount_pct / 100))


def gross_price(unit_net: Decimal, vat_pct: Decimal) -> Decimal:
    """Brief 3.9: product master Price (gross) = unit net x (1 + VAT/100), 2 dp.

    The transaction-line discount is deliberately not applied here.
    """
    return money(unit_net * (1 + vat_pct / 100))


def format_pct(value: Decimal) -> str:
    return format(value.normalize(), "f")


def vat_name(vat_pct: Decimal) -> str:
    """Brief 3.4/3.6: VAT rows are named ``VAT`` + percentage, e.g. ``VAT 19%``."""
    return f"VAT {format_pct(vat_pct)}%"


def totals(order: OrderDraft) -> tuple[Decimal, Decimal, Decimal]:
    """Net, VAT and gross from the item rows; VAT is rounded per rate group."""
    net = Decimal(0)
    by_rate: dict[Decimal, Decimal] = {}
    for i in order.items:
        ln = line_net(i.qty, i.unit_net, i.discount_pct)
        net += ln
        by_rate[i.vat_pct] = by_rate.get(i.vat_pct, Decimal(0)) + ln
    vat = sum((money(base * rate / 100) for rate, base in by_rate.items()), Decimal(0))
    net, vat = money(net), money(vat)
    return net, vat, money(net + vat)


_ZIP = re.compile(r"^[0-9A-Z][0-9A-Z \-]{2,9}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_order(order: OrderDraft) -> list[str]:
    """Return human-readable problems; an empty list means the draft is usable."""
    problems: list[str] = []
    if not order.external_ref.strip():
        problems.append("external reference is empty")
    if not _CURRENCY.match(order.currency):
        problems.append(f"currency {order.currency!r} is not a 3-letter code")

    d = order.debtor
    for label, value in (
        ("company", d.company),
        ("first name", d.first_name),
        ("last name", d.last_name),
    ):
        if not value.strip():
            problems.append(f"debtor {label} is empty")
    if d.email and not _EMAIL.match(d.email):
        problems.append(f"email {d.email!r} looks malformed")
    for kind, address in (("billing", d.billing), ("delivery", d.delivery)):
        if not _ZIP.match(address.zip):
            problems.append(f"{kind} ZIP {address.zip!r} looks malformed")
        for field in ("street", "city", "country"):
            if not getattr(address, field).strip():
                problems.append(f"{kind} {field} is empty")

    seen: set[str] = set()
    for item in order.items:
        if item.sku in seen:
            problems.append(f"SKU {item.sku} appears twice")
        seen.add(item.sku)
        if item.qty <= 0:
            problems.append(f"item {item.pos}: quantity {item.qty} is not positive")
        if not (0 <= item.discount_pct < 100):
            problems.append(f"item {item.pos}: discount {item.discount_pct}% out of range")
        if not (0 <= item.vat_pct < 100):
            problems.append(f"item {item.pos}: VAT {item.vat_pct}% out of range")
        expected = line_net(item.qty, item.unit_net, item.discount_pct)
        if expected != money(item.line_net):
            problems.append(
                f"item {item.pos} ({item.sku}): {item.qty} x {item.unit_net} "
                f"- {item.discount_pct}% = {expected}, source says {item.line_net}"
            )

    net, vat, gross = totals(order)
    for label, computed, printed in (
        ("net total", net, order.net_total),
        ("VAT total", vat, order.vat_total),
        ("gross total", gross, order.gross_total),
    ):
        if computed != money(printed):
            problems.append(f"{label}: items give {computed}, source says {printed}")

    p = order.payment
    if p.status is PaidStatus.PAID and p.paid_date is None:
        problems.append("status is PAID but no payment date was found")
    if p.status is PaidStatus.OTHER:
        problems.append("paid status is neither PAID nor UNPAID")
    if not p.method.strip():
        problems.append("payment method is empty")
    elif p.payment_code is None:
        problems.append(f"payment method {p.method!r} has no payment-code mapping: pick its code (brief 2.10.4)")
    return problems


# Eastern-Arabic / Persian digits and separators. The test workspace runs with
# a ps/AF currency locale, so Fakturama renders amounts like "۴۵۰٫۰۰ ؋".
_DIGITS = str.maketrans(
    {
        **{chr(0x0660 + i): str(i) for i in range(10)},
        **{chr(0x06F0 + i): str(i) for i in range(10)},
        "٫": ".",
        "٬": ",",
        "−": "-",
    }
)


def parse_amount(text: str | None) -> Decimal | None:
    """Parse a money/percent string as shown by Fakturama or OCR.

    Handles '1,234.56', '1.234,56', '$450.00', '-10.00 %', '19%' and
    Persian-digit renderings. Returns ``None`` when no number is present.
    """
    text = (text or "").translate(_DIGITS)
    cleaned = re.sub(r"[^\d,.\-]", "", text)
    if not re.search(r"\d", cleaned):
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        cleaned = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _zip_core(zip_: str) -> str:
    """'ME 04101' -> '04101': a leading two-letter state code is not part of the ZIP.
    Postcodes that start with letters ('S4P 2S7', 'SW1A 1AA') are kept whole."""
    parts = zip_.split()
    return " ".join(parts[1:]) if len(parts) > 1 and len(parts[0]) == 2 and parts[0].isalpha() else zip_.strip()


def _city_core(city: str) -> str:
    """'Portland, ME' -> 'Portland'."""
    return re.sub(r",\s*[A-Za-z]{2}$", "", city.strip())


def same_place(zip_a: str, city_a: str, zip_b: str, city_b: str) -> bool:
    """Same ZIP and City, wherever the state code was written ('ME 04101 Portland' and
    '04101 Portland, ME' are one address)."""
    return (
        _zip_core(zip_a).casefold() == _zip_core(zip_b).casefold()
        and _city_core(city_a).casefold() == _city_core(city_b).casefold()
    )
