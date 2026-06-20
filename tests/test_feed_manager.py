"""Tests for open_compute.feed_manager — v0.6.

Coverage
--------
1. InjectorSink protocol — LocalFileInjector and BachInjectorAdapter satisfy it.
2. LocalFileInjector — push + status; State vs Event write semantics.
3. BachInjectorAdapter — push renders [OC-FEEDS] block; BACH mocked + absent.
4. _hash_observation — deterministic SHA-256 hash; changes when data changes.
5. _diff_uia_elements — added/removed; empty inputs; no-change.
6. _default_dosage — known and unknown feed names.
7. FeedManager construction — DI for feeds and sink; lazy default.
8. FeedManager.set_dosage / get_dosage — valid modes; ValueError on invalid.
9. FeedManager.cycle — dosage "off" skipped; change-detection (hash skip);
   State-Feed full/delta/notify payloads; Event-Feed push; error handling.
10. FeedManager.on_demand_full — returns observation from the right feed.
11. FeedManager.status — returns feed names, dosages, feed_status, sink.
12. Import-without-extras: import open_compute.feed_manager works standalone.

All OS/file-system calls are mocked or directed to tmp directories.
No live desktop interaction, no permanent daemon.
"""

from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock feed helpers
# ---------------------------------------------------------------------------

def _make_feed_obs(kind: str = "test", elements: list | None = None, text: str | None = None):
    from open_compute.feeds.base import FeedObservation
    return FeedObservation(
        kind=kind,
        elements=elements if elements is not None else [{"name": "Btn", "role": "Button"}],
        text=text,
    )


def _make_mock_feed(name: str, obs=None, available: bool = True):
    """Build a minimal mock PerceptionFeed."""
    feed = MagicMock()
    feed.name = name
    feed.available.return_value = available
    if obs is None:
        obs = _make_feed_obs(kind=name)
    feed.observe.return_value = obs
    return feed


# ---------------------------------------------------------------------------
# 1. InjectorSink protocol
# ---------------------------------------------------------------------------

class TestInjectorSinkProtocol:
    def test_local_file_injector_satisfies_protocol(self):
        from open_compute.feed_manager import InjectorSink, LocalFileInjector
        lfi = LocalFileInjector()
        assert isinstance(lfi, InjectorSink)

    def test_bach_adapter_satisfies_protocol(self):
        from open_compute.feed_manager import BachInjectorAdapter, InjectorSink
        ba = BachInjectorAdapter()
        assert isinstance(ba, InjectorSink)


# ---------------------------------------------------------------------------
# 2. LocalFileInjector
# ---------------------------------------------------------------------------

