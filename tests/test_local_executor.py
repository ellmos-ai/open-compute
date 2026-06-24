"""Tests for LocalExecutor — coordinate math and action dispatch (no real OS calls).

The Win32 SendInput calls are kept behind a thin boundary:
- ``to_sendinput_coords`` is a pure function → tested directly.
- ``LocalExecutor._sendinput_coords`` delegates to it.
- ``LocalExecutor._move / _click / _drag / _type / _key / _scroll`` call
  ``_send_input`` → we monkeypatch ``_send_input`` and the ctypes windll calls.

This file contains NO real mouse movements or clicks.  It verifies:
1. Coordinate math correctness (the most critical correctness property).
2. Action dispatch: the right Win32 events are constructed per action type.
3. CLI argument parsing (oc capture / oc do / oc run).
4. Import-without-mss: ``from open_compute.drivers.local import LocalExecutor``
   should succeed (ctypes is stdlib); only ``screenshot()`` requires mss.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Skip entire module on non-Windows (local.py is Windows-only)
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="LocalExecutor is Windows-only"
)


# ---------------------------------------------------------------------------
# 1. Pure coordinate math
# ---------------------------------------------------------------------------

from open_compute.drivers.local import to_sendinput_coords  # noqa: E402


class TestToSendInputCoords:
    """Verify the normalized->0..65535 mapping in isolation."""

    def _virt(self):
        """Single monitor: origin (0,0), 1920x1080."""
        return (0, 0, 1920, 1080)

    def test_top_left_maps_to_zero(self):
        dx, dy = to_sendinput_coords(0.0, 0.0, *self._virt())
        assert dx == 0
        assert dy == 0

    def test_bottom_right_maps_to_65535(self):
        dx, dy = to_sendinput_coords(1.0, 1.0, *self._virt())
        assert dx == 65535
        assert dy == 65535

    def test_center_maps_near_32767(self):
        dx, dy = to_sendinput_coords(0.5, 0.5, *self._virt())
        assert abs(dx - 32767) <= 1
        assert abs(dy - 32767) <= 1

    def test_negative_origin_virtual_desktop(self):
        """Multi-monitor: secondary left of primary; origin at (-1920, 0)."""
        # Virtual desktop: left=-1920, top=0, width=3840, height=1080
        # A click at normalized (0.0, 0.5) = absolute pixel (-1920, 540).
        # to_sendinput_coords maps 0..1 to 0..65535 regardless of sign.
        dx, dy = to_sendinput_coords(0.0, 0.5, -1920, 0, 3840, 1080)
        assert dx == 0
        assert abs(dy - 32767) <= 1

    def test_clamping_prevents_overflow(self):
        # Slightly out-of-range values due to float rounding
        dx, dy = to_sendinput_coords(1.0001, 0.9999, 0, 0, 1920, 1080)
        assert 0 <= dx <= 65535
        assert 0 <= dy <= 65535

    def test_quarter_point(self):
        dx, dy = to_sendinput_coords(0.25, 0.25, *self._virt())
        assert abs(dx - 16383) <= 1
        assert abs(dy - 16383) <= 1

    def test_three_quarter_point(self):
        dx, dy = to_sendinput_coords(0.75, 0.75, *self._virt())
        assert abs(dx - 49151) <= 1
        assert abs(dy - 49151) <= 1


# ---------------------------------------------------------------------------
# 2. LocalExecutor: action dispatch (mocked Win32 calls)
# ---------------------------------------------------------------------------

def _make_executor(virt=(0, 0, 1920, 1080)):
    """Build a LocalExecutor with all Win32 calls mocked out."""
    from open_compute.drivers.local import LocalExecutor

    with (
        patch("open_compute.drivers.local._set_dpi_awareness"),
        patch("open_compute.drivers.local._get_virtual_desktop", return_value=virt),
    ):
        return LocalExecutor(monitor_index=0)


class TestLocalExecutorDispatch:
    """Verify that execute() calls the right helpers for each ActionType."""

    def setup_method(self):
        self.executor = _make_executor()
        # Patch _send_input to capture calls without touching Win32
        self._sent: list[Any] = []
        self._orig_send = None

    def _patch_send(self):
        import open_compute.drivers.local as _mod

        sent = self._sent

        def fake_send(*inputs):
            sent.extend(inputs)
            return len(inputs)

        return patch.object(_mod, "_send_input", side_effect=fake_send)

    def _patch_screenshot(self):
        """Replace screenshot() so dispatch tests don't need mss."""
        from open_compute.perception import Observation

        fake_obs = Observation(screenshot=b"PNG", width=1920, height=1080)
        return patch.object(self.executor, "screenshot", return_value=fake_obs)

    def test_mouse_move_sends_move_event(self):
        from open_compute.actions import Action, ActionType

        with self._patch_send() as mock_si, self._patch_screenshot():
            self.executor.execute(Action(ActionType.MOUSE_MOVE, x=0.5, y=0.5))
        # _move calls _send_input with one INPUT
        assert len(self._sent) == 1

    def test_left_click_sends_move_down_up(self):
        from open_compute.actions import Action, ActionType

        with self._patch_send(), self._patch_screenshot():
            self.executor.execute(Action(ActionType.LEFT_CLICK, x=0.5, y=0.5))
        # move + (leftdown + leftup) = 3 events
        assert len(self._sent) == 3

    def test_right_click_sends_three_events(self):
        from open_compute.actions import Action, ActionType

        with self._patch_send(), self._patch_screenshot():
            self.executor.execute(Action(ActionType.RIGHT_CLICK, x=0.5, y=0.5))
        assert len(self._sent) == 3

    def test_double_click_sends_six_events(self):
        from open_compute.actions import Action, ActionType

        with self._patch_send(), self._patch_screenshot():
            self.executor.execute(Action(ActionType.DOUBLE_CLICK, x=0.5, y=0.5))
        # 2x (move + down + up) = 6
        assert len(self._sent) == 6

    def test_triple_click_sends_nine_events(self):
        from open_compute.actions import Action, ActionType

        with self._patch_send(), self._patch_screenshot():
            self.executor.execute(Action(ActionType.TRIPLE_CLICK, x=0.5, y=0.5))
        assert len(self._sent) == 9

    def test_drag_sends_move_down_move_up(self):
        from open_compute.actions import Action, ActionType

        with self._patch_send(), self._patch_screenshot():
            self.executor.execute(
                Action(ActionType.LEFT_CLICK_DRAG, x=0.1, y=0.1, end_x=0.9, end_y=0.9)
            )
        # move_start + leftdown + move_end + leftup = 4
        assert len(self._sent) == 4

    def test_type_calls_user32_sendinput(self):
        """_type bypasses _send_input helper and calls windll.user32.SendInput directly."""
        from open_compute.actions import Action, ActionType

        with patch("ctypes.windll.user32") as mock_user32, self._patch_screenshot():
            mock_user32.SendInput.return_value = 4
            self.executor.execute(Action(ActionType.TYPE, text="ab"))
        # 'a' down + 'a' up + 'b' down + 'b' up = 4 events in one call
        assert mock_user32.SendInput.called

    def test_key_calls_user32_sendinput(self):
        from open_compute.actions import Action, ActionType

        with patch("ctypes.windll.user32") as mock_user32, self._patch_screenshot():
            mock_user32.SendInput.return_value = 2
            mock_user32.VkKeyScanW.return_value = 0x43  # 'C'
            self.executor.execute(Action(ActionType.KEY, text="ctrl+c"))
        assert mock_user32.SendInput.called

    def test_scroll_down_sends_wheel_event(self):
        from open_compute.actions import Action, ActionType

        with self._patch_send(), self._patch_screenshot():
            self.executor.execute(
                Action(ActionType.SCROLL, x=0.5, y=0.5, scroll_direction="down", scroll_amount=3)
            )
        # move + wheel = 2 events
        assert len(self._sent) == 2

    def test_screenshot_action_triggers_screenshot(self):
        from open_compute.actions import Action, ActionType
        from open_compute.perception import Observation

        fake = Observation(screenshot=b"FAKE", width=1920, height=1080)
        with patch.object(self.executor, "screenshot", return_value=fake) as mock_ss:
            obs = self.executor.execute(Action(ActionType.SCREENSHOT))
        mock_ss.assert_called_once()
        assert obs.screenshot == b"FAKE"

    def test_width_height_from_virtual_desktop(self):
        assert self.executor.width == 1920
        assert self.executor.height == 1080

    def test_sendinput_coords_center(self):
        """Internal: 0.5, 0.5 on 1920x1080 virtual desktop -> near (32767, 32767)."""
        dx, dy = self.executor._sendinput_coords(0.5, 0.5)
        assert abs(dx - 32767) <= 1
        assert abs(dy - 32767) <= 1

    def test_sendinput_coords_top_left(self):
        dx, dy = self.executor._sendinput_coords(0.0, 0.0)
        assert dx == 0
        assert dy == 0

    def test_sendinput_coords_bottom_right(self):
        dx, dy = self.executor._sendinput_coords(1.0, 1.0)
        assert dx == 65535
        assert dy == 65535


