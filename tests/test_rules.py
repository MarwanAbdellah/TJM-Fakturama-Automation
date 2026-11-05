"""Business rules that guard identity, currency and idempotency (no Fakturama needed)."""

import pytest

from image2cash.steps import order
from image2cash.steps.order import booked_currency
from image2cash.steps.product import sku_eq
from image2cash.ui.core import ManualReview


@pytest.mark.parametrize(
    "seen, sku, same",
    [
        ("OTC-GAU-4X4", "OTC-GAU-4X4", True),
        ("| OTC-GAU-4X4", "OTC-GAU-4X4", True),  # cell border read as '|'
        ("SAMPLO3", "SAMPL03", True),  # O/0 confusion
        ("OTC-ALC-PAD", "OTC-GAU-4X4", False),
        ("SAMPL04", "SAMPL03", False),
        ("", "X", False),
        ("M5J 251", "M5J 2S1", True),  # Canadian postal code, S read as 5
        ("CUSTO00006", "CUST000006", True),
    ],
)
def test_sku_eq(seen, sku, same):
    assert sku_eq(seen, sku) is same


def test_booked_currency():
    assert booked_currency("$0.00") == "USD"
    assert booked_currency("0,00 €") == "EUR"
    assert booked_currency("۰٫۰۰ ؋") == "AFN"
    assert booked_currency("0.00") is None


class _Ctx:
    def __init__(self, resume):
        self.resume, self.resume_at, self.order_no = resume, None, ""
        self.order = type("O", (), {"external_ref": "RX-1"})()

    def record(self, *a, **k):
        pass


@pytest.mark.parametrize(
    "docs, resume, outcome",
    [
        ([], False, "ok"),
        ([{"dtype": "Order", "number": "PO7"}], True, "resume"),
        ([{"dtype": "Order", "number": "PO7"}], False, "review"),
        ([{"dtype": "Order", "number": "PO7"}, {"dtype": "Invoice", "number": "INV7"}], True, "review"),
    ],
)
def test_preflight_never_duplicates(monkeypatch, docs, resume, outcome):
    monkeypatch.setattr(order.db, "documents_with_ref", lambda ref, dtype=None: docs)
    ctx = _Ctx(resume)
    if outcome == "review":
        with pytest.raises(ManualReview):
            order.preflight(ctx)
    else:
        order.preflight(ctx)
        assert (ctx.resume_at == "invoice") is (outcome == "resume")


def test_close_grows_with_quantity():
    from decimal import Decimal as D
    from types import SimpleNamespace as NS

    from image2cash.validate import close

    one = [NS(qty=D(1))]
    assert close(D("10.01"), D("10.00"), one)
    assert not close(D("10.03"), D("10.00"), one)
    # RX-1003 live: 8 lines, 169 units in all; the booked net was 10 cents over the image
    rx1003 = [NS(qty=D(q)) for q in (48, 6, 10, 8, 40, 15, 30, 12)]
    assert close(D("1974.33"), D("1974.23"), rx1003)
    assert not close(D("1976.33"), D("1974.23"), rx1003)
    assert not close(None, D("1"), one)


def test_locate_lines_across_pages():
    from types import SimpleNamespace as NS

    from image2cash.steps.product import locate
    from image2cash.ui.grid import Row

    items = [NS(sku=s) for s in ("A", "B", "C")]
    row = lambda y, sku: Row(y, {"Item No.": sku}, {})  # noqa: E731
    top = {1: row(375, "A"), 2: row(404, "B")}
    lower = {2: row(300, "B"), 3: row(620, "C")}
    assert locate([(0, top), (2, lower)], items) == {1: (0, 375), 2: (0, 404), 3: (2, 620)}
    with pytest.raises(ManualReview, match="line 3 shows 'X'"):
        locate([(0, top), (2, {3: row(620, "X")})], items)
    with pytest.raises(ManualReview, match=r"lines \[3\] not found"):
        locate([(0, top)], items)


class _FakeTable:
    """n lines, `visible` per view, `per_notch` lines scrolled per wheel notch."""

    def __init__(self, n, visible, per_notch):
        self.n, self.visible, self.per_notch, self.offset, self.reads = n, visible, per_notch, 0, 0

    def read(self):
        self.reads += 1
        return {i: i for i in range(self.offset + 1, min(self.n, self.offset + self.visible) + 1)}

    def down(self, k):
        self.offset = max(0, min(self.n - self.visible, self.offset + k * self.per_notch))


