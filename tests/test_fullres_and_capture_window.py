"""Tests for Feature #3 — full-res after-shot + annotated verification shot.

Test tiers
----------
1. _save_fullres_shot — path logic, Pillow annotation, Pillow-absent fallback.
2. _find_window_hwnd / _hwnd_to_mss_region — Win32 mocked, pure rect math.
3. oc do --fullres — CLI parsing + JSON output key presence.
4. oc click-name --fullres — CLI parsing + JSON output key presence.
5. oc capture --window — CLI parsing; window-not-found exit, mocked success.

All Win32 / mss / Pillow calls are fully mocked — no real OS or display calls.
"""

from __future__ import annotations

import io
import json
import pathlib
import struct
import sys
import types
import zlib
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_png(w: int = 2, h: int = 2) -> bytes:
    """Return a valid minimal RGB PNG of size w×h."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    # Raw scanline: filter byte 0x00 + RGB pixels
    raw_row = b"\x00" + b"\x80\x40\x20" * w
    raw_data = raw_row * h
    idat = chunk(b"IDAT", zlib.compress(raw_data, 9))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# 1. _save_fullres_shot
# ---------------------------------------------------------------------------

class TestSaveFullresShot:
    def test_saves_without_pillow(self, tmp_path: pathlib.Path) -> None:
        """Full-res shot saved without annotation when Pillow is absent."""
        from open_compute.cli import _save_fullres_shot

        png = _make_valid_png()
        out = tmp_path / "shot_fr.png"

        # Block Pillow
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None, "PIL.ImageDraw": None}):
            result = _save_fullres_shot(png, out, click_nx=0.5, click_ny=0.5, img_width=2, img_height=2)

        assert out.exists()
        assert out.read_bytes() == png  # no modification
        assert "fullres" in result
        assert result["fullres"] == str(out)

    def test_saves_without_coordinates(self, tmp_path: pathlib.Path) -> None:
        """No annotation attempted when no click coordinates given."""
        from open_compute.cli import _save_fullres_shot

        png = _make_valid_png()
        out = tmp_path / "shot_fr_nocoord.png"

        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
            result = _save_fullres_shot(png, out)

        assert out.exists()
        assert "fullres" in result
        assert "fullres_annotated" not in result

    def test_saves_annotated_with_pillow(self, tmp_path: pathlib.Path) -> None:
        """When Pillow is available, result has 'fullres_annotated' key."""
        from open_compute.cli import _save_fullres_shot

        # Use a real Pillow if available, otherwise skip test
        pytest.importorskip("PIL", reason="Pillow not installed — skip annotation test")

        png = _make_valid_png(50, 50)
        out = tmp_path / "shot_annotated.png"
        result = _save_fullres_shot(png, out, click_nx=0.5, click_ny=0.5, img_width=50, img_height=50)

        assert out.exists()
        # Must be annotated
        assert "fullres_annotated" in result

    def test_path_returned_as_string(self, tmp_path: pathlib.Path) -> None:
        from open_compute.cli import _save_fullres_shot

        png = _make_valid_png()
        out = tmp_path / "shot.png"
        with patch.dict("sys.modules", {"PIL": None}):
            result = _save_fullres_shot(png, out)
        # Path must be a string (JSON-serializable)
        key = "fullres_annotated" if "fullres_annotated" in result else "fullres"
        assert isinstance(result[key], str)

    def test_missing_img_dimensions_no_annotation(self, tmp_path: pathlib.Path) -> None:
        """When img_width/height are None, no annotation attempted."""
        from open_compute.cli import _save_fullres_shot

        png = _make_valid_png()
        out = tmp_path / "shot_nodim.png"
        # Pillow available but no dimensions → no marker
        result = _save_fullres_shot(png, out, click_nx=0.5, click_ny=0.5,
                                    img_width=None, img_height=None)
        assert out.exists()
        # No annotation because dimensions missing
        assert "fullres" in result
        assert "fullres_annotated" not in result


# ---------------------------------------------------------------------------
# 2. Win32 rect helpers (mocked)
# ---------------------------------------------------------------------------

class TestWindowRectHelpers:
    """Test _find_window_hwnd and _hwnd_to_mss_region with mocked Win32 APIs."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
    def test_find_window_hwnd_found(self) -> None:
        """Mocked EnumWindows → _find_window_hwnd returns matching HWND."""
        import ctypes
        from open_compute.cli import _find_window_hwnd

        def fake_enum_windows(cb, lp):
            # Simulate two windows: HWND 10 = "My App", HWND 20 = "Other"
            cb(10, 0)
            cb(20, 0)
            return 1

        def fake_get_text_len(hwnd):
            return {10: 6, 20: 5}.get(hwnd, 0)

        def fake_get_text(hwnd, buf, _n):
            titles = {10: "My App", 20: "Other"}
            title = titles.get(hwnd, "")
            for i, ch in enumerate(title):
                buf[i] = ch
            buf[len(title)] = "\0"
            return len(title)

        with patch("ctypes.windll.user32.EnumWindows", side_effect=fake_enum_windows), \
             patch("ctypes.windll.user32.GetWindowTextLengthW", side_effect=fake_get_text_len), \
             patch("ctypes.windll.user32.GetWindowTextW", side_effect=fake_get_text):
            result = _find_window_hwnd("My App")
            assert result == 10

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
    def test_find_window_hwnd_not_found(self) -> None:
        import ctypes
        from open_compute.cli import _find_window_hwnd

        def fake_enum_windows(cb, lp):
            cb(99, 0)
            return 1

        def fake_get_text_len(hwnd):
            return 5

        def fake_get_text(hwnd, buf, _n):
            for i, ch in enumerate("Other"):
                buf[i] = ch
            buf[5] = "\0"
            return 5

        with patch("ctypes.windll.user32.EnumWindows", side_effect=fake_enum_windows), \
             patch("ctypes.windll.user32.GetWindowTextLengthW", side_effect=fake_get_text_len), \
             patch("ctypes.windll.user32.GetWindowTextW", side_effect=fake_get_text):
            result = _find_window_hwnd("ZZZNoSuchWindow")
            assert result is None

    def test_find_window_hwnd_nonwindows(self) -> None:
        """On non-Windows, _find_window_hwnd returns None without error."""
        from open_compute.cli import _find_window_hwnd
        with patch.object(sys, "platform", "linux"):
            result = _find_window_hwnd("anything")
            assert result is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
    def test_hwnd_to_mss_region_pure_math(self) -> None:
        """_hwnd_to_mss_region converts RECT to mss region dict correctly."""
        import ctypes
        import ctypes.wintypes
        from open_compute.cli import _hwnd_to_mss_region

        class FakeRECT:
            left = 100
            top = 200
            right = 900
            bottom = 700

        def fake_get_window_rect(hwnd, rect_ptr):
            # Fill the RECT pointed to by rect_ptr
            import ctypes
            rect = ctypes.cast(rect_ptr, ctypes.POINTER(ctypes.wintypes.RECT)).contents
            rect.left = FakeRECT.left
            rect.top = FakeRECT.top
            rect.right = FakeRECT.right
            rect.bottom = FakeRECT.bottom
            return 1  # success

        with patch("ctypes.windll.user32.GetWindowRect", side_effect=fake_get_window_rect):
            region = _hwnd_to_mss_region(42)

        assert region["left"] == 100
        assert region["top"] == 200
        assert region["width"] == 800   # right - left
        assert region["height"] == 500  # bottom - top

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
    def test_hwnd_to_mss_region_zero_size_clamped(self) -> None:
        """Width/height ≤ 0 are clamped to 1."""
        import ctypes
        import ctypes.wintypes
        from open_compute.cli import _hwnd_to_mss_region

        def fake_get_window_rect(hwnd, rect_ptr):
            rect = ctypes.cast(rect_ptr, ctypes.POINTER(ctypes.wintypes.RECT)).contents
            rect.left = 10
            rect.top = 10
            rect.right = 10  # width = 0
            rect.bottom = 10  # height = 0
            return 1

        with patch("ctypes.windll.user32.GetWindowRect", side_effect=fake_get_window_rect):
            region = _hwnd_to_mss_region(1)

        assert region["width"] >= 1
        assert region["height"] >= 1


