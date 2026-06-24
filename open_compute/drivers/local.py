"""Real Windows executor: screenshots via mss + input via ctypes SendInput.

This is the host-side driver that makes both operating modes real:
- **Mode A (No-Key Skill):** ``oc capture`` / ``oc do`` call this directly.
- **Mode B (Autonomous AgentLoop):** injected as the executor in the loop.

Dependencies are **lazy** so that ``import open_compute`` never requires mss.
Install the optional extra:  ``pip install open-compute[local]``

Windows-only: uses ctypes Win32 APIs (SendInput, GetSystemMetrics,
SetProcessDpiAwarenessContext). The module imports ctypes at the top level
(stdlib, always available) but defers mss to the first screenshot call.

Coordinate system
-----------------
SendInput in ABSOLUTE + VIRTUALDESK mode maps (0, 0)..(65535, 65535) across
the **entire virtual desktop** (all monitors combined). The virtual desktop
may have a negative top-left on multi-monitor setups. We query
``SM_XVIRTUALSCREEN`` / ``SM_YVIRTUALSCREEN`` for the origin and
``SM_CXVIRTUALSCREEN`` / ``SM_CYVIRTUALSCREEN`` for the span.

mss uses ``monitors[0]`` which is also the whole virtual desktop, with
``"left"`` / ``"top"`` giving the same (possibly negative) origin. Both use
the same frame of reference, so a normalized (0..1) coordinate round-trips
cleanly: ``normalize → _to_sendinput → pixel_back_via_monitor`` lands at
the same logical pixel as mss grabbed it from.

DPI awareness
-------------
``SetProcessDpiAwarenessContext(-4)`` (Per-Monitor-v2) is called once at
executor init so that both GetSystemMetrics *and* mss report true physical
pixel dimensions. Without this, scaled coordinates and grab dimensions
diverge on high-DPI displays.

The default path is pure stdlib + optional mss. The WGC fallback is isolated
behind the ``open-compute[wgc]`` extra, which brings Pillow and the
``windows-capture`` transitive dependencies.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import io
import sys
from dataclasses import dataclass, field
from typing import Any

from ..actions import Action, ActionType
from ..perception import Observation

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# GetSystemMetrics indices for virtual desktop geometry
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

WHEEL_DELTA = 120  # one notch = 120 units

# VK codes for named keys (subset; extended via _VK_MAP)
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_BACK = 0x08
VK_TAB = 0x09
VK_DELETE = 0x2E
VK_HOME = 0x24
VK_END = 0x23
VK_INSERT = 0x2D
VK_PRIOR = 0x21  # Page Up
VK_NEXT = 0x22   # Page Down
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_F1 = 0x70
VK_CONTROL = 0x11
VK_MENU = 0x12   # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_SPACE = 0x20

_VK_MAP: dict[str, int] = {
    "return": VK_RETURN, "enter": VK_RETURN,
    "escape": VK_ESCAPE, "esc": VK_ESCAPE,
    "backspace": VK_BACK,
    "tab": VK_TAB,
    "delete": VK_DELETE, "del": VK_DELETE,
    "home": VK_HOME, "end": VK_END,
    "insert": VK_INSERT, "ins": VK_INSERT,
    "pageup": VK_PRIOR, "page_up": VK_PRIOR, "prior": VK_PRIOR,
    "pagedown": VK_NEXT, "page_down": VK_NEXT, "next": VK_NEXT,
    "left": VK_LEFT, "up": VK_UP, "right": VK_RIGHT, "down": VK_DOWN,
    "ctrl": VK_CONTROL, "control": VK_CONTROL,
    "alt": VK_MENU, "menu": VK_MENU,
    "shift": VK_SHIFT,
    "win": VK_LWIN, "super": VK_LWIN,
    "space": VK_SPACE,
    **{f"f{i}": VK_F1 + i - 1 for i in range(1, 25)},
}

# ---------------------------------------------------------------------------
# ctypes structures
# ---------------------------------------------------------------------------

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("_input", _INPUT_UNION),
    ]


# ---------------------------------------------------------------------------
# Pure coordinate-math helper (testable without OS)
# ---------------------------------------------------------------------------

def to_sendinput_coords(
    nx: float,
    ny: float,
    virt_left: int,
    virt_top: int,
    virt_width: int,
    virt_height: int,
) -> tuple[int, int]:
    """Map normalized 0..1 coords to SendInput's 0..65535 virtual-desktop range.

    The virtual desktop may start at a negative origin (multi-monitor setups
    where a secondary monitor is positioned to the left of the primary). We
    shift the normalized position relative to the virtual desktop span.

    Args:
        nx, ny: Normalized position in 0..1 (relative to the captured frame).
        virt_left, virt_top: ``SM_XVIRTUALSCREEN`` / ``SM_YVIRTUALSCREEN``
            (the absolute origin of the virtual desktop; may be negative).
        virt_width, virt_height: ``SM_CXVIRTUALSCREEN`` / ``SM_CYVIRTUALSCREEN``.

    Returns:
        ``(dx, dy)`` in 0..65535 for use with ``MOUSEEVENTF_ABSOLUTE |
        MOUSEEVENTF_VIRTUALDESK``.
    """
    # mss monitors[0] covers the whole virtual desktop (same origin/span as
    # SM_XVIRTUALSCREEN..SM_CXVIRTUALSCREEN), so nx/ny already maps linearly.
    # The formula: fraction_of_65535_range + offset_to_cover_negative_origins.
    # SendInput absolute coords map [0,65535] across the virtual desktop.
    # We just need to convert nx/ny (already 0..1 of virtual desktop) to
    # the [0, 65535] integer range.
    dx = int(round(nx * 65535))
    dy = int(round(ny * 65535))
    # Clamp to valid range
    dx = max(0, min(65535, dx))
    dy = max(0, min(65535, dy))
    return dx, dy


def _capture_rect_to_sendinput_coords(
    nx: float,
    ny: float,
    capture_left: int,
    capture_top: int,
    capture_width: int,
    capture_height: int,
    virt_left: int,
    virt_top: int,
    virt_width: int,
    virt_height: int,
) -> tuple[int, int]:
    """Map normalized coordinates from a capture frame into virtual desktop input."""
    if capture_width <= 0 or capture_height <= 0 or virt_width <= 0 or virt_height <= 0:
        return 0, 0
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    px = capture_left + nx * max(0, capture_width - 1)
    py = capture_top + ny * max(0, capture_height - 1)
    dx = int(round(((px - virt_left) / max(1, virt_width - 1)) * 65535))
    dy = int(round(((py - virt_top) / max(1, virt_height - 1)) * 65535))
    return max(0, min(65535, dx)), max(0, min(65535, dy))


def _place_png_on_virtual_canvas(
    png_bytes: bytes,
    frame_left: int,
    frame_top: int,
    virt_left: int,
    virt_top: int,
    virt_width: int,
    virt_height: int,
) -> bytes:
    """Place a monitor PNG on a virtual-desktop-sized black canvas."""
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as img:
        if (
            frame_left == virt_left
            and frame_top == virt_top
            and img.width == virt_width
            and img.height == virt_height
        ):
            return png_bytes
        canvas = Image.new("RGB", (virt_width, virt_height), (0, 0, 0))
        canvas.paste(img.convert("RGB"), (frame_left - virt_left, frame_top - virt_top))
        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue()


# ---------------------------------------------------------------------------
# Windows API helper functions
# ---------------------------------------------------------------------------

def _set_dpi_awareness() -> bool:
    """Call SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2).

    Safe to call multiple times; ignores "already set" errors.

    Returns:
        ``True`` if the call succeeded (or we are not on Windows), ``False``
        if Win32 reported failure.  The return value is informational only.

    Note: Must set ``argtypes=[c_void_p]`` so that the handle value ``-4``
    (``0xFFFFFFFFFFFFFFFC`` on 64-bit Windows) is passed correctly through
    libffi. Without ``argtypes`` the bare Python ``int(-4)`` is passed as a
    32-bit value and Win32 receives the wrong handle, causing silent failure
    (return 0 but no exception).
    """
    if sys.platform != "win32":
        return True
    try:
        fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
        fn.restype = ctypes.c_bool
        fn.argtypes = [ctypes.c_void_p]
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = handle value -4
        return bool(fn(ctypes.c_void_p(-4)))
    except (AttributeError, OSError):
        # Not available on Windows < 1703; silently skip.
        return False


def _get_virtual_desktop() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the virtual desktop in pixels."""
    gm = ctypes.windll.user32.GetSystemMetrics
    left = gm(SM_XVIRTUALSCREEN)
    top = gm(SM_YVIRTUALSCREEN)
    width = gm(SM_CXVIRTUALSCREEN)
    height = gm(SM_CYVIRTUALSCREEN)
    # Fallback to primary monitor if virtual desktop query returns zeros
    if width <= 0:
        width = gm(0)   # SM_CXSCREEN
        height = gm(1)  # SM_CYSCREEN
        left = 0
        top = 0
    return left, top, width, height


