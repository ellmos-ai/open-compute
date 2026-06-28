"""Windows low-level capture via ctypes SetWindowsHookEx (zero extra deps).

A dedicated thread runs a message loop with WH_MOUSE_LL + WH_KEYBOARD_LL
hooks and pushes RawEvents into a thread-safe queue. poll() drains it.

NOTE: Real hook + message loop -- not CI-testable. Smoke test only checks the
available()/protocol contract.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes

from .base import RawEvent

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

_BTN_DOWN = {WM_LBUTTONDOWN: "left", WM_RBUTTONDOWN: "right", WM_MBUTTONDOWN: "middle"}
_BTN_UP = {WM_LBUTTONUP: "left", WM_RBUTTONUP: "right", WM_MBUTTONUP: "middle"}


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


_HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class WinApiCaptureBackend:
    def __init__(self):
        self._q: "queue.Queue[RawEvent]" = queue.Queue()
        self._paused = False
        self._t0 = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def available(self) -> bool:
        try:
            import platform
            return platform.system() == "Windows" and hasattr(ctypes, "windll")
        except Exception:
            return False

    def _emit(self, ev: RawEvent) -> None:
        if not self._paused:
            self._q.put(ev)

    def _run(self) -> None:
        user32 = ctypes.windll.user32

        def mouse_proc(nCode, wParam, lParam):
            if nCode >= 0:
                info = ctypes.cast(lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                t = time.monotonic() - self._t0
                x, y = info.pt.x, info.pt.y
                w = int(wParam)
                if w in _BTN_DOWN:
                    self._emit(RawEvent("mouse_down", t, x=x, y=y, button=_BTN_DOWN[w]))
                elif w in _BTN_UP:
                    self._emit(RawEvent("mouse_up", t, x=x, y=y, button=_BTN_UP[w]))
                elif w == WM_MOUSEWHEEL:
                    delta = ctypes.c_short(info.mouseData >> 16).value
                    self._emit(RawEvent("wheel", t, x=x, y=y, delta=delta))
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        def kbd_proc(nCode, wParam, lParam):
            if nCode >= 0 and int(wParam) in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                t = time.monotonic() - self._t0
                ch = self._vk_to_char(info.vkCode, info.scanCode)
                if ch:
                    self._emit(RawEvent("char", t, char=ch))
                else:
                    self._emit(RawEvent("key_down", t, key=self._vk_name(info.vkCode)))
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._mp = _HOOKPROC(mouse_proc)
        self._kp = _HOOKPROC(kbd_proc)
        mh = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mp, None, 0)
        kh = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kp, None, 0)
        msg = wintypes.MSG()
        while not self._stop.is_set():
            r = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
            if r:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.005)
        user32.UnhookWindowsHookEx(mh)
        user32.UnhookWindowsHookEx(kh)

    @staticmethod
    def _vk_name(vk: int) -> str:
        names = {0x0D: "enter", 0x09: "tab", 0x1B: "esc", 0x08: "backspace",
                 0x20: "space", 0x2E: "delete", 0x25: "left", 0x26: "up",
                 0x27: "right", 0x28: "down"}
        return names.get(vk, f"vk_{vk}")

    @staticmethod
    def _vk_to_char(vk: int, scan: int) -> str | None:
        # Printable range heuristic; full keyboard-layout translation is future work.
        user32 = ctypes.windll.user32
        buf = ctypes.create_unicode_buffer(8)
        state = (ctypes.c_byte * 256)()
        user32.GetKeyboardState(ctypes.byref(state))
        n = user32.ToUnicode(vk, scan, ctypes.byref(state), buf, 8, 0)
        if n == 1 and buf.value.isprintable():
            return buf.value
        return None

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def poll(self) -> list[RawEvent]:
        out: list[RawEvent] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out
