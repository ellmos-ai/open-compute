"""Tests for the three capabilities added after the computer-control-mcp comparison.

1. **WGC window capture** — a GDI grab of a hardware-composited window returns an
   all-black frame instead of failing, so ``capture(window=...)`` must detect the
   blank frame and re-grab through Windows.Graphics.Capture.
2. **Host introspection tools** — ``list_windows`` / ``get_screen_size``.
3. **Hold primitives** — ``mouse_down`` / ``mouse_up`` / ``key_down`` / ``key_up``,
   their safety gating, the held-state tracking and the ``release_all`` safety net.

No real input is synthesized and no real window is captured: Win32 calls are behind
``_send_input`` / the wgc module, both of which are monkeypatched.
"""

from __future__ import annotations

import io
import sys

import pytest

from open_compute.actions import Action, ActionType, to_claude, to_openai
from open_compute.safety import Decision, SafetyPolicy


# ---------------------------------------------------------------------------
# 3a. Hold primitives — canonical schema
# ---------------------------------------------------------------------------

class TestHoldActionSchema:
    def test_mouse_down_defaults_to_no_coordinates(self):
        # A hold at the current cursor position is legal: unlike a click, a
        # mouse_down does not have to name a point.
        action = Action(ActionType.MOUSE_DOWN)
        assert action.x is None and action.button is None

    def test_mouse_down_accepts_point_and_button(self):
        action = Action(ActionType.MOUSE_DOWN, x=0.5, y=0.25, button="right")
        assert (action.x, action.y, action.button) == (0.5, 0.25, "right")

    def test_unknown_button_rejected(self):
        with pytest.raises(ValueError, match="button"):
            Action(ActionType.MOUSE_DOWN, button="scroll")

    @pytest.mark.parametrize("kind", [ActionType.KEY_DOWN, ActionType.KEY_UP])
    def test_key_hold_requires_text(self, kind):
        with pytest.raises(ValueError, match="requires text"):
            Action(kind)

    @pytest.mark.parametrize(
        "kind",
        [ActionType.MOUSE_DOWN, ActionType.MOUSE_UP, ActionType.KEY_DOWN, ActionType.KEY_UP],
    )
    def test_holds_are_host_side(self, kind):
        action = Action(kind, text="ctrl") if "key" in kind.value else Action(kind)
        assert action.is_host_side

    def test_mappers_refuse_holds(self):
        # Neither vendor computer tool has a press/release action; the mappers
        # must say so rather than silently emit a full click.
        action = Action(ActionType.MOUSE_DOWN, x=0.5, y=0.5)
        with pytest.raises(ValueError, match="no Claude computer-tool equivalent"):
            to_claude(action, 1920, 1080)
        with pytest.raises(ValueError, match="no OpenAI computer-tool equivalent"):
            to_openai(action, 1920, 1080)


# ---------------------------------------------------------------------------
# 3b. Hold primitives — safety gate
# ---------------------------------------------------------------------------

class TestHoldSafety:
    def _actions(self):
        return [
            Action(ActionType.MOUSE_DOWN, x=0.1, y=0.1),
            Action(ActionType.MOUSE_UP),
            Action(ActionType.KEY_DOWN, text="ctrl"),
            Action(ActionType.KEY_UP, text="ctrl"),
        ]

    def test_read_only_denies_every_half(self):
        # Both halves are synthesized input; read_only must stay input-free.
        policy = SafetyPolicy(mode="read_only")
        for action in self._actions():
            assert policy.evaluate(action).decision is Decision.DENY

    def test_confirm_gates_every_half(self):
        policy = SafetyPolicy(mode="confirm")
        for action in self._actions():
            assert policy.evaluate(action).decision is Decision.CONFIRM

    def test_allow_all_permits(self):
        policy = SafetyPolicy(mode="allow_all")
        for action in self._actions():
            assert policy.evaluate(action).decision is Decision.ALLOW


