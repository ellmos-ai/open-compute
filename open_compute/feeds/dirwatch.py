"""Directory-watch feed: monitors configured directories for file-system changes.

Implements ``PerceptionFeed`` (event-feed variant) — change events accumulate
in a rolling window (newest first) rather than being replaced on each observe().

Two backends, selected automatically:
- **watchdog** (MIT): native OS FS events via ``ReadDirectoryChangesW`` /
  ``inotify`` / ``FSEvents``.  Activated when ``watchdog`` is importable.
- **stdlib polling**: ``os.scandir`` + mtime-diff snapshot comparison.
  Always available; ``available()`` returns True even without watchdog.

The feed is cross-platform and has **no required dependencies**:
    import open_compute.feeds.dirwatch   # safe everywhere

Install the optional extra for native events:
    pip install open-compute[watch]

Event dict keys
---------------
Each event is a plain ``dict`` with these keys:

    name   (str)  — absolute path of the changed file/directory
    role   (str)  — event type: "created" | "modified" | "deleted" | "moved"
    src    (str)  — source path (equals name for non-move events)
    dst    (str | None) — destination path for "moved" events; None otherwise

Usage
-----
Standalone::

    from open_compute.feeds.dirwatch import DirwatchFeed

    feed = DirwatchFeed()
    feed.start(["/tmp/mydir"])
    import time; time.sleep(5)
    obs = feed.observe()
    feed.stop()

CLI (oc watch-dir) delegates here for both ``--for`` (duration) and ``--once``
(snapshot diff) modes.
"""

from __future__ import annotations

import collections
import os
import time
from typing import Deque, Iterable

from .base import FeedObservation

# Maximum number of events kept in the rolling buffer
_MAX_EVENTS: int = 200


# ---------------------------------------------------------------------------
# Pure helper: snapshot diff (testable without OS events / watchdog)
# ---------------------------------------------------------------------------

def _scan_snapshot(paths: Iterable[str]) -> dict[str, float]:
    """Scan directories and return a ``{abs_path: mtime}`` snapshot.

    Args:
        paths: Iterable of directory paths to scan (non-recursive).

    Returns:
        Dict mapping absolute file paths to their modification timestamps.
        Non-existent or inaccessible paths are silently skipped.
    """
    snap: dict[str, float] = {}
    for root in paths:
        try:
            with os.scandir(root) as it:
                for entry in it:
                    try:
                        snap[entry.path] = entry.stat().st_mtime
                    except OSError:
                        pass
        except OSError:
            pass
    return snap


def _diff_snapshots(
    old: dict[str, float],
    new: dict[str, float],
) -> list[dict]:
    """Compare two ``{path: mtime}`` snapshots and return a list of change events.

    Detection logic:
    - Path in new but not in old → "created"
    - Path in both but mtime changed → "modified"
    - Path in old but not in new → "deleted"
    - "moved" detection: match single deleted + single created with equal mtime;
      if unambiguous, emit one "moved" event instead of created+deleted.

    Args:
        old: Previous snapshot dict.
        new: Current snapshot dict.

    Returns:
        List of event dicts (newest is NOT ordered here — caller decides order).
    """
    old_paths = set(old)
    new_paths = set(new)

    created = new_paths - old_paths
    deleted = old_paths - new_paths
    common = old_paths & new_paths

    events: list[dict] = []

    # Modified
    for path in common:
        if new[path] != old[path]:
            events.append({"name": path, "role": "modified", "src": path, "dst": None})

    # Move detection: match deleted mtime to created mtime (one-to-one)
    del_by_mtime: dict[float, list[str]] = {}
    for path in deleted:
        del_by_mtime.setdefault(old[path], []).append(path)

    cre_by_mtime: dict[float, list[str]] = {}
    for path in created:
        cre_by_mtime.setdefault(new[path], []).append(path)

    moved_src: set[str] = set()
    moved_dst: set[str] = set()

    for mtime, del_list in del_by_mtime.items():
        cre_list = cre_by_mtime.get(mtime, [])
        if len(del_list) == 1 and len(cre_list) == 1:
            src, dst = del_list[0], cre_list[0]
            events.append({"name": dst, "role": "moved", "src": src, "dst": dst})
            moved_src.add(src)
            moved_dst.add(dst)

    # Remaining created / deleted (not part of a move pair)
    for path in created:
        if path not in moved_dst:
            events.append({"name": path, "role": "created", "src": path, "dst": None})

    for path in deleted:
        if path not in moved_src:
            events.append({"name": path, "role": "deleted", "src": path, "dst": None})

    return events


# ---------------------------------------------------------------------------
# watchdog backend (lazy, optional)
# ---------------------------------------------------------------------------

def _watchdog_available() -> bool:
    """Return True when the ``watchdog`` package is importable."""
    try:
        import watchdog  # noqa: F401 — probe only
        return True
    except ImportError:
        return False


