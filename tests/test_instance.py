"""Which open Fakturama a run drives (windows and the database port are stubbed)."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from image2cash.ui import core

TEST = "Fakturama - " + "D:" + "\\" + "Fakturama_Test_Working"
OTHER = "Fakturama - " + "F:" + "\\" + "test"


def win(title, pid):
    return NS(window_text=lambda: title, process_id=lambda: pid, class_name=lambda: "SWT_Window0")


@pytest.fixture
def desk(monkeypatch):
    def setup(windows, port_owner):
        monkeypatch.setattr(core, "_swt_windows", lambda: windows)
        monkeypatch.setattr(core, "current_workdir", lambda w=None: Path("D:/Fakturama_Test_Working"))
        monkeypatch.setattr(core, "_db_port_owner", lambda: (9001, port_owner))

    return setup


def test_the_test_fakturama_is_used_when_open(desk):
    desk([win(TEST, 10), win(OTHER, 20)], port_owner=10)
    assert core.target_instance() == 10


def test_the_one_open_fakturama_is_used_when_the_test_one_is_not(desk):
    # a friend's PC: his Fakturama, opened as usual, on his own working directory
    desk([win(OTHER, 20)], port_owner=20)
    assert core.target_instance() == 20


def test_two_other_fakturamas_are_never_guessed_between(desk):
    desk([win(OTHER, 20), win("Fakturama - " + "E:" + "\\" + "live", 30)], port_owner=20)
    with pytest.raises(core.AutomationError, match="not open"):
        core.target_instance()


def test_an_open_fakturama_that_does_not_serve_the_database_is_not_used(desk):
    desk([win(OTHER, 20)], port_owner=99)  # someone else holds port 9001
    with pytest.raises(core.AutomationError):
        core.target_instance()


def test_the_adopted_fakturama_is_announced_once(desk, caplog):
    core._ANNOUNCED.clear()
    desk([win(OTHER, 20)], port_owner=20)
    for _ in range(3):  # the app checks every second
        core.target_instance()
    assert sum("using the open" in r.message for r in caplog.records) == 1


def test_screenshots_appear_only_when_complete(tmp_path):
    from PIL import Image

    path = tmp_path / "01_shot.png"
    core._save_png(Image.new("RGB", (4, 4)), path)
    assert Image.open(path).size == (4, 4)
    assert [p.name for p in tmp_path.iterdir()] == ["01_shot.png"]  # no '.part' left behind


def test_the_floating_documents_panel_is_enlarged_by_its_edges():
    win = (-8, -8, 1928, 1040)
    docs = (1111, 115, 1897, 511)  # measured live: the pop-out over the editor
    (left, bottom) = core.float_resize(docs, win)
    (x1, y1), (x2, y2) = left
    assert x1 == 1108 and y1 == y2 and x2 == int(1928 - 0.6 * 1936) - 3  # left edge out to 60 %
    (x1, y1), (x2, y2) = bottom
    assert y1 == 512 and x1 == x2 and y2 == int(-8 + 0.85 * 1048) + 1  # bottom edge down to 85 %
    assert core.float_resize((100, 115, 1897, 950), win) == []  # already big: never shrinks


def test_a_fakturama_without_a_working_directory_is_never_used(desk):
    # its title says 'null' while it starts or waits in the first-run dialog (a friend's PC)
    desk([win("Fakturama - null", 20)], port_owner=20)
    with pytest.raises(core.AutomationError, match="no working directory yet"):
        core.target_instance()


def test_only_saved_editor_tabs_are_closed_before_a_new_order():
    tabs = ["Fakturama", "PO000041", "*New Order", "INV000040", "Lakeshore General Hospital"]
    state = {"active": None}

    def activate(match, nth=None):
        assert nth == 0  # always a real click: Ctrl+W goes to the part with keyboard focus
        state["active"] = next(t for t in tabs if match(t))

    def close():
        assert not state["active"].startswith("*")  # never an unsaved tab
        tabs.remove(state["active"])

    fake = NS(editor_tabs=lambda: list(tabs), activate_editor=activate, close_active_editor=close)
    closed = core.UI.close_saved_editors(fake)
    assert closed == ["PO000041", "INV000040", "Lakeshore General Hospital"]
    assert tabs == ["Fakturama", "*New Order"]  # the start page and the unsaved tab stay
