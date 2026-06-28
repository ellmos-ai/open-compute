"""Cross-platform capture via pynput (opt-in: pip install open-compute[record])."""

from __future__ import annotations

import queue
import time

from .base import RawEvent


class PynputCaptureBackend:
    def __init__(self):
        self._q: "queue.Queue[RawEvent]" = queue.Queue()
        self._paused = False
        self._t0 = 0.0
        self._ml = None
        self._kl = None

    def available(self) -> bool:
        try:
            import pynput  # noqa: F401
            return True
        except Exception:
            return False

    def start(self) -> None:
        from pynput import mouse, keyboard
        self._t0 = time.monotonic()

        def on_click(x, y, button, pressed):
            if self._paused:
                return
            kind = "mouse_down" if pressed else "mouse_up"
            self._q.put(RawEvent(kind, time.monotonic() - self._t0,
                                 x=int(x), y=int(y), button=button.name))

        def on_scroll(x, y, dx, dy):
            if self._paused:
                return
            self._q.put(RawEvent("wheel", time.monotonic() - self._t0,
                                 x=int(x), y=int(y), delta=int(dy)))

        def on_press(key):
            if self._paused:
                return
            ch = getattr(key, "char", None)
            if ch:
                self._q.put(RawEvent("char", time.monotonic() - self._t0, char=ch))
            else:
                name = getattr(key, "name", str(key))
                self._q.put(RawEvent("key_down", time.monotonic() - self._t0, key=name))

        self._ml = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
        self._kl = keyboard.Listener(on_press=on_press)
        self._ml.start()
        self._kl.start()

    def stop(self) -> None:
        for l in (self._ml, self._kl):
            if l is not None:
                l.stop()

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
