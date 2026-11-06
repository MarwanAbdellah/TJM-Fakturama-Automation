"""Pure helpers of the UI layer (no Fakturama needed)."""

from datetime import date

import pytest
from PIL import Image, ImageDraw

from image2cash.extract.ocr import Line
from image2cash.steps.selector import prefix_match
from image2cash.ui.datefield import _which_changed, parse_display
from image2cash.ui.grid import _fill_missing, separators, split_on_separators


def test_parse_display_formats():
    assert parse_display("14 Jul 2026") == date(2026, 7, 14)
    assert parse_display("23 Sept 2026") == date(2026, 9, 23)  # en_GB 'Sept'
    assert parse_display("Jul 14, 2026") == date(2026, 7, 14)
    assert parse_display("14.07.2026") == date(2026, 7, 14)
    assert parse_display("21 Sept 7") == date(7, 9, 21)  # mid-edit states must parse
    assert parse_display("<choose date>") is None


def test_which_segment_changed():
    assert _which_changed(date(2026, 7, 14), date(2027, 7, 14)) == "year"
    assert _which_changed(date(2026, 7, 14), date(2026, 8, 14)) == "month"
    assert _which_changed(date(2026, 7, 14), date(2026, 7, 15)) == "day"


def test_split_joined_cells():
    l = Line("Northstar Office ... 10117", 1.0, 1000, 700, 1180, 712)
    parts = split_on_separators(l, [1130])
    assert [p.text for p in parts] == ["Northstar Office ...", "10117"]
    assert parts[1].x0 > 1130


def test_split_leaves_single_cells_alone():
    l = Line("CHR-ERG-01", 1.0, 100, 0, 180, 10)
    assert split_on_separators(l, [50, 200]) == [l]


def test_fill_missing_headers_by_position():
    expected = ["Pos.", "Qty.", "Item No.", "Picture", "Name", "Description", "VAT", "U.Price"]
    seen = ["Pos.", None, "Item No.", "Picture", None, "Description", None, "U.Price"]
    assert _fill_missing(seen, expected) == expected


def test_fill_missing_refuses_on_a_tie():
    assert _fill_missing(["A", None, None, "D"], ["A", "B", "D"]) == ["A", None, None, "D"]


def test_fill_missing_outvotes_a_partial_caption():
    # live case: 'U.Price' read as 'Price', most captions lost, blank columns at the end
    expected = ["Pos.", "Qty.", "Item No.", "Picture", "Name", "Description", "VAT", "U.Price", "Discount", "Price"]
    seen = ["Pos.", None, None, None, None, None, None, "Price", "Discount", None, None, None]
    assert _fill_missing(seen, expected) == expected + [None, None]


def test_separators_ignore_text_but_find_grid_lines():
    img = Image.new("RGB", (120, 24), (192, 192, 192))
    d = ImageDraw.Draw(img)
    d.line((40, 0, 40, 23), fill=(255, 255, 255))  # a column line, lighter than the header
    d.line((80, 0, 80, 23), fill=(100, 100, 100))  # one darker
    d.text((5, 6), "Qty.", fill=(255, 255, 255))  # header text does not span the band
    assert separators(img, 0, 23) == [40, 80]


def test_prefix_match_for_truncated_cells():
    assert prefix_match("Northstar Offic...", "Northstar Office GmbH")
    assert prefix_match("Northstar Office GmbH", "Northstar Office GmbH")
    assert not prefix_match("Northstar Offic...", "Southstar Office GmbH")
    assert not prefix_match("", "x")


def test_listener_pid_reads_netstat():
    from image2cash.ui.core import listener_pid

    out = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:8501           0.0.0.0:0              LISTENING       69372
  TCP    0.0.0.0:9001           0.0.0.0:0              LISTENING       176348
  TCP    127.0.0.1:59001        0.0.0.0:0              LISTENING       1234
  TCP    127.0.0.1:9001         127.0.0.1:50000        ESTABLISHED     999
