"""On-screen signaling for active screen functions (compute/companion modes).

Befund 2026-07-31: :class:`open_compute.cooperative.OwnershipIndicator` shipped
only as a Protocol plus ``NullOwnershipIndicator`` — intentionally no renderer.
This module is the renderer side:

* a colored, glowing border overlay around the virtual screen (Windows,
  click-through, topmost) with per-mode colors — red while the model may act
  (CONTROL), blue while it only watches (OBSERVE), plus a neon ring around the
  mouse cursor and a small status strip,
* an abort channel: when the human aborts compute mode, a short reason is
  captured (console or topmost Tk input box) and handed back to the model.

Layering mirrors ``cooperative.py``: every side effect sits behind an injected
protocol (:class:`OverlayRenderer`, :class:`AbortChannel`), so headless tests
run everywhere and the Win32 implementation stays separately gated. The Win32
overlay is constructed lazily and raises on non-Windows platforms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Callable, Protocol

from .session import SessionMode


# Mode -> (label, (r, g, b)).
# Rot = Modell darf handeln (CONTROL). Blau = Modell beobachtet nur (OBSERVE) —
# die andere Farbe fuer "nur zusehen / gemeinsam etwas anschauen".
MODE_SIGNALS: dict[SessionMode, tuple[str, tuple[int, int, int]]] = {
    SessionMode.OBSERVE: ("observe - Modell schaut zu", (0, 150, 255)),
    SessionMode.COMPANION: ("companion - gemeinsam", (0, 200, 120)),
    SessionMode.ASSIST: ("assist", (255, 200, 0)),
    SessionMode.HANDOFF: ("handoff - Uebergabe", (255, 130, 0)),
    SessionMode.CONTROL: ("CONTROL - Modell steuert", (255, 40, 60)),
    SessionMode.PAUSED: ("paused", (150, 150, 150)),
}


def signal_for_mode(mode: SessionMode | str) -> tuple[str, tuple[int, int, int]]:
    """Return the (label, rgb) signal for a session mode."""

    return MODE_SIGNALS[SessionMode(mode)]


# --- Hotkey parsing (headless-testable) --------------------------------------

_MOD_KEYS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
}
_VK_KEYS = {
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "pause": 0x13,
}
for _fnum in range(1, 13):
    _VK_KEYS[f"f{_fnum}"] = 0x6F + _fnum


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse ``ctrl+alt+esc`` / ``f9`` / ``shift+a`` into (modifiers, vk)."""

    parts = [p.strip().casefold() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("hotkey spec must not be empty")
    mods = 0
    key: int | None = None
    for part in parts:
        if part in _MOD_KEYS:
            mods |= _MOD_KEYS[part]
            continue
        if key is not None:
            raise ValueError(f"hotkey has more than one key: {spec!r}")
        if part in _VK_KEYS:
            key = _VK_KEYS[part]
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        else:
            raise ValueError(f"unknown hotkey key: {part!r}")
    if key is None:
        raise ValueError(f"hotkey needs a non-modifier key: {spec!r}")
    return mods, key


# --- Signal configuration (JSON file, defaults = built-in palette) -----------

def _validate_color(value: object) -> tuple[int, int, int]:
    parts = tuple(int(c) for c in value)  # type: ignore[union-attr]
    if len(parts) != 3 or any(not 0 <= c <= 255 for c in parts):
        raise ValueError(f"color must be three 0..255 ints, got {value!r}")
    return parts  # type: ignore[return-value]


@dataclass
class SignalModeConfig:
    """Per-mode signal settings.

    ``border`` and ``cursor`` are independently toggleable — the use case
    "cursor highlight only when the model actively drives, and no screen
    border then" is simply ``control: {border: false, cursor: true}``.
    A mode with ``enabled: false`` renders nothing at all.
    """

    enabled: bool = True
    color: tuple[int, int, int] = (255, 255, 255)
    label: str = ""
    border: bool = True
    cursor: bool = True

    def __post_init__(self) -> None:
        self.color = _validate_color(self.color)


@dataclass
class SignalConfig:
    """Signal overlay configuration.

    Defaults are exactly the built-in palette (:data:`MODE_SIGNALS`) with
    border + cursor on for every mode. A JSON file only needs to carry the
    overrides — unknown keys are ignored, unspecified modes keep defaults.
    """

    modes: dict[SessionMode, SignalModeConfig] = field(default_factory=dict)
    thickness: int = 6
    abort_hotkey: str | None = None

    def __post_init__(self) -> None:
        merged = self.default_modes()
        merged.update({SessionMode(k): v for k, v in self.modes.items()})
        self.modes = merged
        if not 2 <= self.thickness <= 40:
            raise ValueError("thickness must be in 2..40")
        if self.abort_hotkey is not None:
            parse_hotkey(self.abort_hotkey)  # validate eagerly

    @staticmethod
    def default_modes() -> dict[SessionMode, SignalModeConfig]:
        return {
            mode: SignalModeConfig(color=color, label=label)
            for mode, (label, color) in MODE_SIGNALS.items()
        }

    def for_mode(self, mode: SessionMode | str) -> SignalModeConfig:
        return self.modes[SessionMode(mode)]

    @classmethod
    def from_dict(cls, data: dict) -> "SignalConfig":
        if not isinstance(data, dict):
            raise ValueError("signal config must be a JSON object")
        defaults = cls.default_modes()
        modes: dict[SessionMode, SignalModeConfig] = {}
        for name, raw in dict(data.get("modes", {})).items():
            mode = SessionMode(name)  # unknown mode names fail loud
            if not isinstance(raw, dict):
                raise ValueError(f"mode config for {name!r} must be an object")
            base = defaults[mode]
            modes[mode] = SignalModeConfig(
                enabled=bool(raw.get("enabled", base.enabled)),
                color=_validate_color(raw.get("color", base.color)),
                label=str(raw.get("label", base.label)),
                border=bool(raw.get("border", base.border)),
                cursor=bool(raw.get("cursor", base.cursor)),
            )
        hotkey = data.get("abort_hotkey")
        return cls(
            modes=modes,
            thickness=int(data.get("thickness", 6)),
            abort_hotkey=str(hotkey) if hotkey else None,
        )

    @classmethod
    def load(cls, path: str | Path) -> "SignalConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        return {
            "thickness": self.thickness,
            "abort_hotkey": self.abort_hotkey,
            "modes": {
                mode.value: {
                    "enabled": cfg.enabled,
                    "color": list(cfg.color),
                    "label": cfg.label,
                    "border": cfg.border,
                    "cursor": cfg.cursor,
                }
                for mode, cfg in self.modes.items()
            },
        }

    def save(self, path: str | Path) -> None:
        """Atomic JSON write (temp file + os.replace), like SessionStore."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


class OverlayRenderer(Protocol):
    """Renderer seam. Implementations must be click-through and non-modal."""

    def show(
        self,
        *,
        color: tuple[int, int, int],
        label: str,
        border: bool = True,
        cursor: bool = True,
    ) -> None: ...

    def hide(self) -> None: ...

    def is_visible(self) -> bool: ...


class AbortChannel(Protocol):
    """Where the human abort reason comes from (console, Tk overlay, ...)."""

    def prompt_reason(self, *, context: str) -> str | None: ...


@dataclass
class NullOverlayRenderer:
    """Headless default; renders nothing, like ``NullOwnershipIndicator``."""

    def show(
        self,
        *,
        color: tuple[int, int, int],
        label: str,
        border: bool = True,
        cursor: bool = True,
    ) -> None:
        return None

    def hide(self) -> None:
        return None

    def is_visible(self) -> bool:
        return False


@dataclass
class NullAbortChannel:
    """Default abort channel: never captures a message."""

    def prompt_reason(self, *, context: str) -> str | None:
        return None


@dataclass
class ConsoleAbortChannel:
    """stdin prompt; usable in terminals, never pops a window."""

    input_fn: Callable[[str], str] = input

    def prompt_reason(self, *, context: str) -> str | None:
        try:
            text = self.input_fn(
                "[open-compute] Abbruch"
                + (f" ({context})" if context else "")
                + " — kurzer Grund fuers Modell (leer = keiner): "
            )
        except (EOFError, KeyboardInterrupt):
            return None
        text = text.strip()
        return text or None


@dataclass
class TkAbortChannel:
    """Small topmost Tk input box — the abort overlay with reason entry.

    tkinter is stdlib; the import is lazy so the module stays import-safe
    where Tk is missing. Returns the entered text, or ``None`` on cancel,
    empty input, or timeout.
    """

    timeout_seconds: float = 60.0

    def prompt_reason(self, *, context: str) -> str | None:
        import tkinter as tk

        result: dict[str, str | None] = {"text": None}
        root = tk.Tk()
        root.title("open-compute — Abbruch")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        if context:
            tk.Label(root, text=context, anchor="w").pack(
                fill="x", padx=10, pady=(10, 0)
            )
        tk.Label(
            root,
            text="Kurzer Grund fuers Modell (wird mitgesendet):",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(10, 0))
        entry = tk.Entry(root, width=50)
        entry.pack(fill="x", padx=10, pady=10)
        entry.focus_set()

        def _ok() -> None:
            text = entry.get().strip()
            result["text"] = text or None
            root.destroy()

        def _cancel() -> None:
            result["text"] = None
            root.destroy()

        buttons = tk.Frame(root)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(buttons, text="Senden", command=_ok).pack(side="left")
        tk.Button(buttons, text="Ohne Grund", command=_cancel).pack(
            side="left", padx=(8, 0)
        )
        root.bind("<Return>", lambda _event: _ok())
        root.bind("<Escape>", lambda _event: _cancel())
        if self.timeout_seconds > 0:
            root.after(int(self.timeout_seconds * 1000), _cancel)
        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
        x = (root.winfo_screenwidth() - width) // 2
        y = max(0, (root.winfo_screenheight() - height) // 3)
        root.geometry(f"+{x}+{y}")
        root.mainloop()
        return result["text"]


@dataclass
class ScreenSignalIndicator:
    """``OwnershipIndicator`` implementation: renders mode color + agent + scope.

    Drop-in for ``CooperativeOrchestrator(indicator=…)``. All rendering goes
    through the injected :class:`OverlayRenderer`; the abort message goes
    through the injected :class:`AbortChannel` and is forwarded to
    ``on_abort_message`` (the caller passes it on to the model).
    """

    renderer: OverlayRenderer = field(default_factory=NullOverlayRenderer)
    abort_channel: AbortChannel = field(default_factory=NullAbortChannel)
    on_abort_message: Callable[[str], None] | None = None
    config: SignalConfig | None = None
    last_label: str = ""

    def show(self, *, agent: str, scope: str, mode: SessionMode) -> None:
        cfg = self.config or SignalConfig()
        mode_cfg = cfg.for_mode(mode)
        if not mode_cfg.enabled:
            # Mode is switched off in the config: render nothing at all.
            self.clear()
            return
        label_text = mode_cfg.label or signal_for_mode(mode)[0]
        self.last_label = f"{agent} | {label_text} | {scope}"
        self.renderer.show(
            color=mode_cfg.color,
            label=self.last_label,
            border=mode_cfg.border,
            cursor=mode_cfg.cursor,
        )

    def clear(self) -> None:
        self.renderer.hide()
        self.last_label = ""

    def capture_abort_message(self, *, context: str = "") -> str | None:
        """Collect the human's short abort message and forward it to the model.

        Returns the message (or ``None``); when a message exists and
        ``on_abort_message`` is set, the callback receives it.
        """

        reason = self.abort_channel.prompt_reason(
            context=context or self.last_label
        )
        if reason and self.on_abort_message is not None:
            self.on_abort_message(reason)
        return reason


# ---------------------------------------------------------------------------
# Windows overlay renderer (ctypes, lazy, click-through)
# ---------------------------------------------------------------------------

_BORDER_ALPHA = 235
_GLOW_ALPHA = 90
_CURSOR_RING = 48


class WindowsBorderOverlay:
    """Glowing screen border + neon cursor ring + status strip (Windows-only).

    Implementation: layered, topmost, click-through popup windows on a
    dedicated thread with its own message loop. The border is a bright inner
    frame plus a dimmer outer glow frame; the cursor ring follows the pointer
    (~30 Hz). Construction and use are confined to :meth:`show`/:meth:`hide`;
    instantiating on non-Windows raises ``RuntimeError``.
    """

    def __init__(
        self,
        *,
        thickness: int = 6,
        border: bool = True,
        cursor_ring: bool = True,
        on_abort: Callable[[], None] | None = None,
        abort_hotkey: str | None = None,
    ) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsBorderOverlay is Windows-only")
        if not 2 <= thickness <= 40:
            raise ValueError("thickness must be in 2..40")
        self._thickness = thickness
        self._border = border
        self._cursor_ring = cursor_ring
        self._on_abort = on_abort
        # Parsed eagerly so a bad spec fails at construction, not in the thread.
        self._abort_hotkey = parse_hotkey(abort_hotkey) if abort_hotkey else None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._visible = False
        self.error: BaseException | None = None

    # -- public seam -----------------------------------------------------

    def show(
        self,
        *,
        color: tuple[int, int, int],
        label: str,
        border: bool | None = None,
        cursor: bool | None = None,
    ) -> None:
        self.hide()
        self._stop.clear()
        self.error = None
        use_border = self._border if border is None else border
        use_cursor = self._cursor_ring if cursor is None else cursor
        self._thread = threading.Thread(
            target=self._run,
            args=(tuple(int(c) for c in color), str(label), use_border, use_cursor),
            name="oc-border-overlay",
            daemon=True,
        )
        self._thread.start()
        self._visible = True

    def hide(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._visible = False

    def is_visible(self) -> bool:
        return self._visible and self.error is None

    def _fire_abort(self) -> None:
        """Invoke the abort callback; failures surface via ``self.error``."""

        try:
            if self._on_abort is not None:
                self._on_abort()
        except BaseException as exc:
            self.error = exc

    # -- thread internals ------------------------------------------------

    def _run(
        self,
        color: tuple[int, int, int],
        label: str,
        border: bool = True,
        cursor: bool = True,
    ) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32

        # 64-bit safety: handle-returning calls must not truncate to c_int.
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = wintypes.LPARAM
        user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.BeginPaint.restype = wintypes.HDC
        user32.EndPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetClientRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT),
        ]
        user32.FillRect.argtypes = [
            wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH,
        ]
        user32.DrawTextW.argtypes = [
            wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
            ctypes.POINTER(wintypes.RECT), wintypes.UINT,
        ]
        user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t,
        ]
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetLayeredWindowAttributes.argtypes = [
            wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD,
        ]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT,
        ]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
        ]
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        gdi32.CreatePen.restype = wintypes.HANDLE
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.GetStockObject.argtypes = [ctypes.c_int]
        gdi32.GetStockObject.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
        gdi32.Ellipse.argtypes = [wintypes.HDC] + [ctypes.c_int] * 4
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        WNDPROC = ctypes.WINFUNCTYPE(
            wintypes.LPARAM,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        def _colorref(rgb: tuple[int, int, int]) -> int:
            r, g, b = rgb
            return r | (g << 8) | (b << 16)

        colorref = _colorref(color)
        brush = gdi32.CreateSolidBrush(colorref)
        black_brush = gdi32.CreateSolidBrush(0)
        glow_brush = gdi32.CreateSolidBrush(colorref)
        ring_pen = gdi32.CreatePen(0, 4, colorref)
        glow_pen = gdi32.CreatePen(0, 10, colorref)
        created: dict[str, list] = {"hwnds": []}
        hotkey_id: int | None = None

        WM_PAINT = 0x000F
        WM_DESTROY = 0x0002
        WM_ERASEBKGND = 0x0014

        def _paint_ring(hwnd: int) -> None:
            hdc = user32.GetDC(hwnd)
            try:
                old_glow = gdi32.SelectObject(hdc, glow_pen)
                old_brush = gdi32.SelectObject(
                    hdc, gdi32.GetStockObject(5)  # NULL_BRUSH
                )
                gdi32.Ellipse(hdc, 2, 2, _CURSOR_RING - 2, _CURSOR_RING - 2)
                gdi32.SelectObject(hdc, ring_pen)
                gdi32.Ellipse(hdc, 8, 8, _CURSOR_RING - 8, _CURSOR_RING - 8)
                gdi32.SelectObject(hdc, old_glow)
                gdi32.SelectObject(hdc, old_brush)
            finally:
                user32.ReleaseDC(hwnd, hdc)

        def _paint_label(hwnd: int) -> None:
            hdc = user32.GetDC(hwnd)
            try:
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
                gdi32.SetTextColor(hdc, colorref)
                gdi32.SelectObject(
                    hdc, gdi32.GetStockObject(17)  # DEFAULT_GUI_FONT
                )
                user32.DrawTextW(
                    hdc, label, -1, ctypes.byref(rect), 0x0024  # DT_CENTER|DT_VCENTER
                )
            finally:
                user32.ReleaseDC(hwnd, hdc)

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_ERASEBKGND:
                role = user32.GetWindowLongPtrW(hwnd, -21)  # GWLP_USERDATA
                if role:
                    # ring/label windows need a black backdrop so the color
                    # key makes everything but the drawing transparent
                    rect = wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(rect))
                    user32.FillRect(wparam, ctypes.byref(rect), black_brush)
                    return 1
                # border bars keep the class brush (the signal color)
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            if msg == WM_PAINT:
                ps = ctypes.create_string_buffer(72)
                hdc = user32.BeginPaint(hwnd, ps)
                user32.EndPaint(hwnd, ps)
                role = user32.GetWindowLongPtrW(hwnd, -21)  # GWLP_USERDATA
                if role == 1:
                    _paint_ring(hwnd)
                elif role == 2:
                    _paint_label(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wnd_proc = WNDPROC(_wnd_proc)  # keep alive for the thread lifetime

        instance = kernel32.GetModuleHandleW(None)
        class_name = f"OCSignalOverlay_{id(self) & 0xFFFFFFFF:x}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = wnd_proc
        wc.hInstance = instance
        wc.lpszClassName = class_name
        wc.hbrBackground = brush
        if not user32.RegisterClassW(ctypes.byref(wc)):
            self.error = RuntimeError("RegisterClassW failed")
            return

        ex_style = (
            0x00000080  # WS_EX_TOOLWINDOW
            | 0x00000020  # WS_EX_TRANSPARENT (click-through)
            | 0x00080000  # WS_EX_LAYERED
            | 0x00000008  # WS_EX_TOPMOST
            | 0x08000000  # WS_EX_NOACTIVATE
        )
        WS_POPUP = 0x80000000

        def _create(x, y, w, h, role=0, alpha=_BORDER_ALPHA, colorkey=None):
            hwnd = user32.CreateWindowExW(
                ex_style, class_name, "", WS_POPUP, x, y, w, h,
                None, None, instance, None,
            )
            if not hwnd:
                raise RuntimeError("CreateWindowExW failed")
            if role:
                user32.SetWindowLongPtrW(hwnd, -21, role)  # GWLP_USERDATA
            if colorkey is not None:
                user32.SetLayeredWindowAttributes(hwnd, colorkey, 0, 1)  # LWA_COLORKEY
            else:
                user32.SetLayeredWindowAttributes(hwnd, 0, alpha, 2)  # LWA_ALPHA
            user32.ShowWindow(hwnd, 8)  # SW_SHOWNA
            created["hwnds"].append(hwnd)
            return hwnd

        try:
            vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            vy = user32.GetSystemMetrics(77)
            vw = user32.GetSystemMetrics(78)
            vh = user32.GetSystemMetrics(79)
            t = self._thickness
            glow = t + 6

            # outer glow frame (dimmer), then bright inner frame
            if border:
                for width, alpha in ((glow, _GLOW_ALPHA), (t, _BORDER_ALPHA)):
                    _create(vx, vy, vw, width, alpha=alpha)
                    _create(vx, vy + vh - width, vw, width, alpha=alpha)
                    _create(vx, vy + width, width, vh - 2 * width, alpha=alpha)
                    _create(vx + vw - width, vy + width, width, vh - 2 * width, alpha=alpha)

            cursor_hwnd = None
            if cursor:
                cursor_hwnd = _create(
                    0, 0, _CURSOR_RING, _CURSOR_RING, role=1, colorkey=0
                )
            if label:
                label_w = min(720, vw - 40)
                _create(
                    vx + (vw - label_w) // 2, vy + glow + 4, label_w, 26,
                    role=2, alpha=210, colorkey=None,
                )

            hotkey_id = None
            if self._abort_hotkey is not None and self._on_abort is not None:
                mods, vk = self._abort_hotkey
                # 0x4000 = MOD_NOREPEAT: one fire per press, not a stream
                if user32.RegisterHotKey(None, 1, mods | 0x4000, vk):
                    hotkey_id = 1

            msg = wintypes.MSG()
            point = wintypes.POINT()
            WM_HOTKEY = 0x0312
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == WM_HOTKEY:
                        # Fire on a side thread so the Tk input box can run
                        # its own modal loop without stalling the overlay.
                        threading.Thread(
                            target=self._fire_abort,
                            name="oc-abort-hotkey",
                            daemon=True,
                        ).start()
                        continue
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                if cursor_hwnd and user32.GetCursorPos(ctypes.byref(point)):
                    user32.SetWindowPos(
                        cursor_hwnd, None,
                        point.x - _CURSOR_RING // 2,
                        point.y - _CURSOR_RING // 2,
                        0, 0,
                        0x0001 | 0x0004 | 0x0010,  # NOSIZE|NOACTIVATE|NOZORDER? keep topmost via ex style
                    )
                self._stop.wait(0.033)
        except BaseException as exc:  # surface overlay failures, never crash caller
            self.error = exc
        finally:
            if hotkey_id is not None:
                user32.UnregisterHotKey(None, hotkey_id)
            for hwnd in created["hwnds"]:
                user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, instance)
            for obj in (brush, black_brush, glow_brush, ring_pen, glow_pen):
                gdi32.DeleteObject(obj)
