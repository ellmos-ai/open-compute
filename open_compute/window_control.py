"""Unambiguous, injectable window-management primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol


class WindowNotFoundError(LookupError):
    pass


class WindowAmbiguousError(LookupError):
    def __init__(self, message: str, candidates: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.candidates = candidates


class WindowAdapter(Protocol):
    def list_windows(self) -> list[dict[str, Any]]: ...

    def show(self, hwnd: int, operation: str) -> None: ...

    def move(self, hwnd: int, left: int, top: int, width: int, height: int) -> None: ...


def resolve_window(
    windows: Iterable[dict[str, Any]],
    *,
    title: str | None = None,
    hwnd: int | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    """Resolve exactly one top-level window; never pick the first ambiguity."""

    items = [dict(item) for item in windows]
    selectors = sum(value is not None for value in (title, hwnd, pid))
    if selectors != 1:
        raise ValueError("provide exactly one of title, hwnd, or pid")
    if hwnd is not None:
        matches = [item for item in items if int(item.get("hwnd", -1)) == hwnd]
    elif pid is not None:
        matches = [item for item in items if int(item.get("pid", -1)) == pid]
    else:
        query = (title or "").strip().casefold()
        if not query:
            raise ValueError("title must not be empty")
        matches = [
            item
            for item in items
            if query in str(item.get("title", "")).casefold()
        ]
    if not matches:
        raise WindowNotFoundError("No window matches the supplied selector")
    if len(matches) > 1:
        raise WindowAmbiguousError(
            f"Window selector is ambiguous ({len(matches)} candidates)",
            matches,
        )
    return matches[0]


Authorize = Callable[[str], tuple[bool, str]]


@dataclass
class WindowController:
    adapter: WindowAdapter

    def apply(
        self,
        *,
        operation: str,
        authorize: Authorize,
        title: str | None = None,
        hwnd: int | None = None,
        pid: int | None = None,
        rect: tuple[int, int, int, int] | None = None,
    ) -> dict[str, Any]:
        window = resolve_window(
            self.adapter.list_windows(), title=title, hwnd=hwnd, pid=pid
        )
        target_hwnd = int(window["hwnd"])
        allowed, reason = authorize(f"window:{target_hwnd}")
        if not allowed:
            raise PermissionError(reason)
        if operation in {"minimize", "maximize", "restore", "activate"}:
            self.adapter.show(target_hwnd, operation)
        elif operation in {"move", "resize"}:
            if rect is None or len(rect) != 4:
                raise ValueError(f"{operation} requires rect=(left, top, width, height)")
            left, top, width, height = (int(value) for value in rect)
            if width <= 0 or height <= 0:
                raise ValueError("window width and height must be positive")
            self.adapter.move(target_hwnd, left, top, width, height)
        else:
            raise ValueError(
                "operation must be activate|minimize|maximize|restore|move|resize"
            )
        return {
            "operation": operation,
            "hwnd": target_hwnd,
            "title": str(window.get("title", "")),
        }


class Win32WindowAdapter:
    """Thin Windows adapter; import-safe on non-Windows for mocked tests."""

    _SHOW = {
        "minimize": 6,  # SW_MINIMIZE
        "maximize": 3,  # SW_MAXIMIZE
        "restore": 9,  # SW_RESTORE
        "activate": 9,
    }

    def list_windows(self) -> list[dict[str, Any]]:
        from .drivers.local import list_windows

        return list_windows()

    def show(self, hwnd: int, operation: str) -> None:
        import ctypes
        import sys

        if sys.platform != "win32":
            raise RuntimeError("window mutation is Windows-only")
        if operation not in self._SHOW:
            raise ValueError(f"unsupported show operation: {operation}")
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            raise WindowNotFoundError(f"window handle {hwnd} is no longer valid")
        user32.ShowWindow(hwnd, self._SHOW[operation])
        if operation == "activate":
            if not user32.SetForegroundWindow(hwnd):
                raise RuntimeError(f"could not activate window handle {hwnd}")

    def move(
        self, hwnd: int, left: int, top: int, width: int, height: int
    ) -> None:
        import ctypes
        import sys

        if sys.platform != "win32":
            raise RuntimeError("window mutation is Windows-only")
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            raise WindowNotFoundError(f"window handle {hwnd} is no longer valid")
        if not user32.MoveWindow(hwnd, left, top, width, height, True):
            raise RuntimeError(f"could not move/resize window handle {hwnd}")

