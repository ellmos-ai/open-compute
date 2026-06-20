"""Tests for Phase 2a: Feed abstractions, registry, UIA feed (mocked).

Test tiers
----------
1. Protocol conformance — PerceptionFeed / Targeter structural checks.
2. FeedObservation / Target dataclass construction and defaults.
3. Registry — available_feeds() with/without UIA (mocked).
4. ScreenshotFeed — available() logic, observe() smoke (executor mocked).
5. UIA feed unit tests (uiautomation FULLY MOCKED):
   a. _rect_to_center_norm math (round-trip against to_sendinput_coords).
   b. _disambiguate: exact > prefix > contains; role filter; visibility.
   c. UiaWindowsFeed.resolve — picks correct element, returns correct Target.
   d. UiaWindowsFeed.invoke — calls InvokePattern, then fallback chain.
   e. UiaWindowsFeed.available() — True when uiautomation importable.
6. CLI parsing — oc tree / oc click-name / oc invoke argument parsing.
7. Import-without-extras: ``import open_compute`` + ``from open_compute.feeds
   import *`` work without uiautomation / mss installed.

All real OS/UIA calls are mocked. No mouse movement, no window manipulation.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_png() -> bytes:
    """Return a valid 1×1 RGB PNG."""
    import io, struct, zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw_row = b"\x00\x80\x40\x20"
    idat = chunk(b"IDAT", zlib.compress(raw_row, 9))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# 1. Protocol conformance (structural duck-typing checks)
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    """PerceptionFeed and Targeter are runtime_checkable Protocols."""

    def test_perception_feed_protocol_importable(self):
        from open_compute.feeds.base import PerceptionFeed
        assert callable(PerceptionFeed)

    def test_targeter_protocol_importable(self):
        from open_compute.feeds.base import Targeter
        assert callable(Targeter)

    def test_screenshot_feed_is_perception_feed(self):
        """ScreenshotFeed satisfies the PerceptionFeed protocol structurally."""
        from open_compute.feeds.base import PerceptionFeed
        from open_compute.feeds.screenshot import ScreenshotFeed
        sf = ScreenshotFeed()
        assert isinstance(sf, PerceptionFeed)

    def test_uia_feed_is_perception_feed(self):
        """UiaWindowsFeed satisfies PerceptionFeed structurally."""
        from open_compute.feeds.base import PerceptionFeed
        from open_compute.feeds.uia_windows import UiaWindowsFeed
        uf = UiaWindowsFeed()
        assert isinstance(uf, PerceptionFeed)

    def test_uia_feed_is_targeter(self):
        """UiaWindowsFeed satisfies Targeter structurally."""
        from open_compute.feeds.base import Targeter
        from open_compute.feeds.uia_windows import UiaWindowsFeed
        uf = UiaWindowsFeed()
        assert isinstance(uf, Targeter)


# ---------------------------------------------------------------------------
# 2. FeedObservation / Target dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_feed_observation_defaults(self):
        from open_compute.feeds.base import FeedObservation
        obs = FeedObservation(kind="test")
        assert obs.kind == "test"
        assert obs.elements == []
        assert obs.text is None
        assert obs.ts > 0

    def test_feed_observation_with_elements(self):
        from open_compute.feeds.base import FeedObservation
        elems = [{"name": "OK", "role": "Button"}]
        obs = FeedObservation(kind="uia_tree", elements=elems, text="hello")
        assert obs.elements[0]["name"] == "OK"
        assert obs.text == "hello"

    def test_target_defaults(self):
        from open_compute.feeds.base import Target
        t = Target(name="Save", role="Button", rect_px=(10, 20, 100, 30), center_norm=(0.5, 0.3))
        assert t.name == "Save"
        assert t.invokable is False
        assert t.feed == ""

    def test_target_with_all_fields(self):
        from open_compute.feeds.base import Target
        t = Target(
            name="File",
            role="MenuItem",
            rect_px=(0, 0, 50, 20),
            center_norm=(0.1, 0.05),
            invokable=True,
            feed="uia_windows",
        )
        assert t.invokable is True
        assert t.feed == "uia_windows"


# ---------------------------------------------------------------------------
# 3. Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    """available_feeds() degrades gracefully."""

    def test_registry_importable_without_uia(self):
        """feeds.registry can be imported even if uiautomation is absent."""
        from open_compute.feeds import registry
        assert hasattr(registry, "available_feeds")

    def test_available_feeds_returns_list(self):
        """available_feeds() always returns a list (may be empty)."""
        from open_compute.feeds.registry import available_feeds
        feeds = available_feeds()
        assert isinstance(feeds, list)

    def test_feed_names_returns_list(self):
        from open_compute.feeds.registry import feed_names
        names = feed_names()
        assert isinstance(names, list)

    def test_registry_without_uia_excludes_uia(self, monkeypatch):
        """When uiautomation is not importable, UIA feed is absent."""
        # Patch UiaWindowsFeed.available to return False
        import importlib
        from open_compute.feeds import registry as reg_mod
        import open_compute.feeds.uia_windows as uia_mod

        with patch.object(uia_mod.UiaWindowsFeed, "available", return_value=False):
            feeds = reg_mod.available_feeds()
        uia_names = [f.name for f in feeds if f.name == "uia_windows"]
        assert uia_names == []

    def test_registry_with_uia_includes_uia(self, monkeypatch):
        """When uiautomation is available, UIA feed appears (Windows-only)."""
        if sys.platform != "win32":
            pytest.skip("UIA feed is Windows-only")

        import open_compute.feeds.uia_windows as uia_mod
        from open_compute.feeds import registry as reg_mod

        with patch.object(uia_mod.UiaWindowsFeed, "available", return_value=True):
            feeds = reg_mod.available_feeds()
        uia_names = [f.name for f in feeds if f.name == "uia_windows"]
        assert len(uia_names) == 1

    def test_registry_exception_in_feed_does_not_crash(self):
        """If a feed constructor raises, available_feeds() returns partial list."""
        from open_compute.feeds import registry as reg_mod
        from open_compute.feeds import screenshot as ss_mod

        with patch.object(ss_mod.ScreenshotFeed, "available", side_effect=RuntimeError("boom")):
            feeds = reg_mod.available_feeds()
        # Must not raise; result is a list (may be shorter)
        assert isinstance(feeds, list)


# ---------------------------------------------------------------------------
# 4. ScreenshotFeed
# ---------------------------------------------------------------------------

class TestScreenshotFeed:
    def test_name_is_screenshot(self):
        from open_compute.feeds.screenshot import ScreenshotFeed
        assert ScreenshotFeed().name == "screenshot"

    def test_available_false_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        import importlib, open_compute.feeds.screenshot as mod
        importlib.reload(mod)
        sf = mod.ScreenshotFeed()
        assert sf.available() is False
        # Restore
        importlib.reload(mod)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_available_false_without_mss(self):
        """available() returns False when mss cannot be imported."""
        from open_compute.feeds.screenshot import ScreenshotFeed
        import builtins
        real_import = builtins.__import__

        def _no_mss(name, *a, **kw):
            if name == "mss":
                raise ImportError("no mss")
            return real_import(name, *a, **kw)

        sf = ScreenshotFeed()
        with patch("builtins.__import__", side_effect=_no_mss):
            result = sf.available()
        assert result is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_observe_returns_feed_observation(self):
        """observe() returns a FeedObservation with kind='screenshot'."""
        from open_compute.feeds.screenshot import ScreenshotFeed
        from open_compute.perception import Observation

        fake_obs = Observation(screenshot=_make_valid_png(), width=1920, height=1080)

        # LocalExecutor is imported inside observe() via 'open_compute.drivers.local'
        with patch("open_compute.drivers.local.LocalExecutor") as MockExec:
            MockExec.return_value.screenshot.return_value = fake_obs

            sf = ScreenshotFeed()
            result = sf.observe()

        assert result.kind == "screenshot"
        assert len(result.elements) == 1
        assert result.elements[0]["role"] == "pixel_frame"
        assert result.elements[0]["png_bytes"] == fake_obs.screenshot


# ---------------------------------------------------------------------------
# 5a. UIA coordinate math — _rect_to_center_norm round-trip
# ---------------------------------------------------------------------------

class TestUiaCoordinateMath:
    """center_norm round-trip: normalize via UIA → denormalize via to_sendinput_coords."""

    def _make_rect(self, x, y, w, h):
        class _R:
            def __init__(self, x, y, w, h):
                self.x, self.y, self.width, self.height = x, y, w, h
        return _R(x, y, w, h)

    def test_center_element_single_monitor(self):
        """Element at screen center → center_norm ≈ (0.5, 0.5)."""
        from open_compute.feeds.uia_windows import _rect_to_center_norm
        # Virtual desktop: 1920x1080, origin (0, 0)
        rect = self._make_rect(860, 520, 200, 80)  # center at (960, 560)
        nx, ny = _rect_to_center_norm(rect, 0, 0, 1920, 1080)
        assert abs(nx - 960 / 1920) < 1e-4
        assert abs(ny - 560 / 1080) < 1e-4

    def test_top_left_element(self):
        """Element at (0, 0) → center_norm near (0, 0)."""
        from open_compute.feeds.uia_windows import _rect_to_center_norm
        rect = self._make_rect(0, 0, 10, 10)  # center at (5, 5)
        nx, ny = _rect_to_center_norm(rect, 0, 0, 1920, 1080)
        assert nx > 0.0
        assert ny > 0.0
        assert nx < 0.01
        assert ny < 0.01

    def test_bottom_right_element(self):
        """Element at bottom-right → center_norm near (1, 1)."""
        from open_compute.feeds.uia_windows import _rect_to_center_norm
        rect = self._make_rect(1910, 1070, 10, 10)  # center at (1915, 1075)
        nx, ny = _rect_to_center_norm(rect, 0, 0, 1920, 1080)
        assert nx > 0.99
        assert ny > 0.99
        assert nx <= 1.0
        assert ny <= 1.0

    def test_negative_virtual_desktop_origin(self):
        """Multi-monitor: virtual desktop starts at negative coords."""
        from open_compute.feeds.uia_windows import _rect_to_center_norm
        # Second monitor to the left: virtual desktop from (-1920, 0) to (1920, 1080)
        # Total width = 3840, height = 1080
        virt_left, virt_top = -1920, 0
        virt_width, virt_height = 3840, 1080
        # Element on primary monitor (x=0..1920), center at (960, 540)
        rect = self._make_rect(860, 480, 200, 120)  # center at (960, 540)
        nx, ny = _rect_to_center_norm(rect, virt_left, virt_top, virt_width, virt_height)
        # (960 - (-1920)) / 3840 = 2880/3840 = 0.75
        # (540 - 0) / 1080 = 0.5
        assert abs(nx - 0.75) < 1e-4
        assert abs(ny - 0.5) < 1e-4

    def test_round_trip_with_sendinput(self):
        """center_norm → to_sendinput_coords should round-trip to same pixel center."""
        from open_compute.feeds.uia_windows import _rect_to_center_norm
        from open_compute.drivers.local import to_sendinput_coords

        vl, vt, vw, vh = 0, 0, 1920, 1080
        # Element with center at (500, 300)
        rect_x, rect_y, rect_w, rect_h = 400, 260, 200, 80
        rect_cx = rect_x + rect_w / 2  # 500
        rect_cy = rect_y + rect_h / 2  # 300

        class _R:
            def __init__(self):
                self.x, self.y, self.width, self.height = rect_x, rect_y, rect_w, rect_h

        nx, ny = _rect_to_center_norm(_R(), vl, vt, vw, vh)
        # nx = (500 - 0) / 1920 = 0.260416...
        # ny = (300 - 0) / 1080 = 0.277777...

        # to_sendinput_coords maps nx,ny → 0..65535
        dx, dy = to_sendinput_coords(nx, ny, vl, vt, vw, vh)
        # Convert back to pixel: px = vl + (dx/65535) * vw
        px = vl + (dx / 65535) * vw
        py = vt + (dy / 65535) * vh
        # Should land within 1 pixel of the original center
        assert abs(px - rect_cx) < 1.0, f"px={px} vs center_x={rect_cx}"
        assert abs(py - rect_cy) < 1.0, f"py={py} vs center_y={rect_cy}"

    def test_zero_sized_virtual_desktop_returns_zero(self):
        """Degenerate case: zero-width virtual desktop → (0, 0)."""
        from open_compute.feeds.uia_windows import _rect_to_center_norm

        class _R:
            x, y, width, height = 100, 100, 50, 50

        nx, ny = _rect_to_center_norm(_R(), 0, 0, 0, 0)
        assert nx == 0.0
        assert ny == 0.0


# ---------------------------------------------------------------------------
# 5b. _disambiguate
# ---------------------------------------------------------------------------

class TestDisambiguate:
    def _elems(self):
        return [
            {"name": "Einfügen", "role": "TabItem", "visible": True},
            {"name": "Einfügung", "role": "Button", "visible": True},   # prefix match
            {"name": "kein Einfügen", "role": "MenuItem", "visible": True},  # contains
            {"name": "Start", "role": "TabItem", "visible": True},
            {"name": "Unsichtbar", "role": "Button", "visible": False},
        ]

    def test_exact_match_wins_over_prefix(self):
        from open_compute.feeds.uia_windows import _disambiguate
        elems = self._elems()
        result = _disambiguate("Einfügen", None, elems)
        assert result is not None
        assert result["name"] == "Einfügen"

    def test_prefix_match_when_no_exact(self):
        from open_compute.feeds.uia_windows import _disambiguate
        elems = [
            {"name": "Einfügung", "role": "Button", "visible": True},
            {"name": "kein Einfügen", "role": "MenuItem", "visible": True},
        ]
        result = _disambiguate("einfüg", None, elems)
        assert result is not None
        assert result["name"] == "Einfügung"

    def test_contains_match_as_last_resort(self):
        from open_compute.feeds.uia_windows import _disambiguate
        elems = [
            {"name": "kein Einfügen hier", "role": "MenuItem", "visible": True},
        ]
        result = _disambiguate("einfügen", None, elems)
        assert result is not None
        assert "Einfügen" in result["name"]

    def test_role_filter_applied(self):
        from open_compute.feeds.uia_windows import _disambiguate
        elems = self._elems()
        # Only TabItem — should return "Einfügen" (TabItem), not "Einfügung" (Button)
        result = _disambiguate("Einfügen", "TabItem", elems)
        assert result is not None
        assert result["role"] == "TabItem"
        assert result["name"] == "Einfügen"

    def test_role_filter_excludes_wrong_role(self):
        from open_compute.feeds.uia_windows import _disambiguate
        elems = [{"name": "Einfügen", "role": "Button", "visible": True}]
        result = _disambiguate("Einfügen", "TabItem", elems)
        # No TabItem named Einfügen → None
        assert result is None

    def test_visible_preferred_over_invisible(self):
        from open_compute.feeds.uia_windows import _disambiguate
        elems = [
            {"name": "Save", "role": "Button", "visible": False},
            {"name": "Save", "role": "Button", "visible": True},
        ]
        result = _disambiguate("Save", None, elems)
        assert result is not None
        assert result["visible"] is True

    def test_no_match_returns_none(self):
        from open_compute.feeds.uia_windows import _disambiguate
        result = _disambiguate("NoSuchElement", None, self._elems())
        assert result is None

    def test_case_insensitive(self):
        from open_compute.feeds.uia_windows import _disambiguate
        elems = [{"name": "SAVE", "role": "Button", "visible": True}]
        result = _disambiguate("save", None, elems)
        assert result is not None
        assert result["name"] == "SAVE"


# ---------------------------------------------------------------------------
# 5c. UiaWindowsFeed.resolve (uiautomation mocked)
# ---------------------------------------------------------------------------

class TestUiaFeedResolve:
    """resolve() with uiautomation mocked — no real UIA calls.

    The real ``uiautomation`` library uses ``WalkControl(root, maxDepth=N)``
    which yields ``(ctrl, depth)`` tuples.  Mocks reflect this API.
    """

    def _build_mock_uia(self, elements: list[dict[str, Any]]):
        """Build a minimal uiautomation mock that returns the given elements.

        ``WalkControl()`` is mocked to yield ``(ctrl, depth)`` tuples from
        the element list.  Each ctrl has ``.Name``, ``.ControlTypeName``,
        ``.BoundingRectangle``, and pattern accessors.
        """
        mock_uia = MagicMock()

        # Build fake control objects
        ctrls = []
        for elem in elements:
            ctrl = MagicMock()
            ctrl.Name = elem.get("name", "")
            ctrl.ControlTypeName = elem.get("role", "")
            rect = MagicMock()
            rx, ry, rw, rh = elem.get("rect_px", (0, 0, 0, 0))
            # Set all rect attrs that _rect_to_px_tuple reads
            rect.left = rx
            rect.top = ry
            rect.right = rx + rw
            rect.bottom = ry + rh
            rect.xcenter = rx + rw / 2.0
            rect.ycenter = ry + rh / 2.0
            ctrl.BoundingRectangle = rect
            ctrl.GetValuePattern.return_value = None
            ctrl.GetInvokePattern.return_value = MagicMock()
            ctrl.GetTogglePattern.return_value = None
            ctrl.GetSelectionItemPattern.return_value = None
            ctrl.GetLegacyIAccessiblePattern.return_value = None
            ctrl.GetTextPattern.return_value = None
            # Children / siblings for _find_live_control
            ctrl.GetFirstChildControl.return_value = None
            ctrl.GetNextSiblingControl.return_value = None
            ctrls.append(ctrl)

        # WalkControl yields (ctrl, depth) tuples — depth=1 for all elements
        walk_results = [(c, 1) for c in ctrls]
        mock_uia.WalkControl.return_value = iter(walk_results)

        # Root control (used by _get_root)
        root = ctrls[0] if ctrls else MagicMock()
        root.GetTextPattern.return_value = None
        root.GetFirstChildControl.return_value = None
        mock_uia.GetForegroundControl.return_value = root
        mock_uia.GetRootControl.return_value = root

        return mock_uia, ctrls

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_resolve_returns_target_for_known_name(self, monkeypatch):
        """resolve('Einfügen') finds the element and returns a Target."""
        elems = [
            {"name": "Start", "role": "TabItem", "rect_px": (0, 290, 100, 30)},
            {"name": "Einfügen", "role": "TabItem", "rect_px": (400, 290, 200, 30)},
        ]
        mock_uia, _ = self._build_mock_uia(elems)

        import open_compute.feeds.uia_windows as uia_mod

        # WalkControl must be re-iterable for observe() + _check_invokable()
        def fresh_walk(*args, **kwargs):
            walk_results = [(ctrl, 1) for ctrl in _[1]] if False else [
                (c, 1) for c in mock_uia._ctrls
            ]
            return iter(walk_results)

        # Store ctrls on the mock for easy access
        ctrls_list = []
        for elem in elems:
            ctrl = MagicMock()
            ctrl.Name = elem["name"]
            ctrl.ControlTypeName = elem["role"]
            rect = MagicMock()
            rx, ry, rw, rh = elem["rect_px"]
            # Set ALL rect fields that _rect_to_px_tuple / _rect_to_center_norm read
            rect.left = rx
            rect.top = ry
            rect.right = rx + rw
            rect.bottom = ry + rh
            rect.xcenter = rx + rw / 2.0
            rect.ycenter = ry + rh / 2.0
            ctrl.BoundingRectangle = rect
            ctrl.GetValuePattern.return_value = None
            ctrl.GetInvokePattern.return_value = MagicMock()
            ctrl.GetTogglePattern.return_value = None
            ctrl.GetSelectionItemPattern.return_value = None
            ctrl.GetLegacyIAccessiblePattern.return_value = None
            ctrl.GetTextPattern.return_value = None
            ctrl.GetFirstChildControl.return_value = None
            ctrl.GetNextSiblingControl.return_value = None
            ctrls_list.append(ctrl)

        mock_uia.WalkControl.side_effect = lambda *a, **kw: iter([(c, 1) for c in ctrls_list])
        root = ctrls_list[0]
        root.GetTextPattern.return_value = None
        mock_uia.GetForegroundControl.return_value = root
        mock_uia.GetRootControl.return_value = root

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_virtual_desktop", return_value=(0, 0, 1920, 1080)),
            patch.object(uia_mod, "_set_dpi_awareness"),
        ):
            feed = uia_mod.UiaWindowsFeed()
            with patch.object(feed, "available", return_value=True):
                target = feed.resolve("Einfügen")

        assert target is not None
        assert target.name == "Einfügen"
        assert target.role == "TabItem"
        # center_norm: rect (400,290,200,30), center=(500,305)
        # nx = 500/1920 ≈ 0.2604, ny = 305/1080 ≈ 0.2824
        assert abs(target.center_norm[0] - 500 / 1920) < 0.01
        assert abs(target.center_norm[1] - 305 / 1080) < 0.01

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_resolve_returns_none_for_unknown(self):
        """resolve() returns None when the element is not in the tree."""
        import open_compute.feeds.uia_windows as uia_mod

        ctrl = MagicMock()
        ctrl.Name = "Start"
        ctrl.ControlTypeName = "TabItem"
        rect = MagicMock()
        rect.left, rect.top, rect.right, rect.bottom = 0, 290, 100, 320
        rect.xcenter, rect.ycenter = 50.0, 305.0
        ctrl.BoundingRectangle = rect
        ctrl.GetValuePattern.return_value = None
        ctrl.GetTextPattern.return_value = None
        ctrl.GetFirstChildControl.return_value = None

        mock_uia = MagicMock()
        mock_uia.WalkControl.side_effect = lambda *a, **kw: iter([(ctrl, 1)])
        mock_uia.GetForegroundControl.return_value = ctrl
        mock_uia.GetRootControl.return_value = ctrl

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_virtual_desktop", return_value=(0, 0, 1920, 1080)),
            patch.object(uia_mod, "_set_dpi_awareness"),
        ):
            feed = uia_mod.UiaWindowsFeed()
            with patch.object(feed, "available", return_value=True):
                target = feed.resolve("NonExistent")

        assert target is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_resolve_center_norm_range(self):
        """center_norm values are always in [0, 1]."""
        import open_compute.feeds.uia_windows as uia_mod

        ctrl = MagicMock()
        ctrl.Name = "OK"
        ctrl.ControlTypeName = "Button"
        rect = MagicMock()
        rect.left, rect.top, rect.right, rect.bottom = 100, 200, 180, 230
        rect.xcenter, rect.ycenter = 140.0, 215.0
        ctrl.BoundingRectangle = rect
        ctrl.GetValuePattern.return_value = None
        ctrl.GetTextPattern.return_value = None
        ctrl.GetInvokePattern.return_value = MagicMock()
        ctrl.GetFirstChildControl.return_value = None
        ctrl.GetNextSiblingControl.return_value = None

        mock_uia = MagicMock()
        mock_uia.WalkControl.side_effect = lambda *a, **kw: iter([(ctrl, 1)])
        mock_uia.GetForegroundControl.return_value = ctrl
        mock_uia.GetRootControl.return_value = ctrl

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_virtual_desktop", return_value=(0, 0, 1920, 1080)),
            patch.object(uia_mod, "_set_dpi_awareness"),
        ):
            feed = uia_mod.UiaWindowsFeed()
            with patch.object(feed, "available", return_value=True):
                target = feed.resolve("OK")

        assert target is not None
        nx, ny = target.center_norm
        assert 0.0 <= nx <= 1.0
        assert 0.0 <= ny <= 1.0

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_resolve_non_windows_returns_none(self, monkeypatch):
        """On non-Windows, resolve() returns None cleanly."""
        import open_compute.feeds.uia_windows as uia_mod
        monkeypatch.setattr(sys, "platform", "linux")
        import importlib
        importlib.reload(uia_mod)
        feed = uia_mod.UiaWindowsFeed()
        result = feed.resolve("whatever")
        assert result is None
        importlib.reload(uia_mod)


# ---------------------------------------------------------------------------
# 5d. UiaWindowsFeed.invoke fallback chain (uiautomation mocked)
# ---------------------------------------------------------------------------

class TestUiaFeedInvoke:
    """invoke() pattern fallback chain with mocked UIA."""

    def _mock_ctrl(self, has_invoke=True, has_toggle=False, has_selection=False, has_legacy=False):
        ctrl = MagicMock()
        ctrl.Name = "TestBtn"
        ctrl.ControlTypeName = "Button"
        rect = MagicMock()
        rect.x, rect.y, rect.width, rect.height = 100, 100, 50, 20
        ctrl.BoundingRectangle = rect
        ctrl.GetValuePattern.return_value = None
        ctrl.GetTextPattern.return_value = None

        invoke_pattern = MagicMock() if has_invoke else None
        ctrl.GetInvokePattern.return_value = invoke_pattern

        toggle_pattern = MagicMock() if has_toggle else None
        ctrl.GetTogglePattern.return_value = toggle_pattern

        sel_pattern = MagicMock() if has_selection else None
        ctrl.GetSelectionItemPattern.return_value = sel_pattern

        legacy_pattern = MagicMock() if has_legacy else None
        ctrl.GetLegacyIAccessiblePattern.return_value = legacy_pattern

        return ctrl

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_invoke_uses_invoke_pattern(self):
        """invoke() calls InvokePattern.Invoke() when available."""
        import open_compute.feeds.uia_windows as uia_mod

        ctrl = self._mock_ctrl(has_invoke=True)
        # _invoke_control works directly on the ctrl — no UIA module needed
        result = uia_mod._invoke_control(ctrl)

        assert result is True
        ctrl.GetInvokePattern.return_value.Invoke.assert_called_once()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_invoke_falls_back_to_toggle(self):
        """When InvokePattern is absent, TogglePattern.Toggle() is used."""
        import open_compute.feeds.uia_windows as uia_mod

        ctrl = self._mock_ctrl(has_invoke=False, has_toggle=True)
        result = uia_mod._invoke_control(ctrl)

        assert result is True
        ctrl.GetTogglePattern.return_value.Toggle.assert_called_once()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_invoke_falls_back_to_selection_item(self):
        """When Invoke+Toggle absent, SelectionItemPattern.Select() is used."""
        import open_compute.feeds.uia_windows as uia_mod

        ctrl = self._mock_ctrl(has_invoke=False, has_toggle=False, has_selection=True)
        result = uia_mod._invoke_control(ctrl)

        assert result is True
        ctrl.GetSelectionItemPattern.return_value.Select.assert_called_once()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_invoke_falls_back_to_legacy(self):
        """Last resort: LegacyIAccessible.DoDefaultAction()."""
        import open_compute.feeds.uia_windows as uia_mod

        ctrl = self._mock_ctrl(has_invoke=False, has_toggle=False, has_selection=False, has_legacy=True)
        result = uia_mod._invoke_control(ctrl)

        assert result is True
        ctrl.GetLegacyIAccessiblePattern.return_value.DoDefaultAction.assert_called_once()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_invoke_returns_false_when_no_pattern(self):
        """Returns False when no applicable pattern is available."""
        import open_compute.feeds.uia_windows as uia_mod

        ctrl = self._mock_ctrl(has_invoke=False, has_toggle=False, has_selection=False, has_legacy=False)
        result = uia_mod._invoke_control(ctrl)

        assert result is False


# ---------------------------------------------------------------------------
# 5e. UiaWindowsFeed.available()
# ---------------------------------------------------------------------------

class TestUiaAvailability:
    def test_available_false_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        import importlib
        import open_compute.feeds.uia_windows as uia_mod
        importlib.reload(uia_mod)
        feed = uia_mod.UiaWindowsFeed()
        assert feed.available() is False
        importlib.reload(uia_mod)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_available_false_without_uiautomation(self):
        """available() returns False when uiautomation is not installed."""
        import open_compute.feeds.uia_windows as uia_mod

        def _raise_import(name, *a, **kw):
            raise ImportError("no uiautomation")

        feed = uia_mod.UiaWindowsFeed()
        with patch.object(uia_mod, "_get_uia", side_effect=ImportError("no uiautomation")):
            result = feed.available()
        assert result is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_available_true_with_uiautomation(self):
        """available() returns True when uiautomation is importable."""
        import open_compute.feeds.uia_windows as uia_mod

        feed = uia_mod.UiaWindowsFeed()
        with patch.object(uia_mod, "_get_uia", return_value=MagicMock()):
            result = feed.available()
        assert result is True


# ---------------------------------------------------------------------------
# 6. CLI parsing — oc tree / oc click-name / oc invoke
# ---------------------------------------------------------------------------

class TestCliUiaCommands:
    """CLI argument parsing for UIA commands (mocked feed)."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_cmd_tree_outputs_json_list(self, capsys):
        """oc tree outputs a JSON array."""
        from open_compute.feeds.base import FeedObservation
        from open_compute.cli import cmd_tree

        fake_obs = FeedObservation(
            kind="uia_tree",
            elements=[
                {"name": "OK", "role": "Button", "value": "", "rect_px": (100, 200, 80, 30),
                 "visible": True, "depth": 1},
            ],
        )

        with patch("open_compute.cli._load_uia_feed") as mock_load:
            mock_feed = MagicMock()
            mock_feed.observe.return_value = fake_obs
            mock_load.return_value = mock_feed

            import open_compute.feeds.uia_windows as uia_mod
            with (
                patch.object(uia_mod, "_get_virtual_desktop", return_value=(0, 0, 1920, 1080)),
            ):
                cmd_tree(["--window", "Word"])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "OK"
        assert "center_norm" in data[0]

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_cmd_click_name_executes_on_allow(self, capsys):
        """oc click-name resolves → clicks via LocalExecutor in allow_all mode."""
        from open_compute.feeds.base import Target
        from open_compute.perception import Observation

        target = Target(
            name="Einfügen", role="TabItem",
            rect_px=(400, 290, 200, 30),
            center_norm=(0.26, 0.28),
            invokable=True, feed="uia_windows",
        )
        fake_obs = Observation(screenshot=b"PNG", width=1920, height=1080)

        with (
            patch("open_compute.cli._load_uia_feed") as mock_load_uia,
            patch("open_compute.cli._load_local_executor") as mock_load_exec,
        ):
            mock_feed = MagicMock()
            mock_feed.resolve.return_value = target
            mock_load_uia.return_value = mock_feed

            mock_executor = MagicMock()
            mock_executor.execute.return_value = fake_obs
            mock_load_exec.return_value = mock_executor

            from open_compute.cli import cmd_click_name
            cmd_click_name(["Einfügen", "--mode", "allow_all"])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["result"] == "executed"
        assert data["action"] == "left_click"
        assert data["target"] == "Einfügen"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_cmd_click_name_exits_1_when_not_found(self, capsys):
        """oc click-name exits 2 when the element is not found."""
        with patch("open_compute.cli._load_uia_feed") as mock_load:
            mock_feed = MagicMock()
            mock_feed.resolve.return_value = None
            mock_load.return_value = mock_feed

            from open_compute.cli import cmd_click_name
            with pytest.raises(SystemExit) as exc:
                cmd_click_name(["NoSuchElement", "--mode", "allow_all"])

        assert exc.value.code == 2

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_cmd_invoke_outputs_invoked(self, capsys):
        """oc invoke calls feed.invoke() and reports 'invoked'."""
        from open_compute.feeds.base import Target

        target = Target(
            name="Save", role="Button",
            rect_px=(200, 100, 60, 25),
            center_norm=(0.23, 0.11),
            invokable=True, feed="uia_windows",
        )

        with patch("open_compute.cli._load_uia_feed") as mock_load:
            mock_feed = MagicMock()
            mock_feed.resolve.return_value = target
            mock_feed.invoke.return_value = True
            mock_load.return_value = mock_feed

            from open_compute.cli import cmd_invoke
            cmd_invoke(["Save", "--mode", "allow_all"])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["result"] == "invoked"
        assert data["target"] == "Save"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_cmd_invoke_exits_2_on_failure(self, capsys):
        """oc invoke exits 2 when feed.invoke() returns False."""
        from open_compute.feeds.base import Target

        target = Target(
            name="Broken", role="Button",
            rect_px=(0, 0, 10, 10),
            center_norm=(0.01, 0.01),
            invokable=False, feed="uia_windows",
        )

        with patch("open_compute.cli._load_uia_feed") as mock_load:
            mock_feed = MagicMock()
            mock_feed.resolve.return_value = target
            mock_feed.invoke.return_value = False
            mock_load.return_value = mock_feed

            from open_compute.cli import cmd_invoke
            with pytest.raises(SystemExit) as exc:
                cmd_invoke(["Broken", "--mode", "allow_all"])

        assert exc.value.code == 2
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["result"] == "invoke_failed"