# ---------------------------------------------------------------------------
# 3c. Hold primitives — executor dispatch and release_all
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="LocalExecutor is Windows-only")
class TestHoldExecution:
    @pytest.fixture()
    def executor(self, monkeypatch):
        from open_compute.drivers import local

        sent: list = []
        monkeypatch.setattr(local, "_send_input", lambda *inputs: sent.append(inputs))
        ex = local.LocalExecutor()
        monkeypatch.setattr(ex, "screenshot", lambda: None)  # no mss in tests
        ex._sent = sent  # type: ignore[attr-defined]
        return ex

    def test_mouse_down_then_up_tracks_and_clears(self, executor):
        executor.execute(Action(ActionType.MOUSE_DOWN, x=0.5, y=0.5))
        assert executor.held["buttons"] == ["left"]

        executor.execute(Action(ActionType.MOUSE_UP))
        assert executor.held["buttons"] == []

    def test_key_down_records_each_key_of_a_combo(self, executor):
        executor.execute(Action(ActionType.KEY_DOWN, text="ctrl+shift"))
        assert len(executor.held["keys"]) == 2

        executor.execute(Action(ActionType.KEY_UP, text="ctrl+shift"))
        assert executor.held["keys"] == []

    def test_release_all_releases_a_stranded_hold(self, executor):
        # The failure mode this exists for: a client dies between down and up.
        executor.execute(Action(ActionType.MOUSE_DOWN, x=0.2, y=0.2, button="right"))
        executor.execute(Action(ActionType.KEY_DOWN, text="alt"))

        released = executor.release_all()

        assert released["buttons"] == ["right"]
        assert len(released["keys"]) == 1
        assert executor.held == {"buttons": [], "keys": []}

    def test_release_all_is_idempotent(self, executor):
        # Called from both the finally block and the atexit hook.
        executor.execute(Action(ActionType.MOUSE_DOWN, x=0.2, y=0.2))
        executor.release_all()
        assert executor.release_all() == {"buttons": [], "keys": []}

    def test_repeated_mouse_down_holds_the_button_once(self, executor):
        executor.execute(Action(ActionType.MOUSE_DOWN, x=0.1, y=0.1))
        executor.execute(Action(ActionType.MOUSE_DOWN, x=0.9, y=0.9))
        assert executor.held["buttons"] == ["left"]


# ---------------------------------------------------------------------------
# 1. WGC blank-frame detection
# ---------------------------------------------------------------------------

class TestBlankFrameDetection:
    def _png(self, color) -> bytes:
        Image = pytest.importorskip("PIL.Image")
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), color).save(buf, format="PNG")
        return buf.getvalue()

    def test_black_frame_is_blank(self):
        from open_compute.drivers import wgc

        assert wgc.is_blank_png(self._png((0, 0, 0))) is True

    def test_near_black_frame_is_blank(self):
        from open_compute.drivers import wgc

        assert wgc.is_blank_png(self._png((3, 3, 3))) is True

    def test_real_content_is_not_blank(self):
        from open_compute.drivers import wgc

        assert wgc.is_blank_png(self._png((120, 30, 200))) is False

    def test_unreadable_bytes_never_claim_blank(self):
        # The check is an optimization; it must not turn a capture into a failure.
        from open_compute.drivers import wgc

        assert wgc.is_blank_png(b"not-a-png") is False


# ---------------------------------------------------------------------------
# 1b. capture(window=...) falls back to WGC on a blank GDI frame
# ---------------------------------------------------------------------------

pytest.importorskip("mcp")
from open_compute import mcp_server as S  # noqa: E402


class _FakeWGC:
    def __init__(self, available=True):
        self._available = available
        self.grabbed: list[int] = []

    def available(self):
        return self._available

    def is_blank_png(self, png, threshold=8):
        return png == b"BLACK"

    def grab_window_png(self, hwnd, **kw):
        self.grabbed.append(hwnd)
        return b"WGC-FRAME", 800, 600