def _send_input(*inputs: _INPUT) -> int:
    """Thin wrapper around SendInput; returns number of events inserted."""
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    return ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))


def _mouse_event(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp._input.mi.dx = dx
    inp._input.mi.dy = dy
    inp._input.mi.mouseData = data
    inp._input.mi.dwFlags = flags
    inp._input.mi.time = 0
    inp._input.mi.dwExtraInfo = None  # type: ignore[assignment]
    return inp


def _key_event(vk: int, flags: int = 0, scan: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki.wVk = vk
    inp._input.ki.wScan = scan
    inp._input.ki.dwFlags = flags
    inp._input.ki.time = 0
    inp._input.ki.dwExtraInfo = None  # type: ignore[assignment]
    return inp


def _unicode_event(char: str, flags: int = 0) -> _INPUT:
    """Synthesize a Unicode key event (KEYEVENTF_UNICODE)."""
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki.wVk = 0
    inp._input.ki.wScan = ord(char)
    inp._input.ki.dwFlags = KEYEVENTF_UNICODE | flags
    inp._input.ki.time = 0
    inp._input.ki.dwExtraInfo = None  # type: ignore[assignment]
    return inp


# ---------------------------------------------------------------------------
# LocalExecutor
# ---------------------------------------------------------------------------

@dataclass
class LocalExecutor:
    """Real Windows executor: screenshots via mss, input via ctypes SendInput.

    Satisfies the :class:`~open_compute.drivers.base.Executor` protocol and the
    host-side :class:`~open_compute.drivers.base.OSDriver` surface.

    Args:
        monitor_index: Which monitor to capture. ``0`` = virtual desktop
            (all monitors combined, recommended for consistent coordinate mapping).
            ``1`` = primary monitor, ``2`` = second monitor, etc.

    Raises:
        ImportError: If ``mss`` is not installed when a screenshot is taken.
            Install with ``pip install open-compute[local]``.
    """

    monitor_index: int = 0
    _virt: tuple[int, int, int, int] | None = field(default=None, init=False, repr=False)
    _capture_rect: tuple[int, int, int, int] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        _set_dpi_awareness()
        # Cache virtual desktop geometry (stable for the process lifetime on
        # a single-session machine; re-query if needed).
        self._virt = _get_virtual_desktop()
        self._capture_rect = self._virt

    @property
    def width(self) -> int:
        """Current capture-frame width in pixels."""
        assert self._capture_rect is not None
        return self._capture_rect[2]

    @property
    def height(self) -> int:
        """Current capture-frame height in pixels."""
        assert self._capture_rect is not None
        return self._capture_rect[3]

    # ------------------------------------------------------------------
    # Executor protocol
    # ------------------------------------------------------------------

    def execute(self, action: Action) -> Observation:
        """Dispatch a canonical action and return a fresh observation."""
        t = action.type
        if t is ActionType.SCREENSHOT:
            return self.screenshot()
        if t is ActionType.MOUSE_MOVE:
            self._move(action.x, action.y)
        elif t is ActionType.LEFT_CLICK:
            self._click(action.x, action.y, "left")
        elif t is ActionType.RIGHT_CLICK:
            self._click(action.x, action.y, "right")
        elif t is ActionType.MIDDLE_CLICK:
            self._click(action.x, action.y, "middle")
        elif t is ActionType.DOUBLE_CLICK:
            self._click(action.x, action.y, "left")
            self._click(action.x, action.y, "left")
        elif t is ActionType.TRIPLE_CLICK:
            self._click(action.x, action.y, "left")
            self._click(action.x, action.y, "left")
            self._click(action.x, action.y, "left")
        elif t is ActionType.LEFT_CLICK_DRAG:
            self._drag(action.x, action.y, action.end_x, action.end_y)
        elif t is ActionType.TYPE:
            self._type(action.text or "")
        elif t is ActionType.KEY:
            self._key(action.text or "")
        elif t is ActionType.SCROLL:
            self._scroll(
                action.x, action.y,
                action.scroll_direction or "down",
                action.scroll_amount or 3,
            )
        elif t is ActionType.WAIT:
            import time
            time.sleep(action.duration or 1.0)
        elif t is ActionType.CURSOR_POSITION:
            pass  # read-only; just capture
        elif t is ActionType.LAUNCH_APP:
            self.launch_app(action.app_name or "")
        elif t is ActionType.ACTIVATE_WINDOW:
            self.activate_window(action.app_name or "")
        return self.screenshot()

    def screenshot(self) -> Observation:
        """Capture the virtual desktop and return PNG bytes as an Observation.

        Uses fast GDI capture (``mss``) by default. When GDI ``BitBlt`` fails -
        which it does for DirectX / hardware-rendered / occluded windows such as
        Roblox Studio, Blender, or games ("Zugriff verweigert" / access denied) -
        it falls back to the Windows.Graphics.Capture backend (``wgc``), which
        grabs the composited monitor including hardware surfaces.
        Requires a non-sandboxed Python (the Microsoft-Store Python destabilises
        WGC); install python.org Python for reliable Studio/game capture.
        """
        try:
            import mss  # noqa: F401 — lazy; optional extra
            import mss.tools
        except ImportError as exc:
            raise ImportError(
                "Screenshot requires the 'mss' package. "
                "Install it with: pip install open-compute[local]"
            ) from exc

        selected_mon: dict[str, int] | None = None
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                # monitors[0] is the virtual desktop (all monitors combined).
                # monitors[1..N] are individual monitors.
                idx = min(self.monitor_index, len(monitors) - 1)
                selected_mon = monitors[idx]
                shot = sct.grab(selected_mon)
                # mss.tools.to_png converts the raw BGRA grab to a PNG byte string.
                png_bytes: bytes = mss.tools.to_png(shot.rgb, shot.size)
            self._capture_rect = (
                int(selected_mon.get("left", 0)),
                int(selected_mon.get("top", 0)),
                shot.width,
                shot.height,
            )
            return Observation(
                screenshot=png_bytes,
                width=shot.width,
                height=shot.height,
            )
        except Exception as mss_exc:
            # GDI BitBlt failed (typically a DirectX/hardware-composited window).
            # Fall back to Windows.Graphics.Capture if available.
            try:
                from . import wgc
            except ImportError:
                raise mss_exc
            if not wgc.available():
                raise mss_exc
            # mss monitor 0 = virtual desktop, 1..N = monitors; WGC is 1-based per
            # monitor. Map mss 0 -> primary monitor (1); otherwise pass through.
            wgc_idx = self.monitor_index if self.monitor_index >= 1 else 1
            png_bytes, width, height = wgc.grab_monitor_png(monitor_index=wgc_idx)
            if self.monitor_index == 0:
                assert self._virt is not None
                vl, vt, vw, vh = self._virt
                png_bytes = _place_png_on_virtual_canvas(
                    png_bytes, 0, 0, vl, vt, vw, vh
                )
                self._capture_rect = self._virt
                width, height = vw, vh
            else:
                left = int(selected_mon.get("left", 0)) if selected_mon else 0
                top = int(selected_mon.get("top", 0)) if selected_mon else 0
                self._capture_rect = (left, top, width, height)
            return Observation(screenshot=png_bytes, width=width, height=height)

    # ------------------------------------------------------------------
    # OSDriver surface
    # ------------------------------------------------------------------

    def launch_app(self, app_name: str) -> None:
        """Launch an application by name via ShellExecute."""
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "open", app_name, None, None, 1)

    def activate_window(self, app_name: str) -> None:
        """Bring a window matching ``app_name`` to the foreground (best-effort)."""
        # Find the window by title substring and bring it to the foreground.
        user32 = ctypes.windll.user32

        found: list[int] = []

        # IMPORTANT: keep a reference to the ctypes callback wrapper in a local
        # variable so the GC does not collect it before EnumWindows returns.
        # Without this, 64-bit Python can free the wrapper mid-call (classic
        # ctypes bug with locally-defined WINFUNCTYPE callbacks).
        _EnumCallback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

        def _enum_impl(hwnd: int, _lp: int) -> bool:
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if app_name.lower() in buf.value.lower():
                found.append(hwnd)
            return True

        _cb_ref = _EnumCallback(_enum_impl)  # keep alive until EnumWindows returns
        user32.EnumWindows(_cb_ref, 0)
        if found:
            hwnd = found[0]
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            user32.SetForegroundWindow(hwnd)

    # ------------------------------------------------------------------
    # Internal input helpers
    # ------------------------------------------------------------------

    def _sendinput_coords(self, nx: float, ny: float) -> tuple[int, int]:
        """Convert normalized 0..1 to SendInput 0..65535 via virtual desktop."""
        assert self._virt is not None
        assert self._capture_rect is not None
        cl, ct, cw, ch = self._capture_rect
        vl, vt, vw, vh = self._virt
        return _capture_rect_to_sendinput_coords(nx, ny, cl, ct, cw, ch, vl, vt, vw, vh)

    def _move(self, nx: float | None, ny: float | None) -> None:
        if nx is None or ny is None:
            return
        dx, dy = self._sendinput_coords(nx, ny)
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        _send_input(_mouse_event(flags, dx, dy))

    def _click(self, nx: float | None, ny: float | None, button: str) -> None:
        if nx is None or ny is None:
            return
        dx, dy = self._sendinput_coords(nx, ny)
        move_flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        _send_input(_mouse_event(move_flags, dx, dy))

        if button == "left":
            down = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            up = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        elif button == "right":
            down = MOUSEEVENTF_RIGHTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            up = MOUSEEVENTF_RIGHTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        else:  # middle
            down = MOUSEEVENTF_MIDDLEDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            up = MOUSEEVENTF_MIDDLEUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK

        _send_input(
            _mouse_event(down, dx, dy),
            _mouse_event(up, dx, dy),
        )

    def _drag(
        self,
        sx: float | None, sy: float | None,
        ex: float | None, ey: float | None,
    ) -> None:
        if None in (sx, sy, ex, ey):
            return
        sdx, sdy = self._sendinput_coords(sx, sy)  # type: ignore[arg-type]
        edx, edy = self._sendinput_coords(ex, ey)   # type: ignore[arg-type]
        move_flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        ld = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        lu = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        _send_input(
            _mouse_event(move_flags, sdx, sdy),
            _mouse_event(ld, sdx, sdy),
            _mouse_event(move_flags, edx, edy),
            _mouse_event(lu, edx, edy),
        )

    def _type(self, text: str) -> None:
        """Type a string via Unicode key events (KEYEVENTF_UNICODE)."""
        events: list[_INPUT] = []
        for ch in text:
            events.append(_unicode_event(ch))
            events.append(_unicode_event(ch, KEYEVENTF_KEYUP))
        if events:
            # Send in one batch for performance
            n = len(events)
            arr = (_INPUT * n)(*events)
            ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))

    def _key(self, combo: str) -> None:
        """Press a key combination like ``ctrl+s`` or ``Return``."""
        parts = [p.strip() for p in combo.replace("+", " ").split() if p.strip()]
        vks: list[int] = []
        for part in parts:
            key_lower = part.lower()
            vk = _VK_MAP.get(key_lower)
            if vk is None:
                # Single printable character
                vk = ctypes.windll.user32.VkKeyScanW(ord(part[0])) & 0xFF
            vks.append(vk)

        # Press all keys down, then release in reverse
        down_events = [_key_event(vk) for vk in vks]
        up_events = [_key_event(vk, KEYEVENTF_KEYUP) for vk in reversed(vks)]
        all_events = down_events + up_events
        n = len(all_events)
        arr = (_INPUT * n)(*all_events)
        ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))

    def _scroll(
        self,
        nx: float | None, ny: float | None,
        direction: str,
        amount: int,
    ) -> None:
        if nx is None or ny is None:
            return
        dx, dy = self._sendinput_coords(nx, ny)
        move_flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        _send_input(_mouse_event(move_flags, dx, dy))

        wheel_flags = MOUSEEVENTF_WHEEL | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        # Positive wheel = scroll up; negative = scroll down
        if direction == "up":
            wheel_delta = WHEEL_DELTA * amount
        elif direction == "down":
            wheel_delta = -(WHEEL_DELTA * amount)
        else:
            # left/right: use MOUSEEVENTF_HWHEEL; not all hardware supports it.
            # Fall back to vertical scroll if unsupported.
            wheel_flags = 0x1000 | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK  # MOUSEEVENTF_HWHEEL
            wheel_delta = WHEEL_DELTA * amount if direction == "right" else -(WHEEL_DELTA * amount)

        # mouseData for WHEEL is a *signed* 16-bit value but stored as ULONG.
        # We cast via ctypes c_long to get the correct two's complement bits.
        _send_input(_mouse_event(wheel_flags, dx, dy, ctypes.c_ulong(wheel_delta).value))
