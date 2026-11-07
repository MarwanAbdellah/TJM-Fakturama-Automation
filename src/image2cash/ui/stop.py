"""Emergency stop: one physical Esc while a run drives Fakturama stops it at its next action.

A Windows low-level keyboard hook (no extra library) watches the keyboard from a background
thread. Keys the automation sends itself (pywinauto uses SendInput) carry the INJECTED flag and
are ignored, so its own Esc, which closes the address-type popup, never stops the run. The Esc
still reaches Fakturama; the run only notices it and stops before the next click or keystroke.
"""

from __future__ import annotations

import ctypes
import logging
import threading

log = logging.getLogger("image2cash.ui")

VK_ESCAPE = 0x1B
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_INJECTED = 0x10
WH_KEYBOARD_LL = 13

STOP = threading.Event()
_started = False


class UserStop(RuntimeError):
    """The user pressed Esc to stop the run."""


def is_user_stop(vk: int, flags: int, message: int) -> bool:
    """A real Esc key-down, not one the automation injected."""
    return vk == VK_ESCAPE and message in (WM_KEYDOWN, WM_SYSKEYDOWN) and not flags & LLKHF_INJECTED


def check() -> None:
    """Raise UserStop if Esc was pressed. Called before every UI action and in every wait."""
    if STOP.is_set():
        raise UserStop("stopped by you (Esc)")


def start() -> None:
    """Start listening, once per process. Never fails a run: without the hook, there is no stop key."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_listen, name="esc-stop", daemon=True).start()


def _listen() -> None:
    from ctypes import wintypes

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        hookproc = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = lresult
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        @hookproc
        def on_key(code, wparam, lparam):
            if code == 0:
                key = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if is_user_stop(key.vkCode, key.flags, wparam) and not STOP.is_set():
                    log.warning("Esc pressed: the run stops at its next action")
                    STOP.set()
            return user32.CallNextHookEx(None, code, wparam, lparam)

        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, on_key, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            log.debug("Esc stop unavailable: SetWindowsHookExW failed (%s)", ctypes.get_last_error())
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:  # the hook needs a message loop
            pass
    except Exception as exc:  # noqa: BLE001 - the stop key is a convenience, never a reason to fail
        log.debug("Esc stop unavailable: %s", exc)
