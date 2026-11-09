"""`oc signal off` and invisible-character tolerant window-title matching (T-20261031-479065591).

Pure unit tests: a fake overlay renderer, no GUI, no real window enumeration.
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from open_compute import cli


class FakeRenderer:
    def __init__(self, **kwargs) -> None:
        self.visible = False

    def show(self, *, color, label, border=True, cursor=True) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def is_visible(self) -> bool:
        return self.visible


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_SESSION_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "WindowsBorderOverlay", FakeRenderer, raising=False)
    return tmp_path


def _json_lines(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip()]


# --- oc signal off -----------------------------------------------------------

def test_signal_off_is_idempotent_when_nothing_is_showing(session, capsys) -> None:
    cli.cmd_signal(["off"])
    cli.cmd_signal(["off"])
    out = _json_lines(capsys)
    assert out == [{"visible": False, "was_visible": False}] * 2


def test_signal_off_stops_a_running_signal_on(session, capsys) -> None:
    worker = threading.Thread(
        target=cli.cmd_signal, args=(["on", "--mode", "observe", "--agent", "t"],), daemon=True
    )
    worker.start()
    deadline = time.monotonic() + 5
    while not list(session.glob("signal-active*")) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert list(session.glob("signal-active*")), "signal on must announce itself"

    cli.cmd_signal(["off", "--timeout", "5"])
    worker.join(5)

    assert not worker.is_alive(), "signal on must end after signal off"
    lines = _json_lines(capsys)
    assert lines[0]["visible"] is True              # from `on`
    assert {"visible": False, "was_visible": True, "stopped": True} in lines
    assert not list(session.glob("signal-active*")) and not list(session.glob("signal-stop*"))


def _start_signal_on(session) -> threading.Thread:
    worker = threading.Thread(
        target=cli.cmd_signal, args=(["on", "--mode", "observe", "--agent", "t"],), daemon=True
    )
    worker.start()
    _wait(lambda: (session / "signal-active.json").exists())
    return worker


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.05)
    return bool(predicate())


def test_signal_on_recreates_its_marker_when_off_swept_it_as_stale(session) -> None:
    worker = _start_signal_on(session)
    marker = session / "signal-active.json"
    marker.unlink()  # what `signal off` does to a marker it believes is stale

    assert _wait(marker.exists), "a stalled overlay must make itself stoppable again"
    cli.cmd_signal(["off", "--timeout", "5"])
    worker.join(5)
    assert not worker.is_alive()


def test_signal_on_leaves_a_marker_owned_by_another_overlay_process(session) -> None:
    worker = _start_signal_on(session)
    marker = session / "signal-active.json"
    marker.write_text(json.dumps({"pid": os.getpid() + 100000}), encoding="utf-8")
    (session / "signal-stop.flag").write_text("stop", encoding="utf-8")

    worker.join(5)

    assert not worker.is_alive()
    assert marker.exists(), "the second overlay's marker must survive the first one's exit"


def test_signal_off_ignores_a_stale_marker_of_a_dead_overlay(session, capsys) -> None:
    marker = session / "signal-active.json"
    marker.write_text(json.dumps({"pid": 1}), encoding="utf-8")
    old = time.time() - 600
    os.utime(marker, (old, old))

    cli.cmd_signal(["off"])

    assert _json_lines(capsys) == [{"visible": False, "was_visible": False}]
    assert not marker.exists()


def test_signal_on_with_duration_zero_leaves_no_marker(session, capsys) -> None:
    cli.cmd_signal(["on", "--mode", "observe", "--agent", "t", "--for", "0"])
    assert not list(session.glob("signal-active*"))


def test_signal_off_recognizes_overlay_older_than_three_seconds_with_live_pid(session, capsys) -> None:
    # Marker is 4 seconds old (older than former 3.0s threshold), but PID is alive (current process)
    marker = session / "signal-active.json"
    marker.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    four_sec_ago = time.time() - 4.0
    os.utime(marker, (four_sec_ago, four_sec_ago))

    def _remove_marker_on_stop():
        stop = session / "signal-stop.flag"
        if _wait(stop.exists, timeout=2.0):
            marker.unlink(missing_ok=True)

    t = threading.Thread(target=_remove_marker_on_stop, daemon=True)
    t.start()

    cli.cmd_signal(["off", "--timeout", "3"])
    t.join(3)

    lines = _json_lines(capsys)
    assert any(line.get("was_visible") is True and line.get("stopped") is True for line in lines)


def test_signal_off_sweeps_marker_with_dead_pid(session, capsys) -> None:
    marker = session / "signal-active.json"
    marker.write_text(json.dumps({"pid": 9999999}), encoding="utf-8")
    cli.cmd_signal(["off"])
    assert _json_lines(capsys) == [{"visible": False, "was_visible": False}]
    assert not marker.exists()


def test_multiple_signal_on_instances_cleanup_and_off(session, capsys) -> None:
    # Start two concurrent signal on overlays in the same session
    w1 = threading.Thread(
        target=cli.cmd_signal, args=(["on", "--mode", "observe", "--agent", "a1", "--for", "0.4"],), daemon=True
    )
    w2 = threading.Thread(
        target=cli.cmd_signal, args=(["on", "--mode", "control", "--agent", "a2", "--for", "10"],), daemon=True
    )
    w1.start()
    w2.start()

    # Wait until both have created their instance markers
    assert _wait(lambda: len(list(session.glob("signal-active-*.json"))) >= 2, timeout=5.0)

    # Wait for w1 to finish its duration
    w1.join(timeout=3)
    assert not w1.is_alive()

    # w2 must still be alive and have an active marker
    assert w2.is_alive()
    assert list(session.glob("signal-active*"))

    # Now call signal off: it must detect the running w2, stop it, and report was_visible=True, stopped=True
    cli.cmd_signal(["off", "--timeout", "5"])
    w2.join(timeout=5)
    assert not w2.is_alive()

    lines = _json_lines(capsys)
    assert any(line.get("was_visible") is True and line.get("stopped") is True for line in lines)
    assert not list(session.glob("signal-active*")) and not list(session.glob("signal-stop*"))


# --- invisible characters in window titles ---------------------------------------

EDGE_TITLE = "Persönlich – Microsoft\u200b Edge"

ALL_INVISIBLE_CHARS = [
    "\u200b",  # Zero Width Space
    "\u200c",  # Zero Width Non-Joiner
    "\u200d",  # Zero Width Joiner
    "\ufeff",  # Byte Order Mark / ZWNBSP
    "\u2060",  # Word Joiner
    "\u00ad",  # Soft Hyphen
    "\u200e",  # Left-to-Right Mark
    "\u200f",  # Right-to-Left Mark
    "\u202a",  # Left-to-Right Embedding
    "\u202b",  # Right-to-Left Embedding
    "\u202c",  # Pop Directional Formatting
    "\u202d",  # Left-to-Right Override
    "\u202e",  # Right-to-Left Override
    "\u2066",  # Left-to-Right Isolate
    "\u2067",  # Right-to-Left Isolate
    "\u2068",  # First Strong Isolate
    "\u2069",  # Pop Directional Isolate
]


@pytest.mark.parametrize("invisible", ALL_INVISIBLE_CHARS)
def test_normalize_window_title_drops_invisible_format_characters(invisible: str) -> None:
    from open_compute.window_control import normalize_window_title

    assert normalize_window_title(f"Micro{invisible}soft{invisible}  Edge ") == "Microsoft Edge"


def test_uia_window_name_normalization_tolerates_the_edge_zero_width_space() -> None:
    from open_compute.feeds.uia_windows import _normalize_window_name

    assert "microsoft edge" in _normalize_window_name(EDGE_TITLE).lower()
    assert _normalize_window_name("Microsoft\u200b Edge").lower() == _normalize_window_name("microsoft edge").lower()


@pytest.mark.parametrize("invisible", ALL_INVISIBLE_CHARS)
def test_window_selector_matches_title_with_all_invisible_chars(invisible: str) -> None:
    from open_compute.window_control import resolve_window

    title = f"Document{invisible} 1"
    windows = [{"hwnd": 1, "pid": 2, "title": title}, {"hwnd": 3, "pid": 4, "title": "Editor"}]
    assert resolve_window(windows, title="Document 1")["hwnd"] == 1
    assert resolve_window([{"hwnd": 5, "pid": 6, "title": "Document 1"}], title=f"Document{invisible} 1")["hwnd"] == 5


def test_window_selector_matches_a_title_with_zero_width_space() -> None:
    from open_compute.window_control import resolve_window

    windows = [{"hwnd": 1, "pid": 2, "title": EDGE_TITLE}, {"hwnd": 3, "pid": 4, "title": "Editor"}]
    assert resolve_window(windows, title="Microsoft Edge")["hwnd"] == 1
    # a query that itself carries the invisible character matches the plain title too
    assert resolve_window([{"hwnd": 5, "pid": 6, "title": "Microsoft Edge"}], title="Microsoft\u200b Edge")["hwnd"] == 5


def test_should_activate_ignores_invisible_characters_in_the_foreground_title() -> None:
    assert cli._should_activate(EDGE_TITLE, "microsoft edge", always=False) is False
    assert cli._should_activate("Notepad", "microsoft edge", always=False) is True


# --- _find_window_hwnd ambiguity handling ---------------------------------------

def test_find_window_hwnd_ambiguous_raises_window_ambiguous_error(monkeypatch) -> None:
    import sys
    from unittest.mock import patch
    from open_compute.window_control import WindowAmbiguousError

    if sys.platform != "win32":
        pytest.skip("Win32 only")

    titles = {101: "My Application - 1", 102: "My Application - 2"}

    def fake_enum_windows(cb, lp):
        # Two matching windows
        cb(101, 0)
        cb(102, 0)
        return 1

    def fake_get_text_len(hwnd):
        return len(titles.get(hwnd, ""))

    def fake_get_text(hwnd, buf, _n):
        title = titles.get(hwnd, "")
        for i, ch in enumerate(title):
            buf[i] = ch
        buf[len(title)] = "\0"
        return len(title)

    with patch("ctypes.windll.user32.EnumWindows", side_effect=fake_enum_windows), \
         patch("ctypes.windll.user32.GetWindowTextLengthW", side_effect=fake_get_text_len), \
         patch("ctypes.windll.user32.GetWindowTextW", side_effect=fake_get_text):
        with pytest.raises(WindowAmbiguousError) as exc_info:
            cli._find_window_hwnd("My Application")
        assert len(exc_info.value.candidates) == 2
        assert exc_info.value.candidates[0]["hwnd"] == 101
        assert exc_info.value.candidates[1]["hwnd"] == 102