class TestLocalFileInjector:
    def test_push_increments_count(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import LocalFileInjector
            lfi = LocalFileInjector()
            lfi.push("screenshot", {"kind": "screenshot", "hash": "abc"}, "notify")
            assert lfi.status()["push_count"] == 1

    def test_push_writes_json_file(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import LocalFileInjector
            lfi = LocalFileInjector()
            lfi.push("dirwatch", {"kind": "dirwatch", "events": [{"path": "/tmp/a"}], "_event_feed": True}, "full")
            out = tmp_path / "inject_queue" / "dirwatch.json"
            assert out.exists()

    def test_state_feed_overwrites_in_place(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import LocalFileInjector
            lfi = LocalFileInjector()
            lfi.push("screenshot", {"hash": "aaa"}, "notify")
            lfi.push("screenshot", {"hash": "bbb"}, "notify")
            out = tmp_path / "inject_queue" / "screenshot.json"
            data = json.loads(out.read_text(encoding="utf-8"))
            # Last write wins (not accumulated)
            assert data["hash"] == "bbb"

    def test_event_feed_accumulates(self, tmp_path):
        # Use a subclass that overrides _queue_dir to point at tmp_path
        from open_compute.feed_manager import LocalFileInjector
        q_dir = tmp_path / "inject_queue"
        q_dir.mkdir(parents=True, exist_ok=True)

        lfi = LocalFileInjector()
        # Monkey-patch _queue_dir to return our tmp dir
        lfi._queue_dir = lambda: q_dir

        lfi.push("dirwatch", {"_event_feed": True, "ev": 1}, "full")
        lfi.push("dirwatch", {"_event_feed": True, "ev": 2}, "full")
        out = q_dir / "dirwatch.json"
        data = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 2

    def test_event_feed_caps_at_max_events(self, tmp_path):
        from open_compute.feed_manager import LocalFileInjector
        q_dir = tmp_path / "inject_queue"
        q_dir.mkdir(parents=True, exist_ok=True)
        lfi = LocalFileInjector(max_events=3)
        lfi._queue_dir = lambda: q_dir
        for i in range(5):
            lfi.push("dirwatch", {"_event_feed": True, "ev": i}, "full")
        out = q_dir / "dirwatch.json"
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == 3

    def test_status_structure(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import LocalFileInjector
            lfi = LocalFileInjector()
            s = lfi.status()
            assert s["sink"] == "LocalFileInjector"
            assert "push_count" in s
            assert "error_count" in s


# ---------------------------------------------------------------------------
# 3. BachInjectorAdapter
# ---------------------------------------------------------------------------

class TestBachInjectorAdapter:
    def test_renders_oc_feeds_block_on_push_no_bach(self, tmp_path):
        """When BACH is not importable, adapter writes fallback file."""
        with (
            patch("open_compute.feed_manager._state_dir", return_value=tmp_path),
        ):
            from open_compute.feed_manager import BachInjectorAdapter
            adapter = BachInjectorAdapter()
            # Force BACH as unavailable
            adapter._bach_available = False
            adapter.push("screenshot", {"hash": "xyz", "dosage": "notify"}, "notify")
            fallback_file = tmp_path / "bach_fallback" / "screenshot.txt"
            assert fallback_file.exists()
            content = fallback_file.read_text(encoding="utf-8")
            assert "[OC-FEEDS]" in content

    def test_renders_notify_block(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import BachInjectorAdapter
            adapter = BachInjectorAdapter()
            adapter._bach_available = False
            adapter.push("screenshot", {"hash": "abc123"}, "notify")
            content = (tmp_path / "bach_fallback" / "screenshot.txt").read_text(encoding="utf-8")
            assert "hash=abc123" in content
            assert "[/OC-FEEDS]" in content

    def test_renders_delta_block(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import BachInjectorAdapter
            adapter = BachInjectorAdapter()
            adapter._bach_available = False
            payload = {
                "dosage": "delta",
                "added": [{"name": "X", "role": "Button"}],
                "removed": [],
            }
            adapter.push("uia_windows", payload, "delta")
            content = (tmp_path / "bach_fallback" / "uia_windows.txt").read_text(encoding="utf-8")
            assert "added=1 elements" in content

    def test_calls_bach_inject_when_available(self, tmp_path):
        """When BACH is importable, push() calls the real inject() surface."""
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import BachInjectorAdapter

            mock_injector = MagicMock()
            mock_reminder_injector_cls = MagicMock(return_value=mock_injector)
            mock_hub_mod = MagicMock()
            mock_hub_mod.ReminderInjector = mock_reminder_injector_cls

            adapter = BachInjectorAdapter()
            adapter._bach_available = True

            with patch.object(adapter, "_try_import_bach", return_value=mock_hub_mod):
                adapter.push("dirwatch", {"kind": "dirwatch", "events": []}, "full")

            mock_injector.inject.assert_called_once()

    def test_status_includes_bach_available(self):
        from open_compute.feed_manager import BachInjectorAdapter
        adapter = BachInjectorAdapter()
        s = adapter.status()
        assert s["sink"] == "BachInjectorAdapter"
        assert "bach_available" in s


# ---------------------------------------------------------------------------
# 4. _hash_observation
# ---------------------------------------------------------------------------

class TestHashObservation:
    def test_same_obs_same_hash(self):
        from open_compute.feed_manager import _hash_observation
        from open_compute.feeds.base import FeedObservation
        obs = FeedObservation(kind="uia_tree", elements=[{"name": "A", "role": "B"}])
        h1 = _hash_observation(obs)
        h2 = _hash_observation(obs)
        assert h1 == h2

    def test_different_elements_different_hash(self):
        from open_compute.feed_manager import _hash_observation
        from open_compute.feeds.base import FeedObservation
        obs1 = FeedObservation(kind="uia_tree", elements=[{"name": "A", "role": "B"}])
        obs2 = FeedObservation(kind="uia_tree", elements=[{"name": "X", "role": "Y"}])
        assert _hash_observation(obs1) != _hash_observation(obs2)

    def test_screenshot_hashes_png_bytes(self):
        from open_compute.feed_manager import _hash_observation
        from open_compute.feeds.base import FeedObservation
        obs_a = FeedObservation(kind="screenshot", elements=[{"png_bytes": b"\x00\x01"}])
        obs_b = FeedObservation(kind="screenshot", elements=[{"png_bytes": b"\x00\x02"}])
        assert _hash_observation(obs_a) != _hash_observation(obs_b)

    def test_hash_is_16_hex_chars(self):
        from open_compute.feed_manager import _hash_observation
        from open_compute.feeds.base import FeedObservation
        obs = FeedObservation(kind="uia_tree", elements=[])
        h = _hash_observation(obs)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_elements_hash_stable(self):
        from open_compute.feed_manager import _hash_observation
        from open_compute.feeds.base import FeedObservation
        obs = FeedObservation(kind="uia_tree", elements=[])
        h = _hash_observation(obs)
        assert isinstance(h, str)
        assert len(h) == 16


# ---------------------------------------------------------------------------
# 5. _diff_uia_elements
# ---------------------------------------------------------------------------

class TestDiffUiaElements:
    def test_no_change_empty_added_removed(self):
        from open_compute.feed_manager import _diff_uia_elements
        elems = [{"name": "A", "role": "Button"}, {"name": "B", "role": "TabItem"}]
        added, removed = _diff_uia_elements(elems, elems)
        assert added == []
        assert removed == []

    def test_added_element_detected(self):
        from open_compute.feed_manager import _diff_uia_elements
        prev = [{"name": "A", "role": "Button"}]
        curr = [{"name": "A", "role": "Button"}, {"name": "B", "role": "MenuItem"}]
        added, removed = _diff_uia_elements(prev, curr)
        assert len(added) == 1
        assert added[0]["name"] == "B"
        assert removed == []

    def test_removed_element_detected(self):
        from open_compute.feed_manager import _diff_uia_elements
        prev = [{"name": "A", "role": "Button"}, {"name": "B", "role": "MenuItem"}]
        curr = [{"name": "A", "role": "Button"}]
        added, removed = _diff_uia_elements(prev, curr)
        assert added == []
        assert len(removed) == 1
        assert removed[0]["name"] == "B"

    def test_both_added_and_removed(self):
        from open_compute.feed_manager import _diff_uia_elements
        prev = [{"name": "Save", "role": "Button"}]
        curr = [{"name": "Cancel", "role": "Button"}]
        added, removed = _diff_uia_elements(prev, curr)
        assert any(e["name"] == "Cancel" for e in added)
        assert any(e["name"] == "Save" for e in removed)

    def test_empty_prev_all_added(self):
        from open_compute.feed_manager import _diff_uia_elements
        curr = [{"name": "X", "role": "Button"}]
        added, removed = _diff_uia_elements([], curr)
        assert len(added) == 1
        assert removed == []

    def test_empty_curr_all_removed(self):
        from open_compute.feed_manager import _diff_uia_elements
        prev = [{"name": "X", "role": "Button"}]
        added, removed = _diff_uia_elements(prev, [])
        assert added == []
        assert len(removed) == 1

    def test_both_empty_no_change(self):
        from open_compute.feed_manager import _diff_uia_elements
        added, removed = _diff_uia_elements([], [])
        assert added == []
        assert removed == []


# ---------------------------------------------------------------------------
# 6. _default_dosage
# ---------------------------------------------------------------------------

class TestDefaultDosage:
    def test_screenshot_notify(self):
        from open_compute.feed_manager import _default_dosage
        assert _default_dosage("screenshot") == "notify"

    def test_uia_windows_delta(self):
        from open_compute.feed_manager import _default_dosage
        assert _default_dosage("uia_windows") == "delta"

    def test_dirwatch_full(self):
        from open_compute.feed_manager import _default_dosage
        assert _default_dosage("dirwatch") == "full"

    def test_unknown_feed_returns_full(self):
        from open_compute.feed_manager import _default_dosage
        assert _default_dosage("some_unknown_feed") == "full"


# ---------------------------------------------------------------------------
# 7. FeedManager construction
# ---------------------------------------------------------------------------

class TestFeedManagerConstruction:
    def test_accepts_injected_feeds_and_sink(self):
        from open_compute.feed_manager import FeedManager, LocalFileInjector
        feeds = [_make_mock_feed("screenshot"), _make_mock_feed("dirwatch")]
        sink = LocalFileInjector()
        mgr = FeedManager(feeds=feeds, sink=sink)
        assert len(mgr._feeds) == 2
        assert mgr._sink is sink

    def test_default_sink_is_local_file(self):
        from open_compute.feed_manager import FeedManager, LocalFileInjector
        mgr = FeedManager(feeds=[])
        assert isinstance(mgr._sink, LocalFileInjector)

    def test_lazy_feeds_loads_available_feeds(self):
        from open_compute.feed_manager import FeedManager
        mock_screenshot = _make_mock_feed("screenshot")
        # Patch available_feeds in feed_manager module (where it's imported lazily)
        with patch("open_compute.feeds.registry.available_feeds", return_value=[mock_screenshot]):
            mgr = FeedManager(feeds=None)
            feeds = mgr._feeds  # triggers lazy load
            assert len(feeds) == 1
            assert feeds[0].name == "screenshot"

    def test_dosage_overrides_applied(self):
        from open_compute.feed_manager import FeedManager
        mgr = FeedManager(feeds=[], dosage_overrides={"screenshot": "full"})
        assert mgr.get_dosage("screenshot") == "full"

    def test_invalid_dosage_override_ignored(self):
        from open_compute.feed_manager import FeedManager, _default_dosage
        mgr = FeedManager(feeds=[], dosage_overrides={"screenshot": "bogus"})
        # Invalid value ignored; falls back to default
        assert mgr.get_dosage("screenshot") == _default_dosage("screenshot")


# ---------------------------------------------------------------------------
# 8. set_dosage / get_dosage
# ---------------------------------------------------------------------------

class TestDosageAPI:
    def test_set_dosage_valid_modes(self):
        from open_compute.feed_manager import FeedManager
        mgr = FeedManager(feeds=[])
        for mode in ("full", "delta", "notify", "off"):
            mgr.set_dosage("screenshot", mode)
            assert mgr.get_dosage("screenshot") == mode

    def test_set_dosage_invalid_raises_value_error(self):
        from open_compute.feed_manager import FeedManager
        mgr = FeedManager(feeds=[])
        with pytest.raises(ValueError, match="Invalid dosage mode"):
            mgr.set_dosage("screenshot", "bogus")

    def test_get_dosage_uses_default_when_not_set(self):
        from open_compute.feed_manager import FeedManager, _default_dosage
        mgr = FeedManager(feeds=[])
        assert mgr.get_dosage("screenshot") == _default_dosage("screenshot")
        assert mgr.get_dosage("dirwatch") == _default_dosage("dirwatch")


# ---------------------------------------------------------------------------
# 9. FeedManager.cycle
# ---------------------------------------------------------------------------

class TestFeedManagerCycle:
    def test_off_dosage_skipped(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            feed = _make_mock_feed("screenshot")
            mgr = FeedManager(feeds=[feed], dosage_overrides={"screenshot": "off"})
            result = mgr.cycle()
            assert result["screenshot"] == "skipped_off"
            feed.observe.assert_not_called()

    def test_unchanged_state_feed_skipped(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            obs = _make_feed_obs("uia_tree", elements=[{"name": "A", "role": "Button"}])
            feed = _make_mock_feed("uia_tree", obs=obs)
            mgr = FeedManager(feeds=[feed])
            mgr.cycle()  # first cycle: pushed
            result = mgr.cycle()  # second cycle: unchanged
            assert result["uia_tree"] == "skipped_unchanged"

    def test_changed_state_feed_pushed(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            from open_compute.feeds.base import FeedObservation
            obs1 = FeedObservation(kind="uia_tree", elements=[{"name": "A", "role": "Button"}])
            obs2 = FeedObservation(kind="uia_tree", elements=[{"name": "X", "role": "MenuItem"}])
            feed = _make_mock_feed("uia_tree", obs=obs1)
            mgr = FeedManager(feeds=[feed])
            mgr.cycle()
            # Change the observation
            feed.observe.return_value = obs2
            result = mgr.cycle()
            assert result["uia_tree"] == "pushed"

    def test_notify_dosage_payload_has_hash_no_elements(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            obs = _make_feed_obs("screenshot", elements=[{"png_bytes": b"PNG"}])
            feed = _make_mock_feed("screenshot", obs=obs)
            sink = MagicMock()
            sink.status.return_value = {}
            mgr = FeedManager(feeds=[feed], sink=sink, dosage_overrides={"screenshot": "notify"})
            mgr.cycle()
            pushed_payload = sink.push.call_args[0][1]
            assert "hash" in pushed_payload
            assert pushed_payload["dosage"] == "notify"
            assert "elements" not in pushed_payload

    def test_delta_dosage_payload_has_diff(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            from open_compute.feeds.base import FeedObservation
            obs1 = FeedObservation(kind="uia_tree", elements=[{"name": "A", "role": "Button"}])
            obs2 = FeedObservation(kind="uia_tree", elements=[
                {"name": "A", "role": "Button"},
                {"name": "B", "role": "MenuItem"},
            ])
            feed = _make_mock_feed("uia_windows", obs=obs1)
            sink = MagicMock()
            sink.status.return_value = {}
            mgr = FeedManager(feeds=[feed], sink=sink, dosage_overrides={"uia_windows": "delta"})
            mgr.cycle()
            feed.observe.return_value = obs2
            mgr.cycle()
            last_payload = sink.push.call_args[0][1]
            assert "added" in last_payload
            assert "removed" in last_payload
            assert last_payload["dosage"] == "delta"

    def test_full_dosage_payload_has_elements(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            obs = _make_feed_obs("uia_tree", elements=[{"name": "Z", "role": "TabItem"}])
            feed = _make_mock_feed("uia_tree", obs=obs)
            sink = MagicMock()
            sink.status.return_value = {}
            mgr = FeedManager(feeds=[feed], sink=sink, dosage_overrides={"uia_tree": "full"})
            mgr.cycle()
            payload = sink.push.call_args[0][1]
            assert payload["dosage"] == "full"
            assert "elements" in payload

    def test_event_feed_always_pushed(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            from open_compute.feeds.base import FeedObservation
            obs = FeedObservation(kind="dirwatch", elements=[{"event": "created", "path": "/tmp/x"}])
            feed = _make_mock_feed("dirwatch", obs=obs)
            sink = MagicMock()
            sink.status.return_value = {}
            mgr = FeedManager(feeds=[feed], sink=sink)
            result1 = mgr.cycle()
            result2 = mgr.cycle()
            # Event-Feeds: always pushed if there are events
            assert result1["dirwatch"] == "pushed"
            assert result2["dirwatch"] == "pushed"

    def test_event_feed_empty_elements_skipped(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            from open_compute.feeds.base import FeedObservation
            obs = FeedObservation(kind="dirwatch", elements=[])
            feed = _make_mock_feed("dirwatch", obs=obs)
            sink = MagicMock()
            sink.status.return_value = {}
            mgr = FeedManager(feeds=[feed], sink=sink)
            result = mgr.cycle()
            assert result["dirwatch"] == "skipped_unchanged"

    def test_observe_error_captured_in_summary(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            feed = _make_mock_feed("screenshot")
            feed.observe.side_effect = RuntimeError("boom")
            mgr = FeedManager(feeds=[feed])
            result = mgr.cycle()
            assert "error" in result["screenshot"]

    def test_multiple_feeds_in_cycle(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            feed_a = _make_mock_feed("uia_windows")
            feed_b = _make_mock_feed("dirwatch", obs=_make_feed_obs("dirwatch", elements=[{"ev": 1}]))
            sink = MagicMock()
            sink.status.return_value = {}
            mgr = FeedManager(feeds=[feed_a, feed_b], sink=sink,
                              dosage_overrides={"uia_windows": "full"})
            result = mgr.cycle()
            assert "uia_windows" in result
            assert "dirwatch" in result


# ---------------------------------------------------------------------------
# 10. FeedManager.on_demand_full
# ---------------------------------------------------------------------------

class TestOnDemandFull:
    def test_returns_observation_for_known_feed(self):
        obs = _make_feed_obs("screenshot")
        feed = _make_mock_feed("screenshot", obs=obs)
        from open_compute.feed_manager import FeedManager
        mgr = FeedManager(feeds=[feed])
        result = mgr.on_demand_full("screenshot")
        assert result is not None
        assert result.kind == "screenshot"

    def test_returns_none_for_unknown_feed(self):
        from open_compute.feed_manager import FeedManager
        mgr = FeedManager(feeds=[])
        assert mgr.on_demand_full("nonexistent") is None

    def test_returns_none_on_observe_error(self):
        feed = _make_mock_feed("screenshot")
        feed.observe.side_effect = RuntimeError("crash")
        from open_compute.feed_manager import FeedManager
        mgr = FeedManager(feeds=[feed])
        assert mgr.on_demand_full("screenshot") is None


# ---------------------------------------------------------------------------
# 11. FeedManager.status
# ---------------------------------------------------------------------------

class TestFeedManagerStatus:
    def test_status_returns_expected_keys(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            feed = _make_mock_feed("screenshot")
            mgr = FeedManager(feeds=[feed])
            s = mgr.status()
            assert "feeds" in s
            assert "dosages" in s
            assert "feed_status" in s
            assert "sink" in s

    def test_status_lists_feed_names(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            feed = _make_mock_feed("screenshot")
            mgr = FeedManager(feeds=[feed])
            s = mgr.status()
            assert "screenshot" in s["feeds"]

    def test_status_after_cycle_has_feed_status(self, tmp_path):
        with patch("open_compute.feed_manager._state_dir", return_value=tmp_path):
            from open_compute.feed_manager import FeedManager
            obs = _make_feed_obs("uia_tree", elements=[{"name": "X", "role": "Button"}])
            feed = _make_mock_feed("uia_tree", obs=obs)
            mgr = FeedManager(feeds=[feed], dosage_overrides={"uia_tree": "full"})
            mgr.cycle()
            s = mgr.status()
            assert "uia_tree" in s["feed_status"]
            assert s["feed_status"]["uia_tree"]["push_count"] >= 1


# ---------------------------------------------------------------------------
# 12. Import-without-extras
# ---------------------------------------------------------------------------

class TestImportWithoutExtras:
    def test_import_feed_manager_no_extras(self):
        import importlib
        import open_compute.feed_manager as fm_mod
        importlib.reload(fm_mod)
        assert hasattr(fm_mod, "FeedManager")
        assert hasattr(fm_mod, "LocalFileInjector")
        assert hasattr(fm_mod, "BachInjectorAdapter")
        assert hasattr(fm_mod, "InjectorSink")

    def test_import_open_compute_still_works(self):
        import open_compute
        assert open_compute.__version__ is not None
