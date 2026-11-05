"""Render a synthetic 'SALES ORDER INPUT' image in the same layout family as the
assessment sample, from a JSON spec. Used to exercise other branches of the flow
(new debtor with billing == delivery, new payment method, new VAT rate, UNPAID).

    uv run python tools/make_sample.py samples/order_SO-TEST-0002.json
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from image2cash.validate import line_net, money

W = 1489
ROW0, ROW_H = 1495, 78
NAVY, BLUE, GREY, BOX = (18, 42, 68), (46, 117, 182), (95, 105, 120), (232, 240, 248)


def font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), ("arialbd.ttf" if bold else "arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(spec: dict, out: Path) -> None:
    totals_y = max(1790, ROW0 + len(spec["items"]) * ROW_H + 20)  # the page grows with the item count
    H = totals_y + 258
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 110), fill=NAVY)
    d.text((93, 40), spec.get("brand", "TJM Labs  |  Automation Assessment"), font=font(24, True), fill="white")
    d.text((1180, 44), "SYNTHETIC TEST DATA", font=font(19, True), fill="white")
    d.text((91, 165), "SALES ORDER INPUT", font=font(52, True), fill=NAVY)
    d.text((93, 236), "Source document - extract all transaction values from this image", font=font(21), fill=GREY)

    def label(x, y, text):
        d.text((x, y), text, font=font(15, True), fill=GREY)

    def value(x, y, text, bold=False, size=24):
        d.text((x, y), text, font=font(size, bold), fill=NAVY)

    def section(y, text, h):
        d.text((94, y), text, font=font(21, True), fill=BLUE)
        d.rounded_rectangle((91, y + 30, 1365, y + 30 + h), 10, fill=BOX, outline=(200, 210, 222))

    o, c, p = spec["order"], spec["customer"], spec["payment"]
    section(285, "ORDER", 140)
    for x, lab, val, b in (
        (120, "EXTERNAL REFERENCE", o["ref"], True),
        (482, "ORDER DATE", o["date"], False),
        (800, "CUSTOMER ID", o["customer_id"], False),
        (1079, "CURRENCY", o["currency"], True),
    ):
        label(x, 352, lab)
        value(x, 384, val, b)

    section(505, "CUSTOMER AND CONTACT", 210)
    label(120, 575, "COMPANY")
    value(120, 605, c["company"], True)
    label(120, 658, "CUSTOMER ALIAS")
    value(120, 690, c["alias"])
    label(780, 565, "CONTACT NAME")
    value(782, 596, c["contact"])
    label(780, 635, "EMAIL")
    value(782, 667, c["email"])
    label(780, 705, "PHONE")
    value(782, 737, c["phone"])

    section(800, "ADDRESSES", 255)
    for x, lab, lines in ((120, "BILLING ADDRESS", c["billing"]), (780, "DELIVERY ADDRESS", c["delivery"])):
        label(x, 872, lab)
        for i, line in enumerate(lines):
            value(x, 915 + i * 34, line, size=23)

    section(1135, "PAYMENT", 130)
    label(120, 1212, "PAYMENT METHOD")
    value(122, 1244, p["method"], True)
    label(664, 1212, "PAID STATUS")
    value(666, 1244, p["status"], True)
    label(985, 1212, "PAYMENT DATE")
    value(987, 1244, p.get("date") or "-")

    d.text((95, 1370), "ITEMS", font=font(21, True), fill=BLUE)
    cols = [
        (95, 140, "#"),
        (140, 375, "SKU"),
        (375, 705, "Description"),
        (705, 775, "Qty"),
        (775, 855, "Unit"),
        (855, 1000, "Unit net"),
        (1000, 1105, "Disc."),
        (1105, 1188, "VAT"),
        (1188, 1365, "Line net"),
    ]
    d.rectangle((92, 1400, 1365, 1455), fill=NAVY)
    for x0, x1, t in cols:
        w = d.textlength(t, font=font(17, True))
        d.text(((x0 + x1 - w) / 2, 1417), t, font=font(17, True), fill="white")
    net = Decimal(0)
    by_rate: dict[Decimal, Decimal] = {}
    for i, it in enumerate(spec["items"]):
        y = ROW0 + i * ROW_H
        q, u, disc, rate = (Decimal(str(it[k])) for k in ("qty", "unit_net", "disc", "vat"))
        ln = line_net(q, u, disc)
        net += ln
        by_rate[rate] = by_rate.get(rate, Decimal(0)) + ln
        cells = [
            str(i + 1),
            it["sku"],
            it["desc"],
            str(it["qty"]),
            "pcs",
            f"{u:.2f}",
            f"{disc.normalize():f}%",
            f"{rate.normalize():f}%",
            f"{ln:.2f}",
        ]
        for (x0, x1, _), t in zip(cols, cells):
            f = font(19, True) if t in (it["sku"], f"{ln:.2f}") else font(19)
            x = x0 + 10 if t == it["desc"] else (x0 + x1 - d.textlength(t, font=f)) / 2
            d.text((x, y), t, font=f, fill=NAVY)
    vat = sum((money(b * r / 100) for r, b in by_rate.items()), Decimal(0))  # per rate group, as validate.totals
    section(totals_y, "", 110)
    for x0, x1, lab, v in (
        (92, 516, "NET TOTAL", net),
        (516, 940, "VAT TOTAL", vat),
        (940, 1365, "GROSS TOTAL", net + vat),
    ):
        w = d.textlength(lab, font=font(17, True))
        d.text(((x0 + x1 - w) / 2, totals_y + 42), lab, font=font(17, True), fill=GREY)
        t = f"{o['currency']} {money(v):.2f}"
        w = d.textlength(t, font=font(30, True))
        d.text(((x0 + x1 - w) / 2, totals_y + 82), t, font=font(30, True), fill=NAVY)
    d.text((91, H - 48), "All data is synthetic | Prices are net | Dates: YYYY-MM-DD", font=font(17), fill=GREY)
    img.save(out)


if __name__ == "__main__":
    src = Path(sys.argv[1])
    render(json.loads(src.read_text(encoding="utf-8")), src.with_suffix(".png"))
    print(src.with_suffix(".png"))