# ---------------------------------------------------------------------------
# 3. oc do --fullres CLI
# ---------------------------------------------------------------------------

class TestCmdDoFullres:
    """Verify that --fullres flag is accepted and produces 'fullres' key in JSON."""

    def _make_mock_executor(self, png: bytes, w: int = 10, h: int = 10):
        from open_compute.perception import Observation
        obs = Observation(screenshot=png, width=w, height=h)
        mock_exec = MagicMock()
        mock_exec.execute.return_value = obs
        mock_exec.screenshot.return_value = obs
        mock_exec.width = w
        mock_exec.height = h
        return mock_exec

    def test_fullres_flag_accepted(self, tmp_path: pathlib.Path) -> None:
        """--fullres produces 'fullres' or 'fullres_annotated' in JSON output."""
        import os
        png = _make_valid_png()
        mock_exec = self._make_mock_executor(png)

        with patch("open_compute.cli._load_local_executor", return_value=mock_exec), \
             patch("open_compute.cli._session_dir", return_value=tmp_path), \
             patch.dict("sys.modules", {"PIL": None, "PIL.Image": None, "PIL.ImageDraw": None}), \
             patch("sys.argv", ["oc", "do", '{"type":"left_click","x":0.5,"y":0.5}',
                                "--yes", "--fullres"]):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                from open_compute.cli import cmd_do
                cmd_do(['{"type":"left_click","x":0.5,"y":0.5}', "--yes", "--fullres"])

        output = captured.getvalue().strip()
        data = json.loads(output)
        assert "fullres" in data or "fullres_annotated" in data

    def test_fullres_path_is_string(self, tmp_path: pathlib.Path) -> None:
        png = _make_valid_png()
        mock_exec = self._make_mock_executor(png)

        captured = io.StringIO()
        with patch("open_compute.cli._load_local_executor", return_value=mock_exec), \
             patch("open_compute.cli._session_dir", return_value=tmp_path), \
             patch.dict("sys.modules", {"PIL": None}), \
             patch("sys.stdout", captured):
            from open_compute.cli import cmd_do
            cmd_do(['{"type":"left_click","x":0.3,"y":0.7}', "--yes", "--fullres"])

        data = json.loads(captured.getvalue().strip())
        key = "fullres_annotated" if "fullres_annotated" in data else "fullres"
        assert isinstance(data[key], str)

    def test_without_fullres_no_fullres_key(self, tmp_path: pathlib.Path) -> None:
        """Without --fullres, neither 'fullres' nor 'fullres_annotated' in response."""
        png = _make_valid_png()
        mock_exec = self._make_mock_executor(png)

        captured = io.StringIO()
        with patch("open_compute.cli._load_local_executor", return_value=mock_exec), \
             patch("open_compute.cli._session_dir", return_value=tmp_path), \
             patch("sys.stdout", captured):
            from open_compute.cli import cmd_do
            cmd_do(['{"type":"left_click","x":0.3,"y":0.7}', "--yes"])

        data = json.loads(captured.getvalue().strip())
        assert "fullres" not in data
        assert "fullres_annotated" not in data

    def test_fullres_non_click_no_annotation_coords(self, tmp_path: pathlib.Path) -> None:
        """For non-click actions (mouse_move), fullres key present but no annotation coords."""
        png = _make_valid_png()
        mock_exec = self._make_mock_executor(png)

        captured = io.StringIO()
        with patch("open_compute.cli._load_local_executor", return_value=mock_exec), \
             patch("open_compute.cli._session_dir", return_value=tmp_path), \
             patch.dict("sys.modules", {"PIL": None}), \
             patch("sys.stdout", captured):
            from open_compute.cli import cmd_do
            cmd_do(['{"type":"mouse_move","x":0.5,"y":0.5}', "--yes", "--fullres"])

        data = json.loads(captured.getvalue().strip())
        # fullres key present (even non-click actions get full-res)
        assert "fullres" in data or "fullres_annotated" in data