# ---------------------------------------------------------------------------
# 7. Import-without-extras
# ---------------------------------------------------------------------------

class TestImportWithoutExtras:
    def test_import_open_compute_no_uiautomation(self):
        """import open_compute works even when uiautomation is not installed."""
        import importlib
        import open_compute
        importlib.reload(open_compute)
        assert open_compute.__version__ == "0.6.0"

    def test_import_feeds_base_no_extras(self):
        """feeds.base is importable without any extras."""
        import importlib
        from open_compute.feeds import base
        importlib.reload(base)
        assert hasattr(base, "FeedObservation")
        assert hasattr(base, "Target")
        assert hasattr(base, "PerceptionFeed")
        assert hasattr(base, "Targeter")

    def test_import_feeds_registry_no_extras(self):
        """feeds.registry is importable without any extras."""
        import importlib
        from open_compute.feeds import registry
        importlib.reload(registry)
        assert hasattr(registry, "available_feeds")

    def test_import_feeds_uia_windows_no_uiautomation(self):
        """feeds.uia_windows is importable even without uiautomation installed.

        The import must NOT trigger an ImportError; only available() should
        return False when the package is absent.
        """
        import importlib
        from open_compute.feeds import uia_windows
        importlib.reload(uia_windows)
        assert hasattr(uia_windows, "UiaWindowsFeed")


# ---------------------------------------------------------------------------
# 8. Version bump to 0.4.0
# ---------------------------------------------------------------------------

class TestVersionBump:
    def test_version_is_040(self):
        import open_compute
        assert open_compute.__version__ == "0.6.0"
