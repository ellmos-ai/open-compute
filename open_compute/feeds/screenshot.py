"""Screenshot feed — wraps LocalExecutor.screenshot() as a PerceptionFeed.

This is the pixel-level feed: it captures the full virtual desktop as a PNG
and exposes it via the ``PerceptionFeed`` protocol so that the registry treats
screenshot access and UIA tree access uniformly.

The feed is always ``available()`` on Windows (where mss + LocalExecutor work);
on other platforms it is unavailable (graceful degradation).

No third-party imports at module level — mss is imported lazily inside
``observe()``, consistent with LocalExecutor's own lazy import strategy.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from .base import FeedObservation


class ScreenshotFeed:
    """Pixel feed: full virtual-desktop screenshot as a ``PerceptionFeed``.

    ``elements`` in the returned ``FeedObservation`` is always empty (pixel
    feeds carry no named elements).  The raw PNG bytes are stored under the
    ``"png_bytes"`` key in ``elements[0]`` as a lightweight envelope, OR you
    can access them via the ``raw_png`` attribute of the observation stored
    by the last call (not thread-safe; for single-agent use).

    For callers that need raw pixels, use ``last_png`` after calling
    ``observe()``, or read ``elements[0]["png_bytes"]`` from the returned
    ``FeedObservation``.
    """

    name: str = "screenshot"

    def __init__(self, monitor_index: int = 0) -> None:
        self._monitor_index = monitor_index
        self._last_png: bytes | None = None

    @property
    def last_png(self) -> bytes | None:
        """Raw PNG bytes from the most recent ``observe()`` call."""
        return self._last_png

    def available(self) -> bool:
        """Return ``True`` on Windows when mss can be imported."""
        if sys.platform != "win32":
            return False
        try:
            import mss  # noqa: F401 — lazy probe only
            return True
        except ImportError:
            return False

    def observe(self, window: str | None = None) -> FeedObservation:
        """Capture a screenshot and return a ``FeedObservation``.

        ``window`` is accepted for protocol compatibility but ignored —
        this feed always captures the full virtual desktop.

        Raises:
            ImportError: if ``mss`` is not installed
                (``pip install open-compute[local]``).
            RuntimeError: on non-Windows platforms.
        """
        if sys.platform != "win32":
            raise RuntimeError(
                "ScreenshotFeed is Windows-only. "
                "Install open-compute[local] on Windows."
            )

        # Lazy import — consistent with LocalExecutor
        try:
            from open_compute.drivers.local import LocalExecutor
        except ImportError as exc:
            raise ImportError(
                "ScreenshotFeed requires 'mss'. "
                "Install with: pip install open-compute[local]"
            ) from exc

        executor = LocalExecutor(monitor_index=self._monitor_index)
        obs = executor.screenshot()
        self._last_png = obs.screenshot

        # Carry PNG bytes in elements[0] so callers have a uniform dict interface
        elements: list[dict[str, Any]] = []
        if obs.screenshot:
            elements.append({
                "name": "screenshot",
                "role": "pixel_frame",
                "width": obs.width,
                "height": obs.height,
                "png_bytes": obs.screenshot,
            })

        return FeedObservation(
            kind="screenshot",
            elements=elements,
            text=None,
            ts=time.time(),
        )
