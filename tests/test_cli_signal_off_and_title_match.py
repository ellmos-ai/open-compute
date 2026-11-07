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


# --- invisible characters in window titles ---------------------------------------

EDGE_TITLE = "Persönlich – Microsoft\u200b Edge"


@pytest.mark.parametrize("invisible", ["\u200b", "\u200c", "\u200d", "\ufeff"])
def test_normalize_window_title_drops_invisible_format_characters(invisible: str) -> None:
    from open_compute.window_control import normalize_window_title

    assert normalize_window_title(f"Micro{invisible}soft{invisible}  Edge ") == "Microsoft Edge"


def test_uia_window_name_normalization_tolerates_the_edge_zero_width_space() -> None:
    from open_compute.feeds.uia_windows import _normalize_window_name

    assert "microsoft edge" in _normalize_window_name(EDGE_TITLE).lower()
    assert _normalize_window_name("Microsoft\u200b Edge").lower() == _normalize_window_name("microsoft edge").lower()


def test_window_selector_matches_a_title_with_zero_width_space() -> None:
    from open_compute.window_control import resolve_window

    windows = [{"hwnd": 1, "pid": 2, "title": EDGE_TITLE}, {"hwnd": 3, "pid": 4, "title": "Editor"}]
    assert resolve_window(windows, title="Microsoft Edge")["hwnd"] == 1
    # a query that itself carries the invisible character matches the plain title too
    assert resolve_window([{"hwnd": 5, "pid": 6, "title": "Microsoft Edge"}], title="Microsoft\u200b Edge")["hwnd"] == 5


def test_should_activate_ignores_invisible_characters_in_the_foreground_title() -> None:
    assert cli._should_activate(EDGE_TITLE, "microsoft edge", always=False) is False
    assert cli._should_activate("Notepad", "microsoft edge", always=False) is True
