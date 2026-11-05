"""Render 4 pharmaceutical orders in layouts the template parser has never seen.

    uv run python tools/make_unseen.py

Writes samples/unseen/<name>.(png|jpg|pdf) plus <name>.truth.json (the values a
correct extraction must produce). They test layout robustness honestly: the
offline template parser is expected to reject most of them, which is what the
AI fallback (last checkpoint) is for.
"""

from __future__ import annotations

import json
import random
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from image2cash.validate import line_net, money

OUT = Path(__file__).resolve().parents[1] / "samples" / "unseen"
INK, GREY, LINE = (25, 25, 25), (110, 110, 110), (170, 170, 170)


def font(size, bold=False):
    for name in (("arialbd.ttf" if bold else "arial.ttf"), ("segoeuib.ttf" if bold else "segoeui.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def totals(items):
    net, by = Decimal(0), {}
    for it in items:
        ln = line_net(Decimal(str(it["qty"])), Decimal(str(it["unit"])), Decimal(str(it.get("disc", 0))))
        it["line"] = ln
        net += ln
        by[it["vat"]] = by.get(it["vat"], Decimal(0)) + ln
    vat = sum((money(b * Decimal(str(r)) / 100) for r, b in by.items()), Decimal(0))
    return money(net), money(vat), money(net + vat)


def truth(name, o):
    net, vat, gross = totals(o["items"])
    data = {
        "external_ref": o["ref"],
        "order_date": o["date"],
        "currency": "USD",
        "company": o["company"],
        "contact": o["contact"],
        "billing": o["bill"],
        "delivery": o["ship"],
        "payment": {"method": o["method"], "status": o["status"], "paid_date": o.get("paid")},
        "items": [{k: str(v) for k, v in it.items()} for it in o["items"]],
        "net_total": str(net),
        "vat_total": str(vat),
        "gross_total": str(gross),
    }
    (OUT / f"{name}.truth.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return net, vat, gross


# 1. Hospital purchase order: different labels, columns in another order, no discount column
def hospital_po():
    o = {
        "ref": "PO-HSP-55120",
        "date": "2026-11-02",
        "company": "St. Brigid Regional Medical Center",
        "contact": "Alan Okafor",
        "method": "Bank Transfer",
        "status": "UNPAID",
        "bill": [
            "St. Brigid Regional Medical Center",
            "Accounts Payable",
            "400 Lakeview Ave",
            "Cleveland, OH 44114",
            "United States",
        ],
        "ship": ["St. Brigid Central Pharmacy", "Dock 3, 410 Lakeview Ave", "Cleveland, OH 44114", "United States"],
        "items": [
            {"sku": "NDC-0409-4888", "desc": "Sodium Chloride 0.9% 1000 mL", "qty": 120, "unit": "3.85", "vat": 0},
            {"sku": "NDC-63323-262", "desc": "Ondansetron 2 mg/mL 2 mL vial", "qty": 50, "unit": "1.95", "vat": 0},
            {"sku": "OTC-MSK-N95", "desc": "N95 Respirator, box of 20", "qty": 30, "unit": "18.40", "vat": 5.75},
        ],
    }
    net, vat, gross = truth("hospital_po", o)
    img = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(img)
    d.text((80, 70), "ST. BRIGID REGIONAL MEDICAL CENTER", font=font(40, True), fill=INK)
    d.text((80, 125), "Materials Management · Purchase Order", font=font(26), fill=GREY)
    d.rectangle((1180, 60, 1620, 200), outline=INK, width=3)
    d.text((1200, 75), "P.O. Number", font=font(22), fill=GREY)
    d.text((1200, 105), o["ref"], font=font(34, True), fill=INK)
    d.text((1200, 155), "Issued  11/02/2026", font=font(24), fill=INK)
    y = 260
    for x, title, lines in (
        (80, "VENDOR", ["Meridian Rx Supply", "Order Desk"]),
        (620, "INVOICE TO", o["bill"]),
        (1160, "DELIVER TO", o["ship"]),
    ):
        d.text((x, y), title, font=font(22, True), fill=GREY)
        for i, t in enumerate(lines):
            d.text((x, y + 36 + i * 32), t, font=font(24), fill=INK)
    d.text(
        (80, 520),
        f"Requested by: {o['contact']}   ·   Terms: Net 30, wire / bank transfer   ·   Currency USD",
        font=font(24),
        fill=INK,
    )
    cols = [(80, "Qty"), (200, "Item Code"), (520, "Product"), (1120, "Price ea."), (1300, "Tax"), (1440, "Extended")]
    y = 600
    d.rectangle((70, y - 10, 1630, y + 40), fill=(225, 225, 225))
    for x, t in cols:
        d.text((x, y), t, font=font(24, True), fill=INK)
    for n, it in enumerate(o["items"]):
        yy = y + 70 + n * 60
        for x, t in zip(
            [c[0] for c in cols],
            [str(it["qty"]), it["sku"], it["desc"], f"${it['unit']}", f"{it['vat']}%", f"${it['line']}"],
        ):
            d.text((x, yy), t, font=font(24), fill=INK)
        d.line((70, yy + 45, 1630, yy + 45), fill=LINE, width=1)
    yt = y + 70 + len(o["items"]) * 60 + 60
    for i, (lab, v) in enumerate((("Subtotal", net), ("Sales tax", vat), ("PO TOTAL", gross))):
        d.text((1180, yt + i * 46), lab, font=font(26, i == 2), fill=INK)
        d.text((1440, yt + i * 46), f"${v}", font=font(26, i == 2), fill=INK)
    d.text((80, 2080), "Authorized signature: ______________________     All amounts USD.", font=font(22), fill=GREY)
    img.save(OUT / "hospital_po.png")


# 2. Faxed / scanned clinic order: rotated, noisy, blurred, JPEG
def scanned_fax():
    o = {
        "ref": "CL-7781",
        "date": "2026-10-29",
        "company": "Maple Grove Family Clinic",
        "contact": "Dr. Sofia Ruiz",
        "method": "Credit Card",
        "status": "PAID",
        "paid": "2026-10-30",
        "bill": ["Maple Grove Family Clinic", "77 Elm Street", "Burlington, VT 05401", "United States"],
        "ship": ["Maple Grove Family Clinic", "77 Elm Street", "Burlington, VT 05401", "United States"],
        "items": [
            {
                "sku": "NDC-0093-4155",
                "desc": "Amoxicillin 500 mg caps 100ct",
                "qty": 4,
                "unit": "18.40",
                "disc": 0,
                "vat": 0,
            },
            {"sku": "OTC-ALC-PAD", "desc": "Alcohol Prep Pads 200", "qty": 10, "unit": "4.15", "disc": 0, "vat": 6.25},
        ],
    }
    net, vat, gross = truth("scanned_fax", o)
    img = Image.new("RGB", (1600, 2000), (246, 244, 238))
    d = ImageDraw.Draw(img)
    d.text((70, 60), "FAX  ·  ORDER FORM  ·  Page 1/1", font=font(30, True), fill=INK)
    d.text(
        (70, 110),
        "To: Meridian Rx Supply   From: Maple Grove Family Clinic   Tel +1 802 555 0119",
        font=font(24),
        fill=INK,
    )
    fields = [
        ("Order ref", o["ref"]),
        ("Date", "Oct 29, 2026"),
        ("Ordered by", o["contact"]),
        ("Clinic", o["company"]),
        ("Address", "77 Elm Street, Burlington, VT 05401"),
        ("Payment", "Credit Card - charged 10/30/2026 (PAID)"),
    ]
    for i, (k, v) in enumerate(fields):
        d.text((70, 200 + i * 50), f"{k}:", font=font(26, True), fill=INK)
        d.text((330, 200 + i * 50), v, font=font(26), fill=INK)
    y = 560
    d.text(
        (70, y),
        "CODE            DESCRIPTION                          QTY     EACH      TOTAL",
        font=font(24, True),
        fill=INK,
    )
    for n, it in enumerate(o["items"]):
        d.text(
            (70, y + 50 + n * 46),
            f"{it['sku']:<16}{it['desc']:<36}{it['qty']:>4}   {it['unit']:>7}   {it['line']:>8}",
            font=font(24),
            fill=INK,
        )
    d.text((70, y + 220), f"Net {net}    Tax {vat}    Total USD {gross}", font=font(26, True), fill=INK)
    img = img.rotate(1.6, expand=True, fillcolor=(246, 244, 238)).filter(ImageFilter.GaussianBlur(0.9))
    rnd = random.Random(7)
    px = img.load()
    for _ in range(60000):  # fax speckle
        x, y2 = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y2] = (60, 60, 60) if rnd.random() < 0.5 else (255, 255, 255)
    img.convert("L").save(OUT / "scanned_fax.jpg", quality=55)


# 3. Two-column distributor form with discounts and a mixed tax rate, label: value pairs
def distributor_form():
    o = {
        "ref": "NG-ORD-2026-0419",
        "date": "2026-11-04",
        "company": "Prairie Wellness Pharmacy Ltd",
        "contact": "Ethan Moreau",
        "method": "SEPA Direct Debit",
        "status": "UNPAID",
        "bill": ["Prairie Wellness Pharmacy Ltd", "1820 Albert Street", "Regina, SK S4P 2S7", "Canada"],
        "ship": ["Prairie Wellness Pharmacy Ltd", "1820 Albert Street", "Regina, SK S4P 2S7", "Canada"],
        "items": [
            {
                "sku": "NDC-0378-0208",
                "desc": "Metformin 500 mg 500ct",
                "qty": 6,
                "unit": "32.75",
                "disc": 7.5,
                "vat": 0,
            },
            {"sku": "NDC-0071-0155", "desc": "Atorvastatin 20 mg 90ct", "qty": 8, "unit": "24.60", "disc": 5, "vat": 0},
            {
                "sku": "OTC-GAU-4X4",
                "desc": "Sterile Gauze 4x4 box 100",
                "qty": 12,
                "unit": "7.80",
                "disc": 0,
                "vat": 6.25,
            },
            {"sku": "OTC-THR-DIG", "desc": "Digital Thermometer", "qty": 10, "unit": "11.20", "disc": 10, "vat": 6.25},
        ],
    }
    net, vat, gross = truth("distributor_form", o)
    img = Image.new("RGB", (1650, 2100), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1650, 150), fill=(18, 92, 74))
    d.text((60, 45), "Northgate Medical Distribution — Customer Order", font=font(40, True), fill="white")
    left = [("Order #", o["ref"]), ("Order date", "2026-11-04"), ("Account", "PH-55021"), ("Buyer", o["contact"])]
    right = [
        ("Pharmacy", o["company"]),
        ("Street", "1820 Albert Street"),
        ("City / Prov / Postal", "Regina, SK S4P 2S7"),
        ("Country", "Canada"),
    ]
    for col, pairs in ((60, left), (860, right)):
        for i, (k, v) in enumerate(pairs):
            d.text((col, 210 + i * 70), k, font=font(22), fill=GREY)
            d.text((col, 238 + i * 70), v, font=font(28, True), fill=INK)
    d.text(
        (60, 520),
        "Settlement: pre-authorized debit (SEPA-style) · Status: open · Ship to = bill to",
        font=font(24),
        fill=INK,
    )
    heads = ["Description", "SKU", "Units", "List", "Disc %", "Tax %", "Net"]
    xs = [60, 560, 860, 990, 1160, 1310, 1450]
    d.line((50, 600, 1600, 600), fill=INK, width=2)
    for x, h in zip(xs, heads):
        d.text((x, 612), h, font=font(24, True), fill=INK)
    d.line((50, 655, 1600, 655), fill=INK, width=2)
    for n, it in enumerate(o["items"]):
        yy = 680 + n * 58
        for x, t in zip(
            xs, [it["desc"], it["sku"], str(it["qty"]), it["unit"], str(it["disc"]), str(it["vat"]), str(it["line"])]
        ):
            d.text((x, yy), t, font=font(24), fill=INK)
    yt = 680 + len(o["items"]) * 58 + 50
    d.text((1160, yt), f"Net amount      {net}", font=font(26), fill=INK)
    d.text((1160, yt + 44), f"Tax                  {vat}", font=font(26), fill=INK)
    d.text((1160, yt + 88), f"Amount due USD {gross}", font=font(28, True), fill=INK)
    img.save(OUT / "distributor_form.png")


# 4. PDF: a plain letter-style order (prose header, compact table), saved as PDF
def letter_pdf():
    o = {
        "ref": "LTR-0907-B",
        "date": "2026-11-03",
        "company": "Harbor Point Long-Term Care",
        "contact": "Grace Lindqvist",
        "method": "Bank Transfer",
        "status": "PAID",
        "paid": "2026-11-05",
        "bill": ["Harbor Point Long-Term Care", "12 Wharf Road", "Portland, ME 04101", "United States"],
        "ship": ["Harbor Point Long-Term Care", "12 Wharf Road", "Portland, ME 04101", "United States"],
        "items": [
            {
                "sku": "NDC-0641-6078",
                "desc": "Heparin 5000 U/mL vial 10ct",
                "qty": 3,
                "unit": "54.20",
                "disc": 0,
                "vat": 0,
            },
            {
                "sku": "OTC-SYR-10ML",
                "desc": "Syringe 10 mL Luer Lock 100ct",
                "qty": 5,
                "unit": "21.30",
                "disc": 0,
                "vat": 6.25,
            },
        ],
    }
    net, vat, gross = truth("letter_pdf", o)
    img = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(img)
    d.text((100, 100), "Harbor Point Long-Term Care", font=font(34, True), fill=INK)
    d.text((100, 150), "12 Wharf Road, Portland, ME 04101 · United States", font=font(24), fill=GREY)
    d.text((100, 260), "November 3, 2026", font=font(26), fill=INK)
    body = [
        "Meridian Rx Supply — Order Desk",
        "",
        f"Re: our order {o['ref']}",
        "",
        "Please supply the items below to the address above. Contact: Grace Lindqvist.",
        f"We paid the full amount of USD {gross} by bank transfer on November 5, 2026.",
    ]
    for i, t in enumerate(body):
        d.text((100, 340 + i * 40), t, font=font(26), fill=INK)
    y = 640
    for n, it in enumerate(o["items"]):
        d.text(
            (100, y + n * 44),
            f"• {it['qty']} x {it['desc']} ({it['sku']}) at ${it['unit']} = ${it['line']}  (tax {it['vat']}%)",
            font=font(25),
            fill=INK,
        )
    d.text((100, y + 140), f"Subtotal ${net}, tax ${vat}, total ${gross}.", font=font(26, True), fill=INK)
    d.text((100, y + 260), "Kind regards,", font=font(26), fill=INK)
    d.text((100, y + 300), "Grace Lindqvist, Director of Nursing", font=font(26), fill=INK)
    img.save(OUT / "letter_pdf.pdf", "PDF", resolution=200)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for make in (hospital_po, scanned_fax, distributor_form, letter_pdf):
        make()
        print("wrote", make.__name__)