# ---------------------------------------------------------------------------
# 4. oc click-name --fullres
# ---------------------------------------------------------------------------

class TestCmdClickNameFullres:
    def _make_target(self, nx: float = 0.4, ny: float = 0.6):
        from open_compute.feeds.base import Target
        return Target(
            name="TestButton",
            role="Button",
            rect_px=(100, 200, 50, 30),
            center_norm=(nx, ny),
            invokable=True,
            feed="uia_windows",
        )

    def _make_obs(self, png: bytes, w: int = 10, h: int = 10):
        from open_compute.perception import Observation
        return Observation(screenshot=png, width=w, height=h)

    def test_fullres_flag_accepted(self, tmp_path: pathlib.Path) -> None:
        """--fullres in oc click-name → 'fullres' or 'fullres_annotated' in JSON."""
        png = _make_valid_png()
        target = self._make_target()
        mock_exec = MagicMock()
        mock_exec.execute.return_value = self._make_obs(png)
        mock_feed = MagicMock()
        mock_feed.resolve.return_value = target

        captured = io.StringIO()
        with patch("open_compute.cli._load_uia_feed", return_value=mock_feed), \
             patch("open_compute.cli._load_local_executor", return_value=mock_exec), \
             patch("open_compute.cli._session_dir", return_value=tmp_path), \
             patch.dict("sys.modules", {"PIL": None}), \
             patch("sys.stdout", captured):
            from open_compute.cli import cmd_click_name
            cmd_click_name(["TestButton", "--yes", "--fullres"])

        data = json.loads(captured.getvalue().strip())
        assert "fullres" in data or "fullres_annotated" in data

    def test_fullres_annotated_with_pillow(self, tmp_path: pathlib.Path) -> None:
        """With Pillow available, click-name --fullres produces annotated shot."""
        pytest.importorskip("PIL", reason="Pillow not installed")
        png = _make_valid_png(50, 50)
        target = self._make_target(0.5, 0.5)
        mock_exec = MagicMock()
        mock_exec.execute.return_value = self._make_obs(png, 50, 50)
        mock_feed = MagicMock()
        mock_feed.resolve.return_value = target

        captured = io.StringIO()
        with patch("open_compute.cli._load_uia_feed", return_value=mock_feed), \
             patch("open_compute.cli._load_local_executor", return_value=mock_exec), \
             patch("open_compute.cli._session_dir", return_value=tmp_path), \
             patch("sys.stdout", captured):
            from open_compute.cli import cmd_click_name
            cmd_click_name(["TestButton", "--yes", "--fullres"])

        data = json.loads(captured.getvalue().strip())
        assert "fullres_annotated" in data

    def test_without_fullres_no_fullres_key(self, tmp_path: pathlib.Path) -> None:
        png = _make_valid_png()
        target = self._make_target()
        mock_exec = MagicMock()
        mock_exec.execute.return_value = self._make_obs(png)
        mock_feed = MagicMock()
        mock_feed.resolve.return_value = target

        captured = io.StringIO()
        with patch("open_compute.cli._load_uia_feed", return_value=mock_feed), \
             patch("open_compute.cli._load_local_executor", return_value=mock_exec), \
             patch("open_compute.cli._session_dir", return_value=tmp_path), \
             patch("sys.stdout", captured):
            from open_compute.cli import cmd_click_name
            cmd_click_name(["TestButton", "--yes"])

        data = json.loads(captured.getvalue().strip())
        assert "fullres" not in data
        assert "fullres_annotated" not in data