@pytest.fixture()
def wgc_fallback(monkeypatch):
    """Patch the Win32 window lookup and the wgc module; return the fake wgc."""
    from open_compute import cli
    from open_compute import drivers

    monkeypatch.setattr(sys, "platform", "win32", raising=False)
    monkeypatch.setattr(cli, "_find_window_hwnd", lambda substr: 4242)
    monkeypatch.setattr(cli, "_window_title", lambda hwnd: "Roblox Studio")
    monkeypatch.setattr(cli, "_hwnd_to_mss_region", lambda hwnd: {"left": 0, "top": 0,
                                                                 "width": 8, "height": 8})
    fake = _FakeWGC()
    monkeypatch.setattr(drivers, "wgc", fake, raising=False)
    monkeypatch.setitem(sys.modules, "open_compute.drivers.wgc", fake)
    return fake


def _fake_mss(monkeypatch, png: bytes):
    """Install an mss stub whose grab yields *png*."""
    import types

    class _Shot:
        rgb = b""
        size = (8, 8)

    class _Sct:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self, region):
            return _Shot()

    mss_mod = types.ModuleType("mss")
    tools_mod = types.ModuleType("mss.tools")
    mss_mod.mss = lambda: _Sct()
    tools_mod.to_png = lambda rgb, size: png
    mss_mod.tools = tools_mod
    monkeypatch.setitem(sys.modules, "mss", mss_mod)
    monkeypatch.setitem(sys.modules, "mss.tools", tools_mod)


def test_capture_window_uses_gdi_when_frame_has_content(monkeypatch, wgc_fallback):
    _fake_mss(monkeypatch, b"REAL-PIXELS")

    assert S._capture_window_png("studio") == b"REAL-PIXELS"
    assert wgc_fallback.grabbed == []  # no need to pay for WGC


def test_capture_window_falls_back_to_wgc_on_black_frame(monkeypatch, wgc_fallback):
    # The whole point: GDI *succeeds* and returns black. Without the check we
    # would hand the model a useless all-black screenshot.
    _fake_mss(monkeypatch, b"BLACK")

    assert S._capture_window_png("studio") == b"WGC-FRAME"
    assert wgc_fallback.grabbed == [4242]  # resolved by HWND, not by title


def test_capture_window_falls_back_when_gdi_raises(monkeypatch, wgc_fallback):
    def _boom(hwnd):
        raise OSError("GetWindowRect failed")

    from open_compute import cli
    monkeypatch.setattr(cli, "_hwnd_to_mss_region", _boom)

    assert S._capture_window_png("studio") == b"WGC-FRAME"


def test_black_frame_returned_when_wgc_missing(monkeypatch, wgc_fallback):
    # Degrade, don't fail: without the wgc extra the black frame is all there is.
    _fake_mss(monkeypatch, b"BLACK")
    wgc_fallback._available = False

    assert S._capture_window_png("studio") == b"BLACK"


def test_black_frame_returned_when_wgc_cannot_capture(monkeypatch, wgc_fallback):
    # WGC refuses some windows outright and delivers no frame for an idle one.
    # Neither may fail the tool call when GDI at least produced *something*.
    _fake_mss(monkeypatch, b"BLACK")

    def _refuse(hwnd, **kw):
        raise RuntimeError("Failed to convert item to GraphicsCaptureItem")

    monkeypatch.setattr(wgc_fallback, "grab_window_png", _refuse)

    assert S._capture_window_png("studio") == b"BLACK"


def test_capture_is_bounded_for_an_idle_window(monkeypatch, wgc_fallback):
    # WGC only pushes frames on redraw; an idle window must not hang the client.
    seen: dict = {}

    def _record(hwnd, **kw):
        seen.update(kw)
        return b"WGC-FRAME", 800, 600

    _fake_mss(monkeypatch, b"BLACK")
    monkeypatch.setattr(wgc_fallback, "grab_window_png", _record)

    S._capture_window_png("studio")

    assert seen["retries"] * seen["max_seconds"] <= 8, "worst case must stay bounded"


