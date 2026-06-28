from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class RawEvent:
    kind: str  # mouse_down|mouse_up|mouse_move|wheel|key_down|key_up|char
    t: float
    x: int | None = None
    y: int | None = None
    button: str | None = None
    key: str | None = None
    char: str | None = None
    delta: int | None = None


@runtime_checkable
class CaptureBackend(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def poll(self) -> list[RawEvent]: ...
    def set_paused(self, paused: bool) -> None: ...


def get_backend(name: str | None = None) -> CaptureBackend:
    """Select a capture backend.

    name=None  -> auto: 'winapi' on Windows, else 'pynput'.
    Explicit:   'mock' | 'winapi' | 'pynput'.
    """
    chosen = name or ("winapi" if platform.system() == "Windows" else "pynput")
    if chosen == "mock":
        from .mock import MockCaptureBackend
        return MockCaptureBackend([])
    if chosen == "winapi":
        from .winapi import WinApiCaptureBackend  # Task 9
        return WinApiCaptureBackend()
    if chosen == "pynput":
        from .pynput_backend import PynputCaptureBackend  # Task 9
        return PynputCaptureBackend()
    raise RuntimeError(f"unknown capture backend: {chosen!r}")
