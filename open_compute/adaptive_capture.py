"""Bounded, scope-aware capture series with exact-frame deduplication."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
import time
from typing import Callable


@dataclass(frozen=True)
class AdaptiveCaptureResult:
    frames: tuple[bytes, ...]
    captured_count: int
    unique_count: int
    reason: str
    scope: str
    final_digest: str


def capture_until_stable(
    capture: Callable[[], bytes],
    *,
    scope: str,
    max_frames: int = 8,
    stable_frames: int = 2,
    max_unique: int = 4,
    interval_seconds: float = 0.2,
    allow_fullscreen: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> AdaptiveCaptureResult:
    """Capture until the exact frame is unchanged or a hard limit is reached.

    ``stable_frames`` counts unchanged frames *after* the first occurrence.
    Only the last ``max_unique`` unique frames are retained, while
    ``unique_count`` reports all unique frames observed.
    """

    clean_scope = scope.strip()
    if not clean_scope:
        raise ValueError("scope must not be empty")
    if clean_scope == "fullscreen" and not allow_fullscreen:
        raise PermissionError("full-screen capture requires explicit opt-in")
    if not 1 <= max_frames <= 120:
        raise ValueError("max_frames must be in 1..120")
    if not 1 <= stable_frames < max_frames:
        raise ValueError("stable_frames must be in 1..max_frames-1")
    if not 1 <= max_unique <= 30:
        raise ValueError("max_unique must be in 1..30")
    if not 0 <= interval_seconds <= 10:
        raise ValueError("interval_seconds must be in 0..10")

    kept: deque[bytes] = deque(maxlen=max_unique)
    seen: set[str] = set()
    previous = ""
    unchanged = 0
    final_digest = ""
    captured = 0
    reason = "max_frames"

    for index in range(max_frames):
        frame = capture()
        if not isinstance(frame, bytes):
            raise TypeError("capture callback must return bytes")
        captured += 1
        final_digest = sha256(frame).hexdigest()
        if final_digest not in seen:
            seen.add(final_digest)
            kept.append(frame)
        if final_digest == previous:
            unchanged += 1
            if unchanged >= stable_frames:
                reason = "stable"
                break
        else:
            unchanged = 0
        previous = final_digest
        if index + 1 < max_frames and interval_seconds:
            sleep(interval_seconds)

    return AdaptiveCaptureResult(
        frames=tuple(kept),
        captured_count=captured,
        unique_count=len(seen),
        reason=reason,
        scope=clean_scope,
        final_digest=final_digest,
    )

