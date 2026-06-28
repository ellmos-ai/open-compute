from __future__ import annotations

from .base import CaptureBackend, RawEvent


class MockCaptureBackend:
    """Deterministic capture backend for tests: replays a preset event list."""

    def __init__(self, events: list[RawEvent]):
        self._events = list(events)
        self._paused = False
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def poll(self) -> list[RawEvent]:
        if self._paused or not self._started:
            return []
        out, self._events = self._events, []
        return out
