"""Pull-based recorder: drain a CaptureBackend, build a clirec Recording.

Testable without threads/real hooks: the caller drives `pump()`. The live
CLI loop calls pump() on a timer.

Pure standard library (frame/probe are injected, optional).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .capture.base import RawEvent
from .format import Recording, write
from .segment import events_to_steps


@dataclass
class RecorderConfig:
    recordings_dir: str = "recordings"
    capture_screenshots: bool = True
    mask_password_fields: bool = True
    ringbuffer_enabled: bool = False
    ringbuffer_minutes: int = 15


def _now_iso() -> str:
    # local time without importing datetime.now at module import; cheap + ok here
    import datetime
    return datetime.datetime.now().replace(microsecond=0).isoformat()


class Recorder:
    def __init__(self, backend, *, config: RecorderConfig, probe=None,
                 frame_grabber=None, host: str = "HOST", resolution: str = "0x0",
                 clock=time.monotonic):
        self.backend = backend
        self.config = config
        self.probe = probe
        self.frame_grabber = frame_grabber
        self.host = host
        self.resolution = resolution
        self._clock = clock
        self._title = ""
        self._buf: list[RawEvent] = []
        self._frames: list[bytes] = []

    def start(self, title: str) -> None:
        self._title = title
        self._buf = []
        self._frames = []
        self.backend.start()

    def set_paused(self, paused: bool) -> None:
        self.backend.set_paused(paused)

    def pump(self) -> None:
        new = self.backend.poll()
        if not new:
            return
        self._buf.extend(new)
        if self.config.capture_screenshots and self.frame_grabber is not None:
            for e in new:
                if e.kind == "mouse_down":
                    png = self.frame_grabber()
                    if png is not None:
                        self._frames.append(png)
        if self.config.ringbuffer_enabled:
            self._prune(self.config.ringbuffer_minutes)

    def _prune(self, minutes: float) -> None:
        cutoff = self._clock() - minutes * 60.0
        self._buf = [e for e in self._buf if e.t >= cutoff]

    def _build(self, events: list[RawEvent]) -> Recording:
        rel = []
        base = events[0].t if events else 0.0
        for e in events:
            rel.append(RawEvent(e.kind, e.t - base, e.x, e.y, e.button, e.key, e.char, e.delta))
        steps = events_to_steps(rel, probe=self.probe,
                                mask_passwords=self.config.mask_password_fields)
        return Recording(title=self._title, created=_now_iso(), host=self.host,
                         resolution=self.resolution, steps=steps)

    def stop(self) -> Recording:
        rec = self._build(self._buf)
        self.backend.stop()
        self._buf = []
        self._frames = []
        return rec

    def cut_last(self, minutes: float, title: str) -> Recording:
        cutoff = self._clock() - minutes * 60.0
        kept = [e for e in self._buf if e.t >= cutoff]
        self._title = title
        return self._build(kept)

    def save(self, rec: Recording, name: str) -> str:
        os.makedirs(self.config.recordings_dir, exist_ok=True)
        path = os.path.join(self.config.recordings_dir, f"{name}.clirec")
        if self._frames:
            frames_dir = path + ".frames"
            os.makedirs(frames_dir, exist_ok=True)
            for n, png in enumerate(self._frames, 1):
                with open(os.path.join(frames_dir, f"{n:04d}.png"), "wb") as fh:
                    fh.write(png)
        write(rec, path)
        return path