"""
    assert listener_pid(out, 9001) == 176348
    assert listener_pid(out, 59001) == 1234
    assert listener_pid(out, 9002) is None


@pytest.mark.parametrize(
    "shown, want",
    [
        ("20 Oct 2026", "7 Nov 2026"),
        ("06 Oct 2026", "07 Nov 2026"),  # keep the field's zero padding
        ("Oct 20, 2026", "Nov 7, 2026"),
        ("20.10.2026", "07.11.2026"),
        ("2026-10-20", "2026-11-07"),
        ("garbage", None),  # unknown format: fall back to stepping
    ],
)
def test_date_is_pasted_in_the_fields_own_format(shown, want):
    from datetime import date

    from image2cash.ui.datefield import format_like, parse_display

    got = format_like(shown, date(2026, 11, 7))
    assert got == want
    assert got is None or parse_display(got) == date(2026, 11, 7)


def test_closed_dialog_is_seen_as_gone():
    import win32gui

    from image2cash.steps.selector import _gone

    class Dialog:
        def __init__(self, handle):
            self.handle = handle

    assert _gone(Dialog(0))  # no window
    assert _gone(Dialog(0x7FFFFFF0))  # a handle that is not (or no longer) a window
    assert not _gone(Dialog(win32gui.GetDesktopWindow()))  # an open, visible window


def test_vat_percent_is_typed_with_a_dot_whatever_the_price_fields_use():
    from decimal import Decimal

    from image2cash.ui.core import UI
    from image2cash.validate import parse_amount

    ui = UI.__new__(UI)  # no Fakturama needed for formatting
    ui.decimal_sep = ","  # learned from a price field
    assert ui.amount_text(Decimal("6.25"), sep=".") == "6.25"
    assert parse_amount("625%") != Decimal("6.25")  # the 2026-11-05 read-back: caught, so ',' is retried
    assert parse_amount("6.25%") == Decimal("6.25")


def test_qty_joined_to_the_sku_is_given_back():
    from image2cash.steps.product import _unmerge
    from image2cash.ui.grid import Row

    row = _unmerge(Row(0, {"Pos.": "1", "Item No.": "120.00 NDC-0409-4888", "Discount": "0.00 %"}, {}))
    assert row.get("Qty.") == "120.00" and row.get("Item No.") == "NDC-0409-4888"
    kept = _unmerge(Row(0, {"Qty.": "2.00", "Item No.": "CHR-ERG-01"}, {}))
    assert kept.cells == {"Qty.": "2.00", "Item No.": "CHR-ERG-01"}


def test_back_to_order_skips_another_runs_new_order_tab(monkeypatch):
    from types import SimpleNamespace as NS

    from image2cash.steps.context import Ctx
    from image2cash.ui import core

    refs = {0: "LTR-0907-B", 1: "LTR-0907-B"}  # a stopped try of the same order left tab 0 open
    state = {"tab": None}

    def activate(match, nth=None):
        state["tab"] = nth

    ui = NS(
        _editor_folder=lambda: NS(element_info=NS(name="Harbor Point")),
        active_editor=lambda: "*New Order",
        editor_tabs=lambda: ["Fakturama", "*New Order", "Harbor Point", "*New Order"],
        activate_editor=activate,
        editor_pane=lambda: NS(handle=state["tab"]),
        labelled=lambda *a, **k: "ref field",
    )
    monkeypatch.setattr(core, "read", lambda ctrl: refs[state["tab"]])
    ctx = Ctx(ui=ui, order=NS(external_ref="LTR-0907-B"), run_dir=None)
    ctx._order_pane = 1  # remembered when this run opened its Order
    ctx.back_to_order()
    assert state["tab"] == 1 and ctx._order_pane == 1


def test_rows_are_numbered_when_pos_is_misread():
    from types import SimpleNamespace as NS

    from image2cash.steps.product import _numbered
    from image2cash.ui.grid import Row

    # the 14:45 run: Pos. read '3' on line 1 (from Qty 3.00) and nothing on line 2
    rows = [Row(10, {"Pos.": "3", "Item No.": "NDC-0641-6078"}, {}), Row(30, {"Item No.": "OTC-SYR-10ML"}, {})]
    assert list(_numbered(NS(rows=rows), bottom=100, total=2)) == [1, 2]
    assert _numbered(NS(rows=rows[:1]), bottom=100, total=2) == {}  # one row of two: cannot tell which
    paged = [Row(10, {"Pos.": "8", "Item No.": "A"}, {}), Row(30, {"Pos.": "9", "Item No.": "B"}, {})]
    assert list(_numbered(NS(rows=paged), bottom=100, total=9)) == [8, 9]
