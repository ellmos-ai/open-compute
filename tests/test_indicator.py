from __future__ import annotations

import json
import sys

import pytest

from open_compute.indicator import (
    MODE_SIGNALS,
    ConsoleAbortChannel,
    NullAbortChannel,
    NullOverlayRenderer,
    ScreenSignalIndicator,
    WindowsBorderOverlay,
    signal_for_mode,
)
from open_compute.session import SessionMode


class FakeRenderer:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple] = []
        self.visible = False

    def show(self, *, color, label, border=True, cursor=True) -> None:
        self.calls.append((color, label, border, cursor))
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def is_visible(self) -> bool:
        return self.visible


class FakeAbortChannel:
    def __init__(self, reason: str | None) -> None:
        self.reason = reason
        self.contexts: list[str] = []

    def prompt_reason(self, *, context: str) -> str | None:
        self.contexts.append(context)
        return self.reason


def test_palette_covers_every_session_mode() -> None:
    assert set(MODE_SIGNALS) == set(SessionMode)


def test_observe_and_control_have_distinct_colors() -> None:
    """Andere Farbe, wenn das Modell nur beobachtet (Kernanforderung)."""
    observe_label, observe_color = signal_for_mode(SessionMode.OBSERVE)
    control_label, control_color = signal_for_mode(SessionMode.CONTROL)
    assert observe_color != control_color
    assert observe_color[2] > observe_color[0]  # blau-toenig
    assert control_color[0] > control_color[2]  # rot-toenig
    assert observe_label != control_label


def test_signal_for_mode_accepts_strings() -> None:
    assert signal_for_mode("observe") == signal_for_mode(SessionMode.OBSERVE)


def test_indicator_show_renders_mode_color_and_label() -> None:
    renderer = FakeRenderer()
    indicator = ScreenSignalIndicator(renderer=renderer)

    indicator.show(agent="kimi", scope="screen", mode=SessionMode.CONTROL)

    assert renderer.is_visible() is True
    color, label, _border, _cursor = renderer.calls[-1]
    assert color == MODE_SIGNALS[SessionMode.CONTROL][1]
    assert "kimi" in label and "screen" in label
    assert indicator.last_label == label


def test_indicator_clear_hides_overlay() -> None:
    renderer = FakeRenderer()
    indicator = ScreenSignalIndicator(renderer=renderer)
    indicator.show(agent="kimi", scope="screen", mode=SessionMode.OBSERVE)

    indicator.clear()

    assert renderer.is_visible() is False
    assert indicator.last_label == ""


def test_abort_message_is_captured_and_forwarded_to_model() -> None:
    forwarded: list[str] = []
    indicator = ScreenSignalIndicator(
        abort_channel=FakeAbortChannel("falscher Ordner"),
        on_abort_message=forwarded.append,
    )

    message = indicator.capture_abort_message()

    assert message == "falscher Ordner"
    assert forwarded == ["falscher Ordner"]


def test_abort_context_defaults_to_last_label() -> None:
    channel = FakeAbortChannel("grund")
    indicator = ScreenSignalIndicator(
        renderer=FakeRenderer(), abort_channel=channel
    )
    indicator.show(agent="kimi", scope="window:42", mode=SessionMode.CONTROL)

    indicator.capture_abort_message()

    assert channel.contexts == [indicator.last_label]


def test_empty_abort_reason_triggers_no_callback() -> None:
    forwarded: list[str] = []
    indicator = ScreenSignalIndicator(
        abort_channel=FakeAbortChannel(None),
        on_abort_message=forwarded.append,
    )

    assert indicator.capture_abort_message() is None
    assert forwarded == []


def test_null_implementations_are_safe_noops() -> None:
    indicator = ScreenSignalIndicator(
        renderer=NullOverlayRenderer(), abort_channel=NullAbortChannel()
    )
    indicator.show(agent="a", scope="s", mode=SessionMode.PAUSED)
    indicator.clear()
    assert indicator.renderer.is_visible() is False
    assert indicator.capture_abort_message() is None


def test_console_channel_strips_input_and_maps_empty_to_none() -> None:
    channel = ConsoleAbortChannel(input_fn=lambda _prompt: "  stop jetzt  ")
    assert channel.prompt_reason(context="x") == "stop jetzt"

    empty = ConsoleAbortChannel(input_fn=lambda _prompt: "   ")
    assert empty.prompt_reason(context="x") is None

    def _eof(_prompt: str) -> str:
        raise EOFError

    eof = ConsoleAbortChannel(input_fn=_eof)
    assert eof.prompt_reason(context="x") is None


def test_windows_overlay_rejects_bad_thickness() -> None:
    if sys.platform != "win32":
        with pytest.raises(RuntimeError, match="Windows-only"):
            WindowsBorderOverlay()
    else:
        with pytest.raises(ValueError, match="thickness"):
            WindowsBorderOverlay(thickness=1)


def test_cli_signal_on_off_cycle(tmp_path, monkeypatch, capsys) -> None:
    from open_compute import cli

    monkeypatch.setattr(cli, "WindowsBorderOverlay", FakeRenderer, raising=False)
    cli.cmd_signal(["on", "--mode", "observe", "--agent", "kimi", "--for", "0"])

    lines = capsys.readouterr().out.strip().splitlines()
    shown = json.loads(lines[0])
    hidden = json.loads(lines[1])
    assert shown["visible"] is True
    assert shown["mode"] == "observe"
    assert shown["color"] == list(MODE_SIGNALS[SessionMode.OBSERVE][1])
    assert "kimi" in shown["label"]
    assert hidden["visible"] is False


def test_cli_signal_abort_none_channel(capsys) -> None:
    from open_compute import cli

    cli.cmd_signal(["abort", "--channel", "none", "--context", "test"])

    assert json.loads(capsys.readouterr().out) == {"abort_message": None}


def test_cli_signal_abort_console_channel(monkeypatch, capsys) -> None:
    from open_compute import cli

    monkeypatch.setattr(
        cli,
        "ConsoleAbortChannel",
        lambda: FakeAbortChannel("mache ich selbst"),
        raising=False,
    )
    cli.cmd_signal(["abort", "--channel", "console"])

    assert json.loads(capsys.readouterr().out) == {
        "abort_message": "mache ich selbst"
    }
