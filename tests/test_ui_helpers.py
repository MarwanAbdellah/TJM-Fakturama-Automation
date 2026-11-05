"""Pure helpers of the UI layer (no Fakturama needed)."""

from datetime import date

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