@pytest.mark.parametrize(
    "n, visible, per_notch, pages",
    [
        (5, 7, 3, 1),  # fits: one read
        (8, 7, 3, 2),  # the RX-2003 case
        (40, 7, 3, 7),  # a long order: 2 notches = 6 lines per page
        (40, 5, 3, 13),  # 2 notches would skip lines: falls back to 1 notch
    ],
)
def test_walk_reads_every_line_once_per_page(n, visible, per_notch, pages):
    from image2cash.steps.product import walk

    t = _FakeTable(n, visible, per_notch)
    views = walk(t.read, t.down, n)
    assert set().union(*(set(rows) for _, rows in views)) == set(range(1, n + 1))
    assert len(views) == pages
    for clicks, rows in views:  # replaying a page's notches from the top shows the same lines
        replay = _FakeTable(n, visible, per_notch)
        replay.down(clicks)
        assert set(rows) <= set(replay.read())


def test_final_read_numbers_rows_past_an_unreadable_pos():
    from image2cash.steps.product import _numbered
    from image2cash.ui.grid import Row

    def row(y, pos, sku):
        return Row(y, {"Pos.": pos, "Item No.": sku}, {})

    rows = [Row(10, {"Qty.": "0.00"}, {}), row(30, "6", "A"), row(50, "7", "B"), row(70, "", "C"), row(90, "9", "D")]
    got = _numbered(type("G", (), {"rows": rows})(), bottom=200)
    assert {n: r.get("Item No.") for n, r in got.items()} == {6: "A", 7: "B", 8: "C", 9: "D"}


def test_catalogue_price_is_confirmed_not_overwritten():
    from decimal import Decimal as D

    from image2cash.models import Item
    from image2cash.steps.product import _check_master

    item = Item(
        pos=1, sku="OTC-GLV-N100", description="x", qty=D(40), unit_net=D("9.90"), vat_pct=D("6.25"), line_net=D("396")
    )
    row = {"vat_value": 0.0625, "vat_name": "VAT 6.25%", "price": 9.901176470588235}  # entered as gross 10.52
    _check_master(None, item, row)  # rounds to 9.90: same price
    with pytest.raises(ManualReview, match="catalogue net price 10.50"):
        _check_master(None, item, {**row, "price": 10.5})


def test_column_net_after_each_pass():
    from decimal import Decimal as D
    from types import SimpleNamespace as NS

    from image2cash.steps.product import column_net

    items = [NS(qty=D(48), unit_net=D("3.85"), discount_pct=D(0)), NS(qty=D(25), unit_net=D("7.80"), discount_pct=D(3))]
    assert column_net(items, False) == D("379.80")  # 184.80 + 195.00, discounts not yet typed
    assert column_net(items, True) == D("373.95")  # 184.80 + 189.15


def test_discount_is_checked_on_one_scale():
    from decimal import Decimal as D

    from image2cash.steps.order import discount_ok

    assert discount_ok(D("0.10"), D("10"))
    assert discount_ok(D("-0.05"), D("5"))  # sign as stored does not matter
    assert discount_ok(None, D("0"))
    assert not discount_ok(D("0.5"), D("0.5"))  # 50 % stored for a 0.5 % line: was accepted before
    assert not discount_ok(D("0.005"), D("0"))  # 0.5 % stored for a 0 % line


def test_saved_documents_must_name_the_chosen_debtor(monkeypatch):
    from types import SimpleNamespace as NS

    from image2cash.steps import debtor

    ctx = NS(contact_id=7)
    assert debtor.chosen_id(ctx) == 7
    resumed = NS(contact_id=None)  # --resume: no pick this run, the one exact match counts
    monkeypatch.setattr(debtor, "_exact", lambda c: [NS(id=3)])
    assert debtor.chosen_id(resumed) == 3
    monkeypatch.setattr(debtor, "_exact", lambda c: [NS(id=3), NS(id=4)])
    assert debtor.chosen_id(resumed) is None  # ambiguous: the check fails rather than guess

    rows = {58: [{"contact_id": 7}, {"contact_id": 7}], 59: [{"contact_id": 7}, {"contact_id": 8}], 60: []}
    monkeypatch.setattr(debtor.db, "document_receivers", lambda doc: rows[doc])
    assert debtor.is_debtor_of(ctx, 58)
    assert not debtor.is_debtor_of(ctx, 59)  # delivery address from another contact
    assert not debtor.is_debtor_of(ctx, 60)  # no receivers at all