class _WatchdogHandler:
    """Minimal FileSystemEventHandler that appends to a shared deque."""

    def __init__(self, buf: Deque[dict]) -> None:
        self._buf = buf

    def _push(self, event_type: str, src: str, dst: str | None = None) -> None:
        entry = {
            "name": dst if dst else src,
            "role": event_type,
            "src": src,
            "dst": dst,
        }
        self._buf.appendleft(entry)

    # watchdog calls these methods on the handler
    def on_created(self, event) -> None:  # type: ignore[override]
        self._push("created", event.src_path)

    def on_modified(self, event) -> None:  # type: ignore[override]
        self._push("modified", event.src_path)

    def on_deleted(self, event) -> None:  # type: ignore[override]
        self._push("deleted", event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[override]
        self._push("moved", event.src_path, event.dest_path)


# ---------------------------------------------------------------------------
# DirwatchFeed
# ---------------------------------------------------------------------------

class DirwatchFeed:
    """PerceptionFeed that monitors directories and emits file-system change events.

    Implements the ``PerceptionFeed`` protocol:
    - ``name``: ``"dirwatch"``
    - ``available()``: always ``True`` (stdlib polling is always usable)
    - ``observe(window=None)``: returns a ``FeedObservation`` with current events

    The feed must be started before events are collected and stopped afterward::

        feed = DirwatchFeed()
        feed.start(["/some/dir"])
        time.sleep(3)
        obs = feed.observe()   # newest events first
        feed.stop()

    For a one-time snapshot diff (no background observer), use
    :meth:`snapshot_diff` directly.

    Args:
        max_events: Maximum number of events kept in the rolling buffer.
    """

    name: str = "dirwatch"

    def __init__(self, max_events: int = _MAX_EVENTS) -> None:
        self._max_events = max_events
        self._buf: Deque[dict] = collections.deque(maxlen=max_events)
        self._watching_paths: list[str] = []
        # watchdog state
        self._observer = None
        # polling state
        self._poll_snapshot: dict[str, float] = {}

    # ------------------------------------------------------------------
    # PerceptionFeed protocol
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Always True — stdlib polling is available on every platform."""
        return True

    def observe(self, window: str | None = None) -> FeedObservation:
        """Return a FeedObservation containing current change events.

        If a polling backend is active (watchdog unavailable or not started
        yet), this also triggers a poll cycle to pick up new changes.

        The ``elements`` list contains event dicts, newest first.

        Args:
            window: Unused (event feeds are not window-scoped). Accepted for
                    protocol compatibility.

        Returns:
            FeedObservation(kind="dirwatch", elements=[...], text=None, ts=...)
        """
        # If using polling backend, do a sync poll cycle now
        if self._observer is None and self._watching_paths:
            self._poll_once()

        events = list(self._buf)  # already newest-first (deque appendleft)
        return FeedObservation(
            kind="dirwatch",
            elements=events,
            text=None,
            ts=time.time(),
        )

    # ------------------------------------------------------------------
    # Lifecycle: start / stop
    # ------------------------------------------------------------------

    def start(self, paths: list[str]) -> None:
        """Begin watching the given directories for changes.

        Selects the watchdog backend if available; falls back to polling.
        Polling requires explicit ``observe()`` calls to discover changes.

        Args:
            paths: List of directory paths to monitor.
        """
        self._watching_paths = [os.path.abspath(p) for p in paths]
        self._buf.clear()

        if _watchdog_available():
            self._start_watchdog()
        else:
            # Initialize polling baseline
            self._poll_snapshot = _scan_snapshot(self._watching_paths)

    def stop(self) -> None:
        """Stop watching and release resources."""
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            self._observer = None

    # ------------------------------------------------------------------
    # Snapshot diff (one-shot, no background observer)
    # ------------------------------------------------------------------

    def snapshot_diff(self, paths: list[str], baseline: dict[str, float] | None = None) -> tuple[list[dict], dict[str, float]]:
        """Compute a one-time diff of directory state against a baseline snapshot.

        Does NOT start any background observer. Useful for ``oc watch-dir --once``.

        Args:
            paths:    Directories to scan.
            baseline: Previous snapshot to compare against.  If ``None``,
                      returns the current snapshot with an empty diff (first run).

        Returns:
            ``(events, new_snapshot)`` — event list (newest first) and the
            snapshot dict that should be stored for the next call.
        """
        abs_paths = [os.path.abspath(p) for p in paths]
        new_snap = _scan_snapshot(abs_paths)
        if baseline is None:
            return [], new_snap
        events = _diff_snapshots(baseline, new_snap)
        events.reverse()  # newest first (diff returns in arbitrary order)
        return events, new_snap

    # ------------------------------------------------------------------
    # Internal: watchdog backend
    # ------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            handler_cls = type(
                "_Handler",
                (FileSystemEventHandler,),
                {
                    "__init__": lambda self, buf: setattr(self, "_delegate", _WatchdogHandler(buf)),
                    "on_created": lambda self, e: self._delegate.on_created(e),
                    "on_modified": lambda self, e: self._delegate.on_modified(e),
                    "on_deleted": lambda self, e: self._delegate.on_deleted(e),
                    "on_moved": lambda self, e: self._delegate.on_moved(e),
                },
            )

            observer = Observer()
            for path in self._watching_paths:
                handler = handler_cls(self._buf)
                observer.schedule(handler, path, recursive=False)
            observer.start()
            self._observer = observer
        except Exception:  # noqa: BLE001
            # Fallback to polling if watchdog start fails for any reason
            self._observer = None
            self._poll_snapshot = _scan_snapshot(self._watching_paths)

    # ------------------------------------------------------------------
    # Internal: polling backend
    # ------------------------------------------------------------------

    def _poll_once(self) -> None:
        """Run one poll cycle: scan → diff → push new events into the buffer."""
        new_snap = _scan_snapshot(self._watching_paths)
        events = _diff_snapshots(self._poll_snapshot, new_snap)
        for ev in events:
            self._buf.appendleft(ev)
        self._poll_snapshot = new_snap
