"""Abort-hotkey additions for the signal overlay (see test_indicator.py)."""

from __future__ import annotations

import json
import sys

import pytest

from open_compute.indicator import WindowsBorderOverlay


def test_overlay_parses_abort_hotkey_at_construction() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only overlay")
    overlay = WindowsBorderOverlay(
        abort_hotkey="ctrl+alt+esc", on_abort=lambda: None
    )
    assert overlay._abort_hotkey == (0x0001 | 0x0002, 0x1B)


def test_overlay_rejects_bad_abort_hotkey_eagerly() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only overlay")
    with pytest.raises(ValueError, match="hotkey"):
        WindowsBorderOverlay(abort_hotkey="ctrl+")


def test_fire_abort_invokes_callback_and_captures_errors() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only overlay")
    fired: list[str] = []
    overlay = WindowsBorderOverlay(on_abort=lambda: fired.append("x"))
    overlay._fire_abort()
    assert fired == ["x"]
    assert overlay.error is None

    def _boom() -> None:
        raise RuntimeError("kaputt")

    broken = WindowsBorderOverlay(on_abort=_boom)
    broken._fire_abort()
    assert isinstance(broken.error, RuntimeError)


def test_cli_signal_on_passes_abort_hotkey_to_renderer(
    monkeypatch, capsys
) -> None:
    from open_compute import cli

    captured: dict = {}

    class SpyRenderer:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def show(self, *, color, label, border=True, cursor=True) -> None:
            pass

        def hide(self) -> None:
            pass

        def is_visible(self) -> bool:
            return False

    monkeypatch.setattr(cli, "WindowsBorderOverlay", SpyRenderer, raising=False)
    cli.cmd_signal(
        ["on", "--mode", "control", "--for", "0", "--abort-hotkey", "f8"]
    )

    assert captured["abort_hotkey"] == "f8"
    assert callable(captured["on_abort"])
    shown = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert shown["mode"] == "control"


def test_cli_chat_console_with_message(monkeypatch, capsys) -> None:
    from open_compute import cli

    class FakeChannel:
        def prompt_reason(self, *, context: str) -> str:
            return "was ist das fuer ein Fenster?"

    monkeypatch.setattr(cli, "ConsoleAbortChannel", FakeChannel, raising=False)
    cli.cmd_chat(["--channel", "console"])

    assert json.loads(capsys.readouterr().out) == {
        "chat_message": "was ist das fuer ein Fenster?",
        "screenshot": None,
    }


def test_cli_chat_attaches_screenshot_path(monkeypatch, capsys, tmp_path) -> None:
    from open_compute import cli

    class FakeChannel:
        def prompt_reason(self, *, context: str) -> str:
            return "schau mal"

    monkeypatch.setattr(cli, "ConsoleAbortChannel", FakeChannel, raising=False)
    fake_png = tmp_path / "shot.png"
    monkeypatch.setattr(
        cli, "_capture_fullscreen_png", lambda: fake_png, raising=False
    )

    cli.cmd_chat(["--channel", "console", "--shot"])

    assert json.loads(capsys.readouterr().out) == {
        "chat_message": "schau mal",
        "screenshot": str(fake_png),
    }
