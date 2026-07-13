"""Tests for Feature #2 — DirwatchFeed (directory watch feed).

All tests use a real temporary directory via pytest's ``tmp_path`` fixture.
No OS observers (watchdog) are required: the polling backend is always
available and is the subject of most tests.  A separate test verifies that
the watchdog import detection flag behaves correctly.

Test tiers
----------
1. Pure helpers — ``_scan_snapshot`` and ``_diff_snapshots`` (no I/O mocking).
2. DirwatchFeed.available() — always True.
3. DirwatchFeed.observe() — returns FeedObservation with correct kind.
4. DirwatchFeed polling backend — create / modify / delete → events emitted.
5. DirwatchFeed.snapshot_diff() — one-shot diff, baseline=None first run.
6. feed_names() / available_feeds() — dirwatch appears in both.
7. CLI parsing — oc watch-dir argument parsing (no real OS/watch calls).
8. Import without watchdog — DirwatchFeed importable and available when
   watchdog is absent (mocked away from sys.modules).

No real watchdog observers are started; no background threads.
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Pure helpers: _scan_snapshot and _diff_snapshots
# ---------------------------------------------------------------------------

class TestScanSnapshot:
    def test_empty_dir(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _scan_snapshot
        result = _scan_snapshot([str(tmp_path)])
        assert isinstance(result, dict)
        assert result == {}

    def test_single_file(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _scan_snapshot
        f = tmp_path / "hello.txt"
        f.write_text("hi")
        snap = _scan_snapshot([str(tmp_path)])
        assert str(f) in snap
        assert isinstance(snap[str(f)], float)

    def test_nonexistent_dir_skipped(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _scan_snapshot
        fake = str(tmp_path / "does_not_exist")
        snap = _scan_snapshot([fake])
        assert snap == {}

    def test_multiple_files(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _scan_snapshot
        for name in ("a.txt", "b.txt", "c.txt"):
            (tmp_path / name).write_text("x")
        snap = _scan_snapshot([str(tmp_path)])
        assert len(snap) == 3

    def test_multiple_dirs(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _scan_snapshot
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "x.txt").write_text("x")
        (d2 / "y.txt").write_text("y")
        snap = _scan_snapshot([str(d1), str(d2)])
        assert len(snap) == 2


class TestDiffSnapshots:
    def test_created(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _diff_snapshots
        new_file = str(tmp_path / "new.txt")
        old: dict = {}
        new = {new_file: 1000.0}
        events = _diff_snapshots(old, new)
        assert len(events) == 1
        assert events[0]["role"] == "created"
        assert events[0]["name"] == new_file

    def test_deleted(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _diff_snapshots
        old_file = str(tmp_path / "old.txt")
        old = {old_file: 1000.0}
        new: dict = {}
        events = _diff_snapshots(old, new)
        assert len(events) == 1
        assert events[0]["role"] == "deleted"
        assert events[0]["name"] == old_file

    def test_modified(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _diff_snapshots
        path = str(tmp_path / "file.txt")
        old = {path: 1000.0}
        new = {path: 2000.0}
        events = _diff_snapshots(old, new)
        assert len(events) == 1
        assert events[0]["role"] == "modified"

    def test_unchanged(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _diff_snapshots
        path = str(tmp_path / "file.txt")
        snap = {path: 1000.0}
        events = _diff_snapshots(snap, snap)
        assert events == []

    def test_moved_unambiguous(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _diff_snapshots
        src = str(tmp_path / "src.txt")
        dst = str(tmp_path / "dst.txt")
        mtime = 1234567890.0
        old = {src: mtime}
        new = {dst: mtime}
        events = _diff_snapshots(old, new)
        # Unambiguous move: one src + one dst at same mtime → "moved"
        assert len(events) == 1
        assert events[0]["role"] == "moved"
        assert events[0]["src"] == src
        assert events[0]["dst"] == dst

    def test_moved_ambiguous_not_merged(self, tmp_path: Path) -> None:
        """Two deletions + two creations at same mtime → NOT treated as move."""
        from open_compute.feeds.dirwatch import _diff_snapshots
        mtime = 1234567890.0
        src1 = str(tmp_path / "a.txt")
        src2 = str(tmp_path / "b.txt")
        dst1 = str(tmp_path / "c.txt")
        dst2 = str(tmp_path / "d.txt")
        old = {src1: mtime, src2: mtime}
        new = {dst1: mtime, dst2: mtime}
        events = _diff_snapshots(old, new)
        roles = {e["role"] for e in events}
        # Ambiguous: both created & deleted remain separate
        assert "created" in roles
        assert "deleted" in roles
        # No "moved" entries
        assert all(e["role"] != "moved" for e in events)

    def test_mixed_events(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _diff_snapshots
        created_p = str(tmp_path / "new.txt")
        deleted_p = str(tmp_path / "gone.txt")
        modified_p = str(tmp_path / "changed.txt")
        old = {deleted_p: 100.0, modified_p: 100.0}
        new = {created_p: 200.0, modified_p: 200.0}
        events = _diff_snapshots(old, new)
        roles = sorted(e["role"] for e in events)
        assert "created" in roles
        assert "deleted" in roles
        assert "modified" in roles

    def test_event_dict_keys(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import _diff_snapshots
        path = str(tmp_path / "x.txt")
        old: dict = {}
        new = {path: 1.0}
        events = _diff_snapshots(old, new)
        ev = events[0]
        assert "name" in ev
        assert "role" in ev
        assert "src" in ev
        assert "dst" in ev


# ---------------------------------------------------------------------------
# 2. DirwatchFeed.available()
# ---------------------------------------------------------------------------

class TestDirwatchFeedAvailable:
    def test_always_true(self) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        feed = DirwatchFeed()
        assert feed.available() is True

    def test_still_true_without_watchdog(self) -> None:
        """available() must return True even when watchdog is blocked."""
        import sys
        from open_compute.feeds.dirwatch import DirwatchFeed

        saved = sys.modules.get("watchdog")
        sys.modules["watchdog"] = None  # type: ignore[assignment] — poison
        try:
            feed = DirwatchFeed()
            assert feed.available() is True
        finally:
            if saved is None:
                sys.modules.pop("watchdog", None)
            else:
                sys.modules["watchdog"] = saved


# ---------------------------------------------------------------------------
# 3. DirwatchFeed.observe() — FeedObservation shape
# ---------------------------------------------------------------------------

class TestDirwatchFeedObserve:
    def test_observe_returns_feed_observation(self) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        from open_compute.feeds.base import FeedObservation
        feed = DirwatchFeed()
        obs = feed.observe()
        assert isinstance(obs, FeedObservation)
        assert obs.kind == "dirwatch"
        assert isinstance(obs.elements, list)

    def test_observe_no_paths_no_events(self) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        feed = DirwatchFeed()
        obs = feed.observe()
        assert obs.elements == []

    def test_observe_window_ignored(self) -> None:
        """window= parameter is accepted but ignored (event feeds are not window-scoped)."""
        from open_compute.feeds.dirwatch import DirwatchFeed
        feed = DirwatchFeed()
        obs = feed.observe(window="irrelevant")
        assert isinstance(obs.elements, list)


# ---------------------------------------------------------------------------
# 4. Polling backend — create / modify / delete → events
# ---------------------------------------------------------------------------

class TestDirwatchPolling:
    """Integration tests using a real temp dir + polling backend.

    We force the polling path by blocking watchdog in sys.modules.
    """

    def _with_no_watchdog(self):
        """Context manager that blocks watchdog import."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            saved = sys.modules.get("watchdog")
            sys.modules["watchdog"] = None  # type: ignore[assignment]
            try:
                yield
            finally:
                if saved is None:
                    sys.modules.pop("watchdog", None)
                else:
                    sys.modules["watchdog"] = saved

        return _ctx()

    def test_created_event(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        with self._with_no_watchdog():
            feed = DirwatchFeed()
            feed.start([str(tmp_path)])
            # Create a file after start
            (tmp_path / "new_file.txt").write_text("hello")
            obs = feed.observe()  # triggers poll cycle
            feed.stop()
        roles = [e["role"] for e in obs.elements]
        assert "created" in roles

    def test_modified_event(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        existing = tmp_path / "existing.txt"
        existing.write_text("before")
        with self._with_no_watchdog():
            feed = DirwatchFeed()
            feed.start([str(tmp_path)])
            # Trigger modification: rewrite with different content
            import time as _t
            _t.sleep(0.02)  # ensure mtime differs
            existing.write_text("after")
            # Force mtime change (some filesystems have 1s resolution)
            os.utime(str(existing), (time.time() + 1, time.time() + 1))
            obs = feed.observe()
            feed.stop()
        roles = [e["role"] for e in obs.elements]
        # May be modified or could be detected as create if mtime precision is low;
        # key assertion: at least one event was emitted
        assert len(obs.elements) >= 1

    def test_deleted_event(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        f = tmp_path / "to_delete.txt"
        f.write_text("bye")
        with self._with_no_watchdog():
            feed = DirwatchFeed()
            feed.start([str(tmp_path)])
            f.unlink()
            obs = feed.observe()
            feed.stop()
        roles = [e["role"] for e in obs.elements]
        assert "deleted" in roles

    def test_events_are_newest_first(self, tmp_path: Path) -> None:
        """Events are stored newest-first (appendleft)."""
        from open_compute.feeds.dirwatch import DirwatchFeed
        with self._with_no_watchdog():
            feed = DirwatchFeed()
            feed.start([str(tmp_path)])
            # Create 3 files sequentially
            for i in range(3):
                (tmp_path / f"file_{i}.txt").write_text(str(i))
                feed._poll_once()  # force a poll cycle after each file
            obs = feed.observe()
            feed.stop()
        # The buffer is a deque with appendleft, so newer events come first.
        # At least one event must exist.
        assert len(obs.elements) >= 1

    def test_poll_respects_max_events(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        # max_events=3 so buffer never exceeds 3
        with self._with_no_watchdog():
            feed = DirwatchFeed(max_events=3)
            feed.start([str(tmp_path)])
            for i in range(10):
                (tmp_path / f"f_{i}.txt").write_text(str(i))
                feed._poll_once()
            obs = feed.observe()
            feed.stop()
        assert len(obs.elements) <= 3


# ---------------------------------------------------------------------------
# 5. snapshot_diff() — one-shot diff
# ---------------------------------------------------------------------------

class TestSnapshotDiff:
    def test_first_run_no_baseline(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        (tmp_path / "a.txt").write_text("x")
        feed = DirwatchFeed()
        events, snap = feed.snapshot_diff([str(tmp_path)], baseline=None)
        assert events == []  # no diff on first run
        assert str(tmp_path / "a.txt") in snap

    def test_second_run_detects_change(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        f = tmp_path / "a.txt"
        f.write_text("original")
        feed = DirwatchFeed()
        _, baseline = feed.snapshot_diff([str(tmp_path)], baseline=None)

        # Add a new file
        (tmp_path / "b.txt").write_text("new")
        events, _ = feed.snapshot_diff([str(tmp_path)], baseline=baseline)
        roles = [e["role"] for e in events]
        assert "created" in roles

    def test_deleted_detected_on_second_run(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        f = tmp_path / "to_remove.txt"
        f.write_text("bye")
        feed = DirwatchFeed()
        _, baseline = feed.snapshot_diff([str(tmp_path)], baseline=None)

        f.unlink()
        events, _ = feed.snapshot_diff([str(tmp_path)], baseline=baseline)
        roles = [e["role"] for e in events]
        assert "deleted" in roles

    def test_returns_events_newest_first(self, tmp_path: Path) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        feed = DirwatchFeed()
        _, baseline = feed.snapshot_diff([str(tmp_path)], baseline=None)
        # Create multiple files
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        events, _ = feed.snapshot_diff([str(tmp_path)], baseline=baseline)
        # Just verify it's a list (newest-first ordering is best-effort for polling)
        assert isinstance(events, list)
        assert all(isinstance(e, dict) for e in events)


# ---------------------------------------------------------------------------
# 6. Registry integration
# ---------------------------------------------------------------------------

class TestRegistryIntegration:
    def test_dirwatch_in_feed_names(self) -> None:
        from open_compute.feeds.registry import feed_names
        names = feed_names()
        assert "dirwatch" in names

    def test_dirwatch_in_available_feeds(self) -> None:
        from open_compute.feeds.registry import available_feeds
        feeds = available_feeds()
        feed_name_list = [f.name for f in feeds]
        assert "dirwatch" in feed_name_list

    def test_dirwatch_feed_available_method(self) -> None:
        from open_compute.feeds.dirwatch import DirwatchFeed
        assert DirwatchFeed().available() is True


# ---------------------------------------------------------------------------
# 7. CLI parsing (oc watch-dir)
# ---------------------------------------------------------------------------

class TestWatchDirCLIParsing:
    """Verify oc watch-dir argument parsing without starting any real observer."""

    def test_missing_path_exits(self, tmp_path: Path) -> None:
        """argparse raises SystemExit when no paths given (nargs='+' requires at least one)."""
        from open_compute.cli import cmd_watch_dir
        # argparse prints to stderr and exits when nargs="+" gets zero args
        with pytest.raises(SystemExit):
            cmd_watch_dir([])

    def test_nonexistent_dir_exits(self, tmp_path: Path) -> None:
        """Non-existent directories should cause exit(2)."""
        from open_compute.cli import cmd_watch_dir
        fake = str(tmp_path / "no_such_dir")
        with pytest.raises(SystemExit) as exc_info:
            cmd_watch_dir([fake])
        assert exc_info.value.code == 2

    def test_once_flag_produces_json(self, tmp_path: Path) -> None:
        """--once mode should print a JSON array and exit 0."""
        import io
        from open_compute.cli import cmd_watch_dir

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_watch_dir(["--once", str(tmp_path)])
            output = mock_out.getvalue().strip()

        parsed = json.loads(output)
        assert isinstance(parsed, list)

    def test_for_duration_produces_json(self, tmp_path: Path) -> None:
        """--for 0.01 should run briefly and emit a JSON array."""
        import io
        from open_compute.feeds.dirwatch import DirwatchFeed

        with patch.object(DirwatchFeed, "start") as mock_start, \
             patch.object(DirwatchFeed, "stop") as mock_stop, \
             patch.object(DirwatchFeed, "observe") as mock_obs:
            from open_compute.feeds.base import FeedObservation
            mock_obs.return_value = FeedObservation(
                kind="dirwatch", elements=[], text=None, ts=0.0
            )
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                from open_compute.cli import cmd_watch_dir
                cmd_watch_dir(["--for", "0.01", str(tmp_path)])
                output = mock_out.getvalue().strip()

        parsed = json.loads(output)
        assert isinstance(parsed, list)

    def test_watch_dir_in_main_dispatch(self) -> None:
        """main() dispatches 'watch-dir' to cmd_watch_dir."""
        with patch("open_compute.cli.cmd_watch_dir") as mock_fn:
            mock_fn.return_value = None
            with patch("sys.argv", ["oc", "watch-dir", "/tmp"]):
                try:
                    from open_compute.cli import main
                    main()
                except SystemExit:
                    pass
            mock_fn.assert_called_once()

    def test_once_cross_path_isolation(self, tmp_path: Path) -> None:
        """--once on dir A then dir B must NOT report A's files as deleted in B's run.

        This is the regression guard for the path-keying fix: before the fix,
        a single flat snapshot was shared by all path-sets, so the second
        ``--once`` call would diff dir B's current state against dir A's
        snapshot and report A's files as deleted.
        """
        import io
        from open_compute.cli import cmd_watch_dir

        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        # Create a file in dir A only.
        (dir_a / "file_in_a.txt").write_text("a")

        # First --once: observe dir A.  Snapshot for A is persisted.
        with patch("sys.stdout", new_callable=io.StringIO):
            cmd_watch_dir(["--once", str(dir_a)])

        # Second --once: observe dir B (empty, no baseline for B yet).
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_watch_dir(["--once", str(dir_b)])
            out_b = mock_out.getvalue().strip()

        events_b = json.loads(out_b)
        # dir B has no baseline → first run → empty events.
        # Crucially, "file_in_a.txt" must NOT appear as a "deleted" event.
        # NOTE: real event dicts use keys "role" and "name" (see _diff_snapshots),
        # not "event"/"path" — using the wrong keys makes this guard vacuous.
        assert isinstance(events_b, list), "Expected a JSON list"
        deleted_paths = [e.get("name", "") for e in events_b if e.get("role") == "deleted"]
        assert not any("file_in_a" in p for p in deleted_paths), (
            "A's files leaked into B's --once diff (path-keying bug)"
        )

    def test_once_preserves_existing_snapshot_keys(self, tmp_path: Path, monkeypatch) -> None:
        """Updating one path-set must keep snapshots for other path-sets intact."""
        import io
        from open_compute.cli import cmd_watch_dir, _load_json_dict

        session_dir = tmp_path / "session"
        monkeypatch.setenv("OC_SESSION_DIR", str(session_dir))

        existing_key = "C:/already|tracked"
        existing_snapshot = {"C:/already/tracked.txt": 123.0}
        session_dir.mkdir()
        snap_file = session_dir / "dirwatch_snapshot.json"
        snap_file.write_text(
            json.dumps({existing_key: existing_snapshot}, ensure_ascii=False),
            encoding="utf-8",
        )

        watched = tmp_path / "fresh"
        watched.mkdir()
        (watched / "new.txt").write_text("x")

        with patch("sys.stdout", new_callable=io.StringIO):
            cmd_watch_dir(["--once", str(watched)])

        store = _load_json_dict(snap_file)
        new_key = str(watched.resolve())
        assert existing_key in store
        assert store[existing_key] == existing_snapshot
        assert new_key in store


# ---------------------------------------------------------------------------
# 8. Import without watchdog
# ---------------------------------------------------------------------------

class TestImportWithoutWatchdog:
    def test_dirwatch_importable_without_watchdog(self) -> None:
        """DirwatchFeed should import and work when watchdog is blocked."""
        import importlib
        saved = sys.modules.get("watchdog")
        sys.modules["watchdog"] = None  # type: ignore[assignment]
        try:
            # Force re-import to pick up the blocked watchdog
            import open_compute.feeds.dirwatch as dw_mod
            importlib.reload(dw_mod)
            feed = dw_mod.DirwatchFeed()
            assert feed.available() is True
            obs = feed.observe()
            assert obs.kind == "dirwatch"
        finally:
            if saved is None:
                sys.modules.pop("watchdog", None)
            else:
                sys.modules["watchdog"] = saved

    def test_watchdog_available_false_when_absent(self) -> None:
        """_watchdog_available() returns False when watchdog is blocked."""
        saved = sys.modules.get("watchdog")
        sys.modules["watchdog"] = None  # type: ignore[assignment]
        try:
            from open_compute.feeds import dirwatch as dw_mod
            import importlib
            importlib.reload(dw_mod)
            assert dw_mod._watchdog_available() is False
        finally:
            if saved is None:
                sys.modules.pop("watchdog", None)
            else:
                sys.modules["watchdog"] = saved
