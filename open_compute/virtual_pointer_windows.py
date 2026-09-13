"""Windows host for the dedicated virtual LLM pointer overlay.

The host follows the existing :mod:`open_compute.indicator` window policy
(tool window, layered, topmost, click-through and non-activating), but owns a
separate window class and never reads or moves the physical cursor.  Native
calls live behind a host/probe seam so the coordinate and lifecycle contracts
can be tested without opening a desktop window.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .virtual_pointer import PointerCoordinateFrame, PointerFrameKind
from .virtual_pointer_overlay import (
    PointerOverlayCapabilities,
    PointerOverlayCommand,
    PointerOverlayContractError,
    PointerRenderSurface,
)


# These flags intentionally match the non-interactive windows of the existing
# ownership indicator.  Keep the names public: contract tests and alternate
# Windows hosts can verify the policy without importing ctypes.
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
SW_SHOWNA = 8
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HTTRANSPARENT = -1
PER_MONITOR_AWARE_V2 = -4

VIRTUAL_POINTER_EXTENDED_STYLE = (
    WS_EX_TOOLWINDOW
    | WS_EX_TRANSPARENT
    | WS_EX_LAYERED
    | WS_EX_TOPMOST
    | WS_EX_NOACTIVATE
)


def _integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


@dataclass(frozen=True)
class WindowsVirtualDesktopSnapshot:
    """One atomic physical-pixel description of the Windows desktop."""

    left: int
    top: int
    width: int
    height: int
    monitor_rects: tuple[tuple[int, int, int, int], ...]

    def __post_init__(self) -> None:
        for name in ("left", "top", "width", "height"):
            object.__setattr__(self, name, _integer(name, getattr(self, name)))
        if self.width <= 0 or self.height <= 0:
            raise ValueError("virtual desktop dimensions must be positive")
        if not isinstance(self.monitor_rects, tuple) or not self.monitor_rects:
            raise ValueError("at least one monitor rectangle is required")
        normalized: list[tuple[int, int, int, int]] = []
        for rect in self.monitor_rects:
            if not isinstance(rect, tuple) or len(rect) != 4:
                raise ValueError("monitor rectangles must be four-integer tuples")
            left, top, right, bottom = (
                _integer("monitor-coordinate", value) for value in rect
            )
            if right <= left or bottom <= top:
                raise ValueError("monitor rectangles must have positive area")
            normalized.append((left, top, right, bottom))
        normalized.sort()
        object.__setattr__(self, "monitor_rects", tuple(normalized))

        union = (
            min(rect[0] for rect in normalized),
            min(rect[1] for rect in normalized),
            max(rect[2] for rect in normalized),
            max(rect[3] for rect in normalized),
        )
        expected = (
            self.left,
            self.top,
            self.left + self.width,
            self.top + self.height,
        )
        if union != expected:
            raise ValueError("virtual desktop bounds and monitor topology disagree")

    @property
    def signature(self) -> tuple[object, ...]:
        return (self.left, self.top, self.width, self.height, self.monitor_rects)


@runtime_checkable
class WindowsVirtualDesktopProbe(Protocol):
    """Provides PMv2 physical-pixel snapshots without pointer state."""

    def snapshot(self) -> WindowsVirtualDesktopSnapshot: ...


@dataclass
class WindowsPointerSurfaceTracker:
    """Turns native topology snapshots into versioned pointer surfaces."""

    probe: WindowsVirtualDesktopProbe
    _signature: tuple[object, ...] | None = field(default=None, init=False)
    _generation: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def current_surface(self) -> PointerRenderSurface:
        snapshot = self.probe.snapshot()
        signature = snapshot.signature
        digest = hashlib.sha256(repr(signature).encode("ascii")).hexdigest()[:16]
        with self._lock:
            if self._signature is not None and signature != self._signature:
                self._generation += 1
            self._signature = signature
            generation = self._generation
        frame = PointerCoordinateFrame(
            frame_id=f"windows-virtual-desktop-{generation}-{digest}",
            kind=PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX,
            left=snapshot.left,
            top=snapshot.top,
            width=snapshot.width,
            height=snapshot.height,
            generation=generation,
            # Under a PMv2 context these are physical pixels.  Per-monitor UI
            # scale is deliberately not applied a second time.
            scale_x=1.0,
            scale_y=1.0,
        )
        return PointerRenderSurface(
            frame=frame,
            topology_id=f"windows-topology-{generation}-{digest}",
        )


@dataclass(frozen=True)
class WindowsPointerWindowSpec:
    """Exact, unclamped physical-pixel placement for one pointer window."""

    command: PointerOverlayCommand
    left: int
    top: int
    width: int
    height: int
    hotspot_x: int
    hotspot_y: int
    extended_style: int = VIRTUAL_POINTER_EXTENDED_STYLE
    window_style: int = WS_POPUP
    show_command: int = SW_SHOWNA
    set_position_flags: int = SWP_NOACTIVATE | SWP_SHOWWINDOW

    def __post_init__(self) -> None:
        for name in (
            "left",
            "top",
            "width",
            "height",
            "hotspot_x",
            "hotspot_y",
            "extended_style",
            "window_style",
            "show_command",
            "set_position_flags",
        ):
            object.__setattr__(self, name, _integer(name, getattr(self, name)))
        if self.width <= 0 or self.height <= 0:
            raise ValueError("pointer window dimensions must be positive")
        if not 0 <= self.hotspot_x < self.width:
            raise ValueError("hotspot_x must be inside the window")
        if not 0 <= self.hotspot_y < self.height:
            raise ValueError("hotspot_y must be inside the window")
        if self.left + self.hotspot_x != self.command.position.x:
            raise ValueError("window hotspot must preserve the exact physical x pixel")
        if self.top + self.hotspot_y != self.command.position.y:
            raise ValueError("window hotspot must preserve the exact physical y pixel")


def pointer_window_spec(command: PointerOverlayCommand) -> WindowsPointerWindowSpec:
    """Build an exact physical placement and never clamp negative origins."""

    x = command.position.x
    y = command.position.y
    if not math.isfinite(x) or not math.isfinite(y) or int(x) != x or int(y) != y:
        raise PointerOverlayContractError("windows-pointer-pixel-not-integral")
    radius = command.style.radius_px
    padding = command.style.stroke_px + 5
    hotspot = radius + padding
    marker_size = hotspot * 2 + 1
    label_height = 24
    label_width = max(176, 12 + len(command.label) * 8)
    return WindowsPointerWindowSpec(
        command=command,
        left=int(x) - hotspot,
        top=int(y) - hotspot,
        width=max(marker_size, label_width),
        height=marker_size + label_height,
        hotspot_x=hotspot,
        hotspot_y=hotspot,
    )


def surface_matches_command(
    surface: PointerRenderSurface,
    command: PointerOverlayCommand,
) -> bool:
    """Generation/frame/topology gate shared by render and hotplug paths."""

    return surface.frame == command.frame and surface.topology_id == command.topology_id


@runtime_checkable
class WindowsPointerWindowHost(Protocol):
    """Native host boundary; fake implementations never touch a desktop."""

    def current_surface(self) -> PointerRenderSurface: ...

    def show_pointer_window(self, spec: WindowsPointerWindowSpec) -> None: ...

    def destroy_pointer_window(self, *, reason: str) -> None: ...


@dataclass
class WindowsVirtualPointerOverlayRenderer:
    """Concrete renderer owning one dedicated Windows LLM-pointer window."""

    host: WindowsPointerWindowHost | None = None

    def __post_init__(self) -> None:
        if self.host is None:
            self.host = CtypesWindowsPointerWindowHost()

    def capabilities(self) -> PointerOverlayCapabilities:
        return PointerOverlayCapabilities(
            click_through=True,
            no_activate=True,
            per_monitor_v2=True,
            renders_shape=True,
            renders_text=True,
            renders_color=True,
        )

    def current_surface(self) -> PointerRenderSurface:
        assert self.host is not None
        return self.host.current_surface()

    def show_pointer(self, command: PointerOverlayCommand) -> None:
        assert self.host is not None
        surface = self.host.current_surface()
        if not surface_matches_command(surface, command):
            self.host.destroy_pointer_window(reason="surface-changed-before-show")
            raise PointerOverlayContractError("windows-render-surface-stale")
        self.host.show_pointer_window(pointer_window_spec(command))

    def clear_pointer(self, *, reason: str) -> None:
        assert self.host is not None
        self.host.destroy_pointer_window(reason=reason)


class CtypesWindowsVirtualDesktopProbe:
    """Read Windows display topology under a temporary PMv2 thread context."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("the ctypes Windows pointer host requires Windows")

    def snapshot(self) -> WindowsVirtualDesktopSnapshot:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_dpi = user32.SetThreadDpiAwarenessContext
        set_dpi.argtypes = [wintypes.HANDLE]
        set_dpi.restype = wintypes.HANDLE
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        previous = set_dpi(ctypes.c_void_p(PER_MONITOR_AWARE_V2))
        if not previous:
            raise RuntimeError("SetThreadDpiAwarenessContext(PMv2) failed")

        try:
            left = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
            top = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
            width = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
            height = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
            monitors: list[tuple[int, int, int, int]] = []
            monitor_enum = ctypes.WINFUNCTYPE(
                wintypes.BOOL,
                wintypes.HMONITOR,
                wintypes.HDC,
                ctypes.POINTER(wintypes.RECT),
                wintypes.LPARAM,
            )

            @monitor_enum
            def _collect(_monitor, _hdc, rect, _data):
                value = rect.contents
                monitors.append(
                    (int(value.left), int(value.top), int(value.right), int(value.bottom))
                )
                return True

            user32.EnumDisplayMonitors.argtypes = [
                wintypes.HDC,
                ctypes.POINTER(wintypes.RECT),
                monitor_enum,
                wintypes.LPARAM,
            ]
            user32.EnumDisplayMonitors.restype = wintypes.BOOL
            if not user32.EnumDisplayMonitors(None, None, _collect, 0):
                raise RuntimeError("EnumDisplayMonitors failed")
            return WindowsVirtualDesktopSnapshot(
                left=left,
                top=top,
                width=width,
                height=height,
                monitor_rects=tuple(monitors),
            )
        finally:
            set_dpi(previous)


