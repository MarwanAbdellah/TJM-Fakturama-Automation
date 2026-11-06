"""Locate, act on and read Fakturama controls without fixed coordinates.

Three grounding strategies, in order of preference (see docs/design.md §4):

1. Named UIA controls (toolbar tooltips, nav links, labelled edits).
2. Label anchoring: the editable control nearest to a ``Static`` label, or the
   N-th ``Image`` sibling of a label (unnamed SWT icon buttons).
3. OCR of custom-drawn grids (NatTable); see ``grid.py``.

Every rectangle used for a click comes from the live UIA tree in this run.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from image2cash import config


def main_title(workdir: Path | None) -> str:
    """Title regex of the Fakturama window for ``workdir``. Windows paths ignore case, and
    'F:\\test' and 'f:\\test\\' are the same folder. No working directory known: match nothing,
    so target_instance says what to set."""
    if not workdir:
        return r"(?!)"
    return r"(?i)Fakturama - " + re.escape(str(workdir).rstrip("\\/")) + r"[\\/]?$"


log = logging.getLogger("image2cash.ui")


class AutomationError(RuntimeError):
    """The UI did not reach the state we expected."""


class ManualReview(RuntimeError):
    """Data or UI is ambiguous; a human has to decide."""


def paste(text: str) -> None:
    """Select all in the focused field and paste ``text`` (one keystroke pair, not typing)."""
    import win32clipboard as cb
    from pywinauto.keyboard import send_keys

    cb.OpenClipboard()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardText(text, cb.CF_UNICODETEXT)
    finally:
        cb.CloseClipboard()
    send_keys("^a^v")


def wait_for(predicate: Callable[[], object], timeout: float = 10, interval: float = 0.1, what: str = ""):
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:  # control not there yet
            last_exc = exc
        time.sleep(interval)
    log.debug("timed out after %ss waiting for %s (%s)", timeout, what or predicate, last_exc or "")
    raise AutomationError(f"timed out waiting for {what or predicate}: {last_exc or ''}")


def listener_pid(netstat: str, port: int) -> int | None:
    """PID listening on ``port`` in `netstat -ano` output."""
    for line in netstat.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
            return int(parts[4])
    return None


def _db_port_owner() -> tuple[int, int | None]:
    port = int(re.search(r":(\d+)/", config.db_url())[1])
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
    return port, listener_pid(out, port)


def start_fakturama(timeout: float = 180, first_run_timeout: float = 600) -> int:
    """The configured Fakturama, started on the test workspace if it is closed. Returns its pid
    once its window is up and it serves the database. Never starts a second instance while
    another program (e.g. the live Fakturama) holds the database port."""
    try:
        return target_instance()
    except AutomationError:
        pass
    port, owner = _db_port_owner()
    if owner is not None:
        raise AutomationError(
            f"port {port} is held by another program (pid {owner}), probably another Fakturama. "
            f"Close it, then start again."
        )
    home = config.fakturama_home()
    if home is None:
        raise AutomationError(
            "Fakturama.exe was not found (not running, not on PATH, not in the registry or the usual "
            "install folders). Set FAKTURAMA_HOME in .env to the folder that holds Fakturama.exe."
        )
    # A new workspace: Fakturama asks for a working directory first; give the user time to answer.
    first_run = config.workdir() is None
    log.info("starting %s on %s%s", home / config.EXE, config.WORKSPACE, " (first run)" if first_run else "")
    subprocess.Popen(
        [str(home / config.EXE), "-data", str(config.WORKSPACE)],
        cwd=str(home),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    what = "Fakturama to start" + (" (choose its working directory in Fakturama's dialog)" if first_run else "")
    pid = wait_for(target_instance, first_run_timeout if first_run else timeout, interval=1, what=what)
    time.sleep(5)  # the window and the DB come up before the workbench finishes loading its views
    return pid


def target_instance() -> int:
    """PID of the Fakturama window for config.workdir(), and only if that same process serves
    the database we read. Two Fakturama instances (test + live) cannot both own port 9001,
    and reading one while driving the other would verify the wrong data."""
    from pywinauto import Desktop

    windows = [w for w in Desktop(backend="win32").windows(visible_only=False) if w.class_name() == "SWT_Window0"]
    wd = config.workdir()
    pids = {w.process_id() for w in windows if re.match(main_title(wd), w.window_text())}
    if not pids:
        exe = config.fakturama_home() / config.EXE if config.fakturama_home() else config.EXE
        others = sorted({t for w in windows if (t := w.window_text()).startswith("Fakturama")})
        raise AutomationError(
            f"Fakturama for the working directory {wd or '(unknown)'} (workspace "
            f"{config.WORKSPACE}) is not open"
            + (f"; open instead: {others}" if others else "")
            + f'. Start it with: "{exe}" -data "{config.WORKSPACE}", or set FAKTURAMA_WORKSPACE in .env '
            "to the workspace that Fakturama window uses."
        )
    port, owner = _db_port_owner()
    if owner not in pids:
        raise AutomationError(
            f"The database on port {port} belongs to another program (pid {owner}), not to Fakturama for "
            f"{wd}. Close the other Fakturama, then restart the one for {wd}."
        )
    return owner


# One worker writes screenshots in order; the interpreter waits for it at exit, so none are lost.
UIA_MAXIMIZED = 1  # UIA WindowVisualState: 0 normal, 1 maximized, 2 minimized (not Win32's SW_*)
_SAVER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shots")


@dataclass
class UI:
    """Handle on the running Fakturama main window (UIA + win32 views)."""

    evidence_dir: Path | None = None

    def __post_init__(self):
        from pywinauto.application import Application

        pid = target_instance()
        # Maximize first. A minimized window is invisible to UIA (win32 still sees it), and in a
        # small window the Order's fields lie off-screen, so clicks and typing miss them.
        w32 = Application(backend="win32").connect(process=pid).window(title_re=main_title(config.workdir()))
        if not w32.is_maximized():
            w32.maximize()
            time.sleep(1)
        self.uia_app = Application(backend="uia").connect(process=pid)
        self.main = self.uia_app.window(title_re=main_title(config.workdir())).wrapper_object()
        self.w32_app = Application(backend="win32").connect(process=self.main.process_id())
        self.w32 = self.w32_app.window(handle=self.main.handle)
        self._shot = 0
        self._folder = None  # the editor tab folder, cached (finding it walks the whole window)

    # ---- basic tree access -------------------------------------------------
    def focus(self) -> None:
        try:
            if self.main.get_show_state() != UIA_MAXIMIZED:  # resized or minimized mid-run
                log.debug("maximize Fakturama (was resized or minimized)")
                self.main.maximize()
                time.sleep(0.5)
            self.main.set_focus()
        except Exception:
            pass

    def find(self, name: str, control_type: str, root=None, timeout: float = 10, index: int = 0):
        """Visible control by exact UIA name and type."""
        root = root or self.main

        def probe():
            hits = [
                c for c in root.descendants(control_type=control_type) if c.element_info.name == name and _visible(c)
            ]
            return hits[index] if len(hits) > index else None

        return wait_for(probe, timeout, what=f"{control_type} {name!r}")

    def dialog(self, title: str, timeout: float = 10):
        """A modal dialog (child top-level window of Fakturama) by title."""
        log.debug("wait for dialog %r", title)

        def probe():
            for c in self.main.children():
                if c.element_info.control_type == "Window" and c.window_text() == title:
                    return c
            from pywinauto import Desktop

            for w in Desktop(backend="uia").windows(process=self.main.process_id()):
                if w.window_text() == title:
                    return w
            return None

        return wait_for(probe, timeout, what=f"dialog {title!r}")

    # ---- label anchoring ---------------------------------------------------
    def labelled(self, label: str, control_type: str = "Edit", root=None, nth_label: int = 0, timeout: float = 10):
        """The ``control_type`` control nearest to the right of / below a Static label.

        Prefers a control on the same row (vertical centres within half a row),
        to the right of the label; else the closest one below. Searches the
        label's parent, then grandparent, so SWT composite nesting is handled.
        """
        root = root or self.main

        def probe():
            labels = [
                c
                for c in root.descendants(control_type="Text")
                if c.element_info.name == label and c.element_info.class_name == "Static" and _visible(c)
            ]
            if len(labels) <= nth_label:
                return None
            lab = labels[nth_label]
            lr = lab.rectangle()
            lcy = (lr.top + lr.bottom) / 2
            scope = lab.parent()
            for _ in range(3):
                cands = [c for c in scope.descendants(control_type=control_type) if _visible(c)]
                same_row = [
                    c
                    for c in cands
                    if abs((c.rectangle().top + c.rectangle().bottom) / 2 - lcy) <= max(12, lr.height())
                    and c.rectangle().left >= lr.right - 4
                ]
                if same_row:
                    return min(same_row, key=lambda c: c.rectangle().left - lr.right)
                below = [c for c in cands if c.rectangle().top >= lr.bottom - 2 and c.rectangle().left <= lr.right]
                if below:
                    return min(below, key=lambda c: c.rectangle().top - lr.bottom)
                scope = scope.parent()
            return None

        return wait_for(probe, timeout, what=f"{control_type} labelled {label!r}")

    def row_controls(self, label: str, control_type: str = "Edit", root=None) -> list:
        """All ``control_type`` controls on the label's row, left to right
        (e.g. 'First Name Last Name' -> [first, last]; 'ZIP - City' -> [zip, city])."""
        first = self.labelled(label, control_type, root=root)
        fr = first.rectangle()
        scope = first.parent().parent()
        same = [
            c
            for c in scope.descendants(control_type=control_type)
            if _visible(c) and abs(c.rectangle().top - fr.top) <= 4 and c.rectangle().left >= fr.left
        ]
        same.sort(key=lambda c: c.rectangle().left)
        return same

    def combo_with_option(self, option: str, root=None):
        """A native ComboBox identified by what it offers (for unlabelled or
        untranslated-label combos such as the payment-code dropdown)."""
        root = root or self.main
        for c in root.descendants(control_type="ComboBox"):
            if not _visible(c) or not c.handle:
                continue
            try:
                if any(o.strip() == option for o in self.combo_items(c)):
                    return c
            except Exception:
                continue
        raise AutomationError(f"no combo offering {option!r}")

    def icon_beside(self, label: str, nth: int, root=None):
        """N-th (top to bottom) unnamed Image sibling of a label: SWT icon buttons."""
        root = root or self.main
        lab = self.find(label, "Text", root=root)
        imgs = [c for c in lab.parent().children() if c.element_info.control_type == "Image" and _visible(c)]
        imgs.sort(key=lambda c: (c.rectangle().top, c.rectangle().left))
        if len(imgs) <= nth:
            raise AutomationError(f"icon #{nth} beside {label!r} not found ({len(imgs)} icons)")
        return imgs[nth]

    # ---- actions -----------------------------------------------------------
    def click(self, ctrl, double: bool = False) -> None:
        log.debug(
            "%s %s %r", "double-click" if double else "click", ctrl.element_info.control_type, ctrl.element_info.name
        )
        self.focus()
        ctrl.click_input(double=double)

    def press(self, name: str, control_type: str = "Button", root=None) -> None:
        ctrl = self.find(name, control_type, root=root)
        log.debug("press %s %r", control_type, name)
        try:
            ctrl.invoke()
        except Exception:
            self.click(ctrl)

    def set_text(self, ctrl, text: str, verify: bool = True, leave: str = "tab", check=None) -> str:
        """Replace a field's content by keyboard and read it back.

        ``leave``: 'tab' (single-line fields), 'enter', or 'none' (multi-line
        fields, where Tab would insert a tab character).
        ``check``: optional predicate on the read-back value; default is an
        exact (whitespace-normalized) comparison with ``text``.
        """
        from pywinauto.keyboard import send_keys

        log.debug("type %r into %r", text, ctrl.element_info.name)
        self.focus()
        ctrl.click_input()
        send_keys("^a{BACKSPACE}", pause=0.02)
        if text:
            send_keys(_escape(text), with_spaces=True, pause=0.01)
        if leave == "tab":
            send_keys("{TAB}")
        elif leave == "enter":
            send_keys("{ENTER}")
        time.sleep(0.3)
        value = read(ctrl)
        if verify:
            ok = check(value) if check else " ".join(value.split()) == " ".join(text.split())
            if not ok:
                raise AutomationError(f"field read back {value!r}, expected {text!r}")
        return value

    def set_amount(self, ctrl, value, leave: str = "tab", sep: str | None = None) -> str:
        """Type a money/number value in the field's own locale (or ``sep``) and verify it."""
        from decimal import Decimal

        from image2cash.validate import parse_amount

        self.learn_locale(read(ctrl))  # the field's own display tells its decimal separator
        text = self.amount_text(Decimal(value), sep=sep)
        return self.set_text(ctrl, text, leave=leave, check=lambda v: parse_amount(v) == Decimal(value))

    decimal_sep: str = "."

    def learn_locale(self, sample_display: str) -> str:
        """Learn the decimal separator from a displayed amount such as
        '0.00 $', '0,00 EUR' or the Persian '۰٫۰۰'. Typing '.' into a
        ps/AF field is read as a thousands separator, so this matters."""
        import re

        m = re.search(r"([.,٫])[0-9۰-۹٠-٩]{2}(?![0-9۰-۹٠-٩])", sample_display)
        if m:
            self.decimal_sep = m.group(1)
        return self.decimal_sep

    def amount_text(self, value, sep: str | None = None) -> str:
        """Format for typing. Form fields parse with the UI locale (learned);
        NatTable grid cells were calibrated to take '.' regardless of locale."""
        from decimal import Decimal

        v = Decimal(value)
        text = format(v.quantize(Decimal("0.01")), "f") if v != v.to_integral() else format(v.to_integral(), "f")
        return text.replace(".", self.decimal_sep if sep is None else sep)

    def combo_select(self, uia_combo, text: str) -> None:
        """Select a native ComboBox entry exactly via the win32 view of the same HWND.

        Entries are compared after stripping (Fakturama pads some with spaces).
        """
        from pywinauto.controls.win32_controls import ComboBoxWrapper

        hwnd = uia_combo.handle
        if not hwnd:
            raise AutomationError("combo has no native handle")
        combo = ComboBoxWrapper(hwnd)
        items = combo.item_texts()
        stripped = [i.strip() for i in items]
        if text.strip() not in stripped:
            raise ManualReview(f"{text!r} not in combo options {items}")
        log.debug("select %r in combo %r", text.strip(), uia_combo.element_info.name)
        combo.select(stripped.index(text.strip()))
        # SWT listens for CBN_SELCHANGE which select() sends; confirm.
        wait_for(lambda: (combo.selected_text() or "").strip() == text.strip(), 3, what=f"combo -> {text}")

    def combo_value(self, uia_combo) -> str:
        from pywinauto.controls.win32_controls import ComboBoxWrapper

        return ComboBoxWrapper(uia_combo.handle).selected_text() or ""

    def combo_items(self, uia_combo) -> list[str]:
        from pywinauto.controls.win32_controls import ComboBoxWrapper

        return ComboBoxWrapper(uia_combo.handle).item_texts()

    def save(self) -> None:
        """Toolbar Save (brief: 'click the toolbar Save control once')."""
        log.info("save %r", self.active_editor())
        self.press("Save the current contents")
        time.sleep(1.2)

    # ---- editors (tabs) ----------------------------------------------------
    def editor_tabs(self) -> list[str]:
        """Titles of the editor tabs in the top folder (left to right)."""
        folder = self._editor_folder()
        items = [c for c in folder.children() if c.element_info.control_type == "TabItem"]
        items.sort(key=lambda c: c.rectangle().left)
        return [c.element_info.name for c in items]

    def _editor_folder(self):
        if self._folder is not None and _visible(self._folder):
            return self._folder
        self._folder = self._find_editor_folder()
        return self._folder

    def _find_editor_folder(self):
        tabs = [
            c
            for c in self.main.descendants(control_type="Tab")
            if c.element_info.class_name == "SWT_Window0" and _visible(c)
        ]
        if not tabs:
            raise AutomationError("no editor tab folder")
        # The editor area always holds the 'Fakturama' start page; with the editor area
        # maximized, views (Documents) float over it at the same top edge.
        for t in tabs:
            if any(
                c.element_info.name == "Fakturama" for c in t.children() if c.element_info.control_type == "TabItem"
            ):
                return t
        return min(tabs, key=lambda c: c.rectangle().top)

    def active_editor(self) -> str:
        return self._editor_folder().element_info.name

    def activate_editor(self, title: str | Callable[[str], bool], nth: int | None = None) -> str:
        """Bring an editor tab to the front. ``nth`` picks one of several tabs with a
        matching title (left to right); several '*New Order' tabs can be open."""
        match = title if callable(title) else (lambda t: t.lstrip("*") == title)
        folder = self._editor_folder()
        if nth is None and match(folder.element_info.name):  # already active: a click costs ~1.5 s
            self.focus()  # the click also gave Fakturama the keyboard (Ctrl+W, typing)
            return folder.element_info.name
        log.debug("activate editor (open: %s)", self.editor_tabs())
        items = sorted(
            (c for c in folder.children() if c.element_info.control_type == "TabItem" and match(c.element_info.name)),
            key=lambda c: c.rectangle().left,
        )
        if len(items) > (nth or 0):
            c = items[nth or 0]
            self.click(c)
            wait_for(lambda: match(self._editor_folder().element_info.name), 5, what=f"editor {title}")
            return c.element_info.name
        raise AutomationError(f"editor tab {title!r} not open; open: {self.editor_tabs()}")

    def close_active_editor(self) -> None:
        """Close the active editor tab once it is saved. Never closes an unsaved
        ('*') tab, so typed input is never discarded."""
        from pywinauto.keyboard import send_keys

        wait_for(lambda: not self.active_editor().startswith("*"), 3, what="editor saved before closing")
        title, n = self.active_editor(), len(self.editor_tabs())
        self.focus()
        send_keys("^w")
        wait_for(lambda: len(self.editor_tabs()) < n, 5, what=f"editor {title!r} closed")

    def view_panel(self, name: str):
        """A non-editor view folder (Documents, Products, VATs, ...) showing ``name``."""
        editor = self._editor_folder()
        tabs = [
            c
            for c in self.main.descendants(control_type="Tab")
            if _visible(c) and c.element_info.name == name and c.handle != editor.handle
        ]
        if not tabs:
            raise AutomationError(f"view {name!r} is not shown")
        return tabs[0]

    def editor_pane(self):
        folder = self._editor_folder()
        panes = [c for c in folder.children() if c.element_info.control_type == "Pane"]
        if not panes:
            raise AutomationError("active editor has no pane")
        return panes[0]

    def nav(self, name: str) -> None:
        """Click a link in the left navigation view (Data / New / Miscellaneous)."""
        log.debug("navigate to %r", name)
        nav = self.find("Navigation View", "Pane")
        self.click(self.find(name, "Text", root=nav))
        time.sleep(1)

    # ---- evidence ----------------------------------------------------------
    def screenshot(self, name: str, boxes: list[tuple[object, str]] | None = None) -> Path | None:
        """Save a screenshot of the main window, outlining touched controls."""
        if self.evidence_dir is None:
            return None
        from PIL import ImageDraw, ImageGrab

        self._shot += 1
        r = self.main.rectangle()
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
        draw = ImageDraw.Draw(img)
        for ctrl, label in boxes or []:
            try:
                cr = ctrl.rectangle() if hasattr(ctrl, "rectangle") else ctrl
                box = (cr.left - r.left, cr.top - r.top, cr.right - r.left, cr.bottom - r.top)
            except Exception:
                continue
            draw.rectangle(box, outline=(230, 30, 30), width=3)
            draw.rectangle((box[0], box[1] - 16, box[0] + 7 * len(label) + 6, box[1]), fill=(230, 30, 30))
            draw.text((box[0] + 3, box[1] - 15), label, fill=(255, 255, 255))
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / f"{self._shot:02d}_{name}.png"
        _SAVER.submit(img.save, path)  # PNG encoding is the slow part; the run need not wait for it
        return path


def read(ctrl) -> str:
    """Current text of an edit/combo via UIA ValuePattern, falling back to window text."""
    try:
        value = ctrl.iface_value.CurrentValue
        if value is not None:
            return str(value)
    except Exception:
        pass
    try:
        return ctrl.window_text()
    except Exception:
        return ""


def _visible(ctrl) -> bool:
    try:
        r = ctrl.rectangle()
        return r.width() > 0 and r.height() > 0 and not ctrl.element_info.element.CurrentIsOffscreen
    except Exception:
        return False


def _escape(text: str) -> str:
    """Escape pywinauto send_keys metacharacters."""
    out = []
    for ch in text:
        out.append(f"{{{ch}}}" if ch in "+^%~(){}[]" else ch)
    return "".join(out)
