"""Feed registry: capability detection + feed list.

``available_feeds()`` returns the list of ``PerceptionFeed`` instances that
are operational in the current environment.  It degrades gracefully:

- Always: ``ScreenshotFeed`` (if mss installed, else excluded).
- Always: ``DirwatchFeed`` (stdlib polling backend; watchdog used when installed).
- Windows + uiautomation installed: ``UiaWindowsFeed`` included.
- Otherwise: only feeds whose ``available()`` returns True are listed.

Import is safe without any optional extras (uiautomation, mss, watchdog).
"""

from __future__ import annotations

import sys
from typing import Any


def available_feeds(
    *,
    uia_max_depth: int | None = None,
    uia_max_elem: int | None = None,
) -> list[Any]:
    """Return a list of ``PerceptionFeed`` instances available right now.

    Instantiates each candidate feed and calls ``available()``; only those
    that return ``True`` are included.

    Args:
        uia_max_depth: Forwarded to ``UiaWindowsFeed`` constructor.
        uia_max_elem:  Forwarded to ``UiaWindowsFeed`` constructor.

    Returns:
        Ordered list of ready ``PerceptionFeed`` instances.
        Empty list when no feeds are available (rare — would require non-Windows
        with mss absent).
    """
    feeds: list[Any] = []

    # --- Screenshot feed (Pixel) ---
    try:
        from open_compute.feeds.screenshot import ScreenshotFeed
        sf = ScreenshotFeed()
        if sf.available():
            feeds.append(sf)
    except Exception:  # noqa: BLE001
        pass

    # --- Dirwatch feed (File-system events; always available via polling) ---
    try:
        from open_compute.feeds.dirwatch import DirwatchFeed
        dw = DirwatchFeed()
        if dw.available():
            feeds.append(dw)
    except Exception:  # noqa: BLE001
        pass

    # --- UIA feed (Windows Element Tree) ---
    if sys.platform == "win32":
        try:
            from open_compute.feeds.uia_windows import UiaWindowsFeed
            kwargs: dict[str, Any] = {}
            if uia_max_depth is not None:
                kwargs["max_depth"] = uia_max_depth
            if uia_max_elem is not None:
                kwargs["max_elem"] = uia_max_elem
            uf = UiaWindowsFeed(**kwargs)
            if uf.available():
                feeds.append(uf)
        except Exception:  # noqa: BLE001
            pass

    return feeds


def feed_names() -> list[str]:
    """Return the names of all available feeds (lightweight — calls available() only)."""
    names: list[str] = []

    try:
        from open_compute.feeds.screenshot import ScreenshotFeed
        sf = ScreenshotFeed()
        if sf.available():
            names.append(sf.name)
    except Exception:  # noqa: BLE001
        pass

    try:
        from open_compute.feeds.dirwatch import DirwatchFeed
        dw = DirwatchFeed()
        if dw.available():
            names.append(dw.name)
    except Exception:  # noqa: BLE001
        pass

    if sys.platform == "win32":
        try:
            from open_compute.feeds.uia_windows import UiaWindowsFeed
            uf = UiaWindowsFeed()
            if uf.available():
                names.append(uf.name)
        except Exception:  # noqa: BLE001
            pass

    return names