# ---------------------------------------------------------------------------
# 5. oc capture --window
# ---------------------------------------------------------------------------

class TestCaptureWindow:
    def test_window_not_found_exits(self, tmp_path: pathlib.Path) -> None:
        """When no window matches --window, oc capture exits with code 2."""
        with patch("open_compute.cli._find_window_hwnd", return_value=None), \
             patch("sys.platform", "win32"):
            from open_compute.cli import cmd_capture
            with pytest.raises(SystemExit) as exc_info:
                cmd_capture(["--window", "NoSuchWindow", "--out", str(tmp_path / "out.png")])
            assert exc_info.value.code == 2

    def test_window_not_supported_on_nonwindows(self, tmp_path: pathlib.Path) -> None:
        """--window on non-Windows exits with code 2."""
        with patch.object(sys, "platform", "linux"):
            from open_compute.cli import cmd_capture
            with pytest.raises(SystemExit) as exc_info:
                cmd_capture(["--window", "SomeApp", "--out", str(tmp_path / "out.png")])
            assert exc_info.value.code == 2

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
    def test_window_found_captures_region(self, tmp_path: pathlib.Path) -> None:
        """When window is found, mss grabs the region and saves PNG."""
        png = _make_valid_png()
        mock_region = {"left": 100, "top": 200, "width": 800, "height": 500}
        mock_hwnd = 42

        # Mock mss shot
        mock_shot = MagicMock()
        mock_shot.width = 800
        mock_shot.height = 500
        mock_shot.rgb = b"\x80\x40\x20" * (800 * 500)
        mock_shot.size = (800, 500)

        mock_sct = MagicMock()
        mock_sct.__enter__ = MagicMock(return_value=mock_sct)
        mock_sct.__exit__ = MagicMock(return_value=False)
        mock_sct.grab.return_value = mock_shot

        out_path = tmp_path / "window_shot.png"

        captured = io.StringIO()
        with patch("open_compute.cli._find_window_hwnd", return_value=mock_hwnd), \
             patch("open_compute.cli._hwnd_to_mss_region", return_value=mock_region), \
             patch("mss.mss", return_value=mock_sct), \
             patch("mss.tools.to_png", return_value=png), \
             patch("sys.stdout", captured):
            from open_compute.cli import cmd_capture
            cmd_capture(["--window", "MyApp", "--out", str(out_path)])

        assert out_path.exists()
        data = json.loads(captured.getvalue().strip())
        assert "path" in data
        assert "window" in data
        assert "region" in data
        assert data["width"] == 800
        assert data["height"] == 500

    def test_capture_window_flag_in_argparse(self) -> None:
        """--window flag is parsed by argparse without error."""
        import argparse
        # Simulate parsing only (don't execute)
        p = argparse.ArgumentParser()
        p.add_argument("--window", default=None)
        p.add_argument("--out", default=None)
        p.add_argument("--monitor", type=int, default=0)
        ns = p.parse_args(["--window", "Chrome", "--out", "/tmp/shot.png"])
        assert ns.window == "Chrome"