class CtypesWindowsPointerWindowHost:
    """Actual Win32 popup host; construction itself performs no GUI action."""

    def __init__(
        self,
        *,
        probe: WindowsVirtualDesktopProbe | None = None,
        startup_timeout_seconds: float = 2.0,
        shutdown_timeout_seconds: float = 2.0,
    ) -> None:
        if os.name != "nt":
            raise OSError("the ctypes Windows pointer host requires Windows")
        if startup_timeout_seconds <= 0 or shutdown_timeout_seconds <= 0:
            raise ValueError("window timeouts must be positive")
        self._tracker = WindowsPointerSurfaceTracker(
            probe=probe or CtypesWindowsVirtualDesktopProbe()
        )
        self._startup_timeout_seconds = float(startup_timeout_seconds)
        self._shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop: threading.Event | None = None
        self._error: BaseException | None = None

    def current_surface(self) -> PointerRenderSurface:
        return self._tracker.current_surface()

    def show_pointer_window(self, spec: WindowsPointerWindowSpec) -> None:
        surface = self.current_surface()
        if not surface_matches_command(surface, spec.command):
            self.destroy_pointer_window(reason="surface-changed-before-create")
            raise PointerOverlayContractError("windows-render-surface-stale")
        self.destroy_pointer_window(reason="replace")

        ready = threading.Event()
        stop = threading.Event()
        with self._state_lock:
            self._error = None
            self._stop = stop
            thread = threading.Thread(
                target=self._run_window,
                args=(spec, ready, stop),
                name="oc-virtual-pointer-overlay",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        if not ready.wait(self._startup_timeout_seconds):
            self.destroy_pointer_window(reason="startup-timeout")
            raise RuntimeError("virtual pointer window startup timed out")
        with self._state_lock:
            error = self._error
        if error is not None:
            self.destroy_pointer_window(reason="startup-error")
            raise RuntimeError("virtual pointer window failed") from error

    def destroy_pointer_window(self, *, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("destroy reason is required")
        with self._state_lock:
            thread = self._thread
            stop = self._stop
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._shutdown_timeout_seconds)
            if thread.is_alive():
                raise RuntimeError("virtual pointer window did not terminate")
        with self._state_lock:
            if self._thread is thread:
                self._thread = None
                self._stop = None

    def _run_window(
        self,
        spec: WindowsPointerWindowSpec,
        ready: threading.Event,
        stop: threading.Event,
    ) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # Win32 handles are pointer-sized.  Explicit signatures are mandatory
        # on 64-bit Windows or ctypes would truncate them to c_int.
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HANDLE,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = wintypes.LPARAM
        user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.BeginPaint.restype = wintypes.HDC
        user32.EndPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetClientRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        user32.FillRect.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.HBRUSH,
        ]
        user32.DrawTextW.argtypes = [
            wintypes.HDC,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.RECT),
            wintypes.UINT,
        ]
        user32.SetLayeredWindowAttributes.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.BYTE,
            wintypes.DWORD,
        ]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UpdateWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        gdi32.CreatePen.restype = wintypes.HANDLE
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.MoveToEx.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(wintypes.POINT),
        ]
        gdi32.LineTo.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.Polyline.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            ctypes.c_int,
        ]
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        set_dpi = user32.SetThreadDpiAwarenessContext
        set_dpi.argtypes = [wintypes.HANDLE]
        set_dpi.restype = wintypes.HANDLE
        previous_dpi = set_dpi(ctypes.c_void_p(PER_MONITOR_AWARE_V2))
        if not previous_dpi:
            with self._state_lock:
                self._error = RuntimeError("window thread could not enter PMv2")
            ready.set()
            return

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        def colorref(color: tuple[int, int, int]) -> int:
            return color[0] | (color[1] << 8) | (color[2] << 16)

        black_brush = gdi32.CreateSolidBrush(0)
        label_brush = gdi32.CreateSolidBrush(colorref(spec.command.style.label_background))
        outline_pen = gdi32.CreatePen(
            0,
            spec.command.style.stroke_px + 2,
            colorref(spec.command.style.outline_color),
        )
        primary_pen = gdi32.CreatePen(
            0,
            spec.command.style.stroke_px,
            colorref(spec.command.style.primary_color),
        )
        resources = (black_brush, label_brush, outline_pen, primary_pen)
        if not all(resources):
            with self._state_lock:
                self._error = RuntimeError("GDI pointer resources could not be created")
            ready.set()
            for resource in resources:
                if resource:
                    gdi32.DeleteObject(resource)
            set_dpi(previous_dpi)
            return

        WM_ERASEBKGND = 0x0014
        WM_PAINT = 0x000F
        WM_DESTROY = 0x0002
        WM_NCHITTEST = 0x0084
        TRANSPARENT = 1
        DT_LEFT = 0x0000
        DT_SINGLELINE = 0x0020
        DT_VCENTER = 0x0004
        LWA_COLORKEY = 0x00000001
        HWND_TOPMOST = wintypes.HWND(-1)
        wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        def _draw_marker(hdc) -> None:
            x = spec.hotspot_x
            y = spec.hotspot_y
            radius = spec.command.style.radius_px

            def _stroke(pen) -> None:
                previous = gdi32.SelectObject(hdc, pen)
                gdi32.MoveToEx(hdc, x - radius - 5, y, None)
                gdi32.LineTo(hdc, x + radius + 6, y)
                gdi32.MoveToEx(hdc, x, y - radius - 5, None)
                gdi32.LineTo(hdc, x, y + radius + 6)
                points = (wintypes.POINT * 5)(
                    wintypes.POINT(x, y - radius),
                    wintypes.POINT(x + radius, y),
                    wintypes.POINT(x, y + radius),
                    wintypes.POINT(x - radius, y),
                    wintypes.POINT(x, y - radius),
                )
                gdi32.Polyline(hdc, points, 5)
                gdi32.SelectObject(hdc, previous)

            _stroke(outline_pen)
            _stroke(primary_pen)

        def _draw_label(hdc) -> None:
            marker_bottom = spec.hotspot_y * 2 + 1
            rect = wintypes.RECT(0, marker_bottom, spec.width, spec.height)
            user32.FillRect(hdc, ctypes.byref(rect), label_brush)
            gdi32.SetBkMode(hdc, TRANSPARENT)
            gdi32.SetTextColor(hdc, colorref(spec.command.style.label_text_color))
            text_rect = wintypes.RECT(8, marker_bottom, spec.width - 4, spec.height)
            user32.DrawTextW(
                hdc,
                spec.command.label,
                -1,
                ctypes.byref(text_rect),
                DT_LEFT | DT_SINGLELINE | DT_VCENTER,
            )

        @wndproc_type
        def _wnd_proc(hwnd, message, wparam, lparam):
            if message == WM_NCHITTEST:
                return HTTRANSPARENT
            if message == WM_ERASEBKGND:
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                user32.FillRect(wparam, ctypes.byref(rect), black_brush)
                return 1
            if message == WM_PAINT:
                paint = ctypes.create_string_buffer(72)
                hdc = user32.BeginPaint(hwnd, paint)
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                user32.FillRect(hdc, ctypes.byref(rect), black_brush)
                _draw_marker(hdc)
                _draw_label(hdc)
                user32.EndPaint(hwnd, paint)
                return 0
            if message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        instance = kernel32.GetModuleHandleW(None)
        class_name = f"OCVirtualPointer_{id(self) & 0xFFFFFFFF:x}_{threading.get_ident()}"
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = ctypes.cast(_wnd_proc, ctypes.c_void_p).value
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        window_class.hbrBackground = black_brush
        atom = user32.RegisterClassW(ctypes.byref(window_class))
        hwnd = None
        try:
            if not atom:
                raise RuntimeError("RegisterClassW failed")
            if not surface_matches_command(self.current_surface(), spec.command):
                raise PointerOverlayContractError("windows-render-surface-stale")
            hwnd = user32.CreateWindowExW(
                spec.extended_style,
                class_name,
                spec.command.label,
                spec.window_style,
                spec.left,
                spec.top,
                spec.width,
                spec.height,
                None,
                None,
                instance,
                None,
            )
            if not hwnd:
                raise RuntimeError("CreateWindowExW failed")
            if not user32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_COLORKEY):
                raise RuntimeError("SetLayeredWindowAttributes failed")
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                spec.left,
                spec.top,
                spec.width,
                spec.height,
                spec.set_position_flags,
            )
            user32.ShowWindow(hwnd, spec.show_command)
            user32.UpdateWindow(hwnd)
            ready.set()

            message = wintypes.MSG()
            while not stop.wait(0.05):
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
                if not surface_matches_command(self.current_surface(), spec.command):
                    stop.set()  # monitor/DPI topology changed: destroy fail-closed
        except BaseException as exc:  # surface native failure through host boundary
            with self._state_lock:
                self._error = exc
            ready.set()
        finally:
            if hwnd:
                user32.DestroyWindow(hwnd)
            if atom:
                user32.UnregisterClassW(class_name, instance)
            for resource in resources:
                gdi32.DeleteObject(resource)
            set_dpi(previous_dpi)
            ready.set()


__all__ = [
    "CtypesWindowsPointerWindowHost",
    "CtypesWindowsVirtualDesktopProbe",
    "HTTRANSPARENT",
    "PER_MONITOR_AWARE_V2",
    "SWP_NOACTIVATE",
    "SWP_SHOWWINDOW",
    "SW_SHOWNA",
    "VIRTUAL_POINTER_EXTENDED_STYLE",
    "WS_EX_LAYERED",
    "WS_EX_NOACTIVATE",
    "WS_EX_TOOLWINDOW",
    "WS_EX_TOPMOST",
    "WS_EX_TRANSPARENT",
    "WS_POPUP",
    "WindowsPointerSurfaceTracker",
    "WindowsPointerWindowHost",
    "WindowsPointerWindowSpec",
    "WindowsVirtualDesktopProbe",
    "WindowsVirtualDesktopSnapshot",
    "WindowsVirtualPointerOverlayRenderer",
    "pointer_window_spec",
    "surface_matches_command",
]