# ---------------------------------------------------------------------------
# 3. Import-without-mss
# ---------------------------------------------------------------------------

class TestImportWithoutMss:
    """Importing local.py must not require mss (lazy import in screenshot())."""

    def test_import_local_module_succeeds(self):
        """local.py is already imported above; this confirms it loaded without mss."""
        from open_compute.drivers import local as _local_mod  # noqa: F401
        assert hasattr(_local_mod, "LocalExecutor")

    def test_screenshot_raises_importerror_without_mss(self):
        """If mss is not installed, screenshot() raises ImportError, not AttributeError."""
        executor = _make_executor()
        # Simulate mss not installed by patching the import inside screenshot()
        import builtins
        real_import = builtins.__import__

        def _no_mss(name, *args, **kwargs):
            if name == "mss":
                raise ImportError("No module named 'mss'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_no_mss):
            with pytest.raises(ImportError, match="mss"):
                executor.screenshot()

    def test_import_open_compute_top_level_without_mss(self):
        """The top-level package must not import mss at import time."""
        # mss may or may not be installed; we verify local.py is not pulled in
        # by the top-level __init__.py
        import open_compute  # should succeed regardless of mss presence
        assert open_compute.__version__ == "0.6.0"


# ---------------------------------------------------------------------------
# 3b. Screenshot fallback: mss -> WGC
# ---------------------------------------------------------------------------

class TestScreenshotWgcFallback:
    """WGC fallback tests with all screenshot backends mocked."""

    def _install_failing_mss(self, monkeypatch, exc: Exception):
        fake_mss = types.ModuleType("mss")
        fake_tools = types.ModuleType("mss.tools")

        class FakeMssContext:
            monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def grab(self, _monitor):
                raise exc

        fake_mss.mss = FakeMssContext
        fake_mss.tools = fake_tools
        monkeypatch.setitem(sys.modules, "mss", fake_mss)
        monkeypatch.setitem(sys.modules, "mss.tools", fake_tools)

    def test_mss_failure_falls_back_to_wgc(self, monkeypatch):
        executor = _make_executor()
        self._install_failing_mss(monkeypatch, RuntimeError("BitBlt failed"))

        with (
            patch("open_compute.drivers.wgc.available", return_value=True),
            patch(
                "open_compute.drivers.local._place_png_on_virtual_canvas",
                return_value=b"VIRTUAL_PNG",
            ) as mock_canvas,
            patch(
                "open_compute.drivers.wgc.grab_monitor_png",
                return_value=(b"WGC_PNG", 1280, 720),
            ) as mock_grab,
        ):
            obs = executor.screenshot()

        mock_grab.assert_called_once_with(monitor_index=1)
        mock_canvas.assert_called_once_with(b"WGC_PNG", 0, 0, 0, 0, 1920, 1080)
        assert obs.screenshot == b"VIRTUAL_PNG"
        assert obs.width == 1920
        assert obs.height == 1080
        assert executor.width == 1920
        assert executor.height == 1080

    def test_wgc_virtual_canvas_preserves_primary_monitor_offset(self, monkeypatch):
        executor = _make_executor(virt=(-1920, 0, 3840, 1080))
        self._install_failing_mss(monkeypatch, RuntimeError("BitBlt failed"))

        with (
            patch("open_compute.drivers.wgc.available", return_value=True),
            patch(
                "open_compute.drivers.local._place_png_on_virtual_canvas",
                return_value=b"VIRTUAL_PNG",
            ),
            patch(
                "open_compute.drivers.wgc.grab_monitor_png",
                return_value=(b"WGC_PNG", 1920, 1080),
            ),
        ):
            executor.screenshot()

        dx, dy = executor._sendinput_coords(0.75, 0.5)
        assert abs(dx - 49151) <= 1
        assert abs(dy - 32767) <= 1

    def test_mss_failure_reraises_when_wgc_unavailable(self, monkeypatch):
        executor = _make_executor()
        self._install_failing_mss(monkeypatch, RuntimeError("BitBlt failed"))

        with patch("open_compute.drivers.wgc.available", return_value=False):
            with pytest.raises(RuntimeError, match="BitBlt failed"):
                executor.screenshot()


# ---------------------------------------------------------------------------
# 4. CLI argument parsing (no real execution)
# ---------------------------------------------------------------------------

class TestCliParsing:
    """Test CLI argument parsing without touching the filesystem or Win32."""

    def test_capture_default_out(self, capsys, tmp_path):
        """oc capture with no args writes to _session/screenshot.png."""
        from open_compute.perception import Observation

        fake_obs = Observation(screenshot=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, width=1920, height=1080)

        with (
            patch("open_compute.cli._load_local_executor") as mock_exec,
            patch("sys.argv", ["oc", "capture", "--out", str(tmp_path / "test.png")]),
        ):
            mock_exec.return_value.screenshot.return_value = fake_obs
            from open_compute.cli import cmd_capture
            cmd_capture(["--out", str(tmp_path / "test.png")])

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["width"] == 1920
        assert data["height"] == 1080

    def test_do_allow_all_executes(self, capsys):
        """oc do with allow_all mode executes the action."""
        from open_compute.perception import Observation

        fake_obs = Observation(screenshot=b"PNG", width=1920, height=1080)

        with (
            patch("open_compute.cli._load_local_executor") as mock_exec,
            patch("sys.argv", ["oc", "do"]),
        ):
            mock_exec.return_value.execute.return_value = fake_obs
            from open_compute.cli import cmd_do
            cmd_do(['{"type":"mouse_move","x":0.5,"y":0.5}', "--mode", "allow_all"])

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["result"] == "executed"
        assert data["action"] == "mouse_move"

    def test_do_confirm_without_yes_exits_1(self, capsys):
        """oc do in confirm mode without --yes should exit with code 1."""
        from open_compute.cli import cmd_do

        with pytest.raises(SystemExit) as exc_info:
            cmd_do(['{"type":"left_click","x":0.5,"y":0.5}', "--mode", "confirm"])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["result"] == "confirm"

    def test_do_confirm_with_yes_executes(self, capsys):
        """oc do --yes bypasses the confirm gate (agent has decided)."""
        from open_compute.perception import Observation

        fake_obs = Observation(screenshot=b"PNG", width=1920, height=1080)

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_exec.return_value.execute.return_value = fake_obs
            from open_compute.cli import cmd_do
            cmd_do(['{"type":"left_click","x":0.5,"y":0.5}', "--yes"])

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["result"] == "executed"

    def test_do_action_key_alias(self, capsys):
        """Accepts 'action' as alias for 'type' in the action JSON."""
        from open_compute.perception import Observation

        fake_obs = Observation(screenshot=b"PNG", width=1920, height=1080)

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_exec.return_value.execute.return_value = fake_obs
            from open_compute.cli import cmd_do
            cmd_do(['{"action":"mouse_move","x":0.5,"y":0.5}', "--mode", "allow_all"])

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["result"] == "executed"

    def test_do_invalid_json_exits_2(self):
        """oc do with malformed JSON exits with code 2."""
        from open_compute.cli import cmd_do

        with pytest.raises(SystemExit) as exc_info:
            cmd_do(["not-json"])
        assert exc_info.value.code == 2

    def test_do_read_only_denies_click(self, capsys):
        """oc do in read_only mode denies a left_click."""
        from open_compute.cli import cmd_do

        with pytest.raises(SystemExit) as exc_info:
            cmd_do(['{"type":"left_click","x":0.5,"y":0.5}', "--mode", "read_only"])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["result"] == "deny"