def test_gdi_failure_with_no_wgc_is_reported(monkeypatch, wgc_fallback):
    from open_compute import cli

    def _boom(hwnd):
        raise OSError("GetWindowRect failed")

    def _refuse(hwnd, **kw):
        raise RuntimeError("no frame")

    monkeypatch.setattr(cli, "_hwnd_to_mss_region", _boom)
    monkeypatch.setattr(wgc_fallback, "grab_window_png", _refuse)

    with pytest.raises(RuntimeError, match="capture of 'Roblox Studio' failed"):
        S._capture_window_png("studio")


def test_oc_wgc_windows_skips_the_gdi_attempt(monkeypatch, wgc_fallback):
    _fake_mss(monkeypatch, b"REAL-PIXELS")
    monkeypatch.setenv("OC_WGC_WINDOWS", "roblox studio, blender")

    assert S._capture_window_png("studio") == b"WGC-FRAME"
    assert wgc_fallback.grabbed == [4242]


def test_unknown_window_is_an_error(monkeypatch, wgc_fallback):
    from open_compute import cli
    monkeypatch.setattr(cli, "_find_window_hwnd", lambda substr: None)

    with pytest.raises(ValueError, match="no window found"):
        S._capture_window_png("does-not-exist")


# ---------------------------------------------------------------------------
# 2. Host introspection tools
# ---------------------------------------------------------------------------

def test_list_windows_tool_returns_driver_output(monkeypatch):
    from open_compute.drivers import local

    fake = [{"title": "Blender", "hwnd": 7, "foreground": True}]
    monkeypatch.setattr(local, "list_windows", lambda visible_only=True: fake)

    assert S.list_windows() == fake


def test_get_screen_size_tool_returns_driver_output(monkeypatch):
    from open_compute.drivers import local

    fake = {"virtual_desktop": {"left": 0, "top": 0, "width": 1920, "height": 1080},
            "monitors": [], "platform": "win32"}
    monkeypatch.setattr(local, "get_screen_size", lambda: fake)

    assert S.get_screen_size() == fake


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 window enumeration")
class TestListWindowsOnHost:
    def test_returns_titled_windows_with_normalized_centers(self):
        from open_compute.drivers.local import list_windows

        windows = list_windows()

        assert windows, "a desktop session always has at least one titled window"
        for w in windows:
            assert w["title"]
            assert 0.0 <= w["center"]["x"] <= 1.0
            assert 0.0 <= w["center"]["y"] <= 1.0
            assert w["rect"]["width"] >= 0

    def test_at_most_one_foreground_window(self):
        from open_compute.drivers.local import list_windows

        assert sum(bool(w["foreground"]) for w in list_windows()) <= 1


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 screen metrics")
def test_get_screen_size_on_host_describes_the_coordinate_frame():
    from open_compute.drivers.local import get_screen_size

    size = get_screen_size()

    assert size["virtual_desktop"]["width"] > 0
    assert size["virtual_desktop"]["height"] > 0


# ---------------------------------------------------------------------------
# 3d. The server's shutdown net
# ---------------------------------------------------------------------------

class _HoldingExec:
    def __init__(self):
        self.released = 0

    def release_all(self):
        self.released += 1
        return {"buttons": ["left"], "keys": []}


def test_shutdown_releases_held_input():
    S._STATE.set_executor(_HoldingExec())

    S._release_held_input()

    assert S._STATE._executor.released == 1


def test_shutdown_does_not_create_an_executor():
    # Must not spin up a LocalExecutor (and grab the DPI context) on the way out.
    S._STATE.set_executor(None)

    S._release_held_input()

    assert S._STATE._executor is None
