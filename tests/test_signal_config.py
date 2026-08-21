"""Signal-config tests: defaults, overrides, toggles, CLI wiring."""

from __future__ import annotations

import json

import pytest

from open_compute.indicator import (
    DEFAULT_PRE_ACTION_GRACE_COLOR,
    DEFAULT_PRE_ACTION_GRACE_LABEL,
    MODE_SIGNALS,
    ScreenSignalIndicator,
    SignalConfig,
    signal_presentation,
)
from open_compute.session import SessionMode


class SpyRenderer:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.hidden = 0

    def show(self, *, color, label, border=True, cursor=True) -> None:
        self.calls.append(
            {"color": color, "label": label, "border": border, "cursor": cursor}
        )

    def hide(self) -> None:
        self.hidden += 1

    def is_visible(self) -> bool:
        return bool(self.calls) and not self.hidden


def test_default_config_has_a_20s_grace_and_no_quick_reasons() -> None:
    """Ticket T-20260818-895473048: 'signal.pre_action_grace_seconds, Default 20'."""
    cfg = SignalConfig()
    assert cfg.pre_action_grace_seconds == 20.0
    assert cfg.pre_action_grace_color == DEFAULT_PRE_ACTION_GRACE_COLOR
    assert cfg.pre_action_grace_label == DEFAULT_PRE_ACTION_GRACE_LABEL
    assert cfg.pre_action_grace_color not in {
        color for _label, color in MODE_SIGNALS.values()
    }
    assert cfg.abort_reasons == ()


def test_from_dict_reads_grace_seconds_and_abort_reasons() -> None:
    cfg = SignalConfig.from_dict({
        "pre_action_grace_seconds": 5,
        "pre_action_grace_color": [12, 34, 56],
        "pre_action_grace_label": "Beginn in {seconds} Sekunden",
        "abort_reasons": [
            "Ich arbeite gerade selbst",
            "Datengeschuetzter Bereich sichtbar",
            "Falsches Fenster",
        ],
    })
    assert cfg.pre_action_grace_seconds == 5.0
    assert cfg.pre_action_grace_color == (12, 34, 56)
    assert cfg.pre_action_grace_label == "Beginn in {seconds} Sekunden"
    assert cfg.abort_reasons == (
        "Ich arbeite gerade selbst",
        "Datengeschuetzter Bereich sichtbar",
        "Falsches Fenster",
    )


def test_abort_reasons_drops_blank_entries() -> None:
    cfg = SignalConfig(abort_reasons=("real reason", "  ", "", "another"))
    assert cfg.abort_reasons == ("real reason", "another")


def test_config_rejects_negative_grace_seconds() -> None:
    with pytest.raises(ValueError, match="grace"):
        SignalConfig(pre_action_grace_seconds=-1)
    with pytest.raises(ValueError, match="finite"):
        SignalConfig(pre_action_grace_seconds=float("inf"))


def test_grace_seconds_zero_disables_the_countdown() -> None:
    """0 must stay legal — the operator's escape hatch for 'no delay'."""
    cfg = SignalConfig(pre_action_grace_seconds=0)
    assert cfg.pre_action_grace_seconds == 0


def test_config_round_trip_save_load_grace_and_reasons(tmp_path) -> None:
    cfg = SignalConfig.from_dict({
        "pre_action_grace_seconds": 3.5,
        "pre_action_grace_color": [70, 80, 90],
        "pre_action_grace_label": "Start in {seconds} Sekunden",
        "abort_reasons": ["Später erneut", "Falsches Fenster"],
    })
    path = tmp_path / "signal-config.json"
    cfg.save(path)

    loaded = SignalConfig.load(path)
    assert loaded.pre_action_grace_seconds == 3.5
    assert loaded.pre_action_grace_color == (70, 80, 90)
    assert loaded.pre_action_grace_label == "Start in {seconds} Sekunden"
    assert loaded.abort_reasons == ("Später erneut", "Falsches Fenster")


def test_countdown_presentation_is_text_first_and_uses_ceiling() -> None:
    countdown = signal_presentation(
        base_label="codex | CONTROL | screen",
        active_color=(255, 40, 60),
        grace_color=(12, 34, 56),
        grace_label_template="Start in {seconds} Sekunden",
        remaining_seconds=19.01,
    )

    assert countdown.phase == "countdown"
    assert countdown.seconds_remaining == 20
    assert countdown.color == (12, 34, 56)
    assert "Start in 20 Sekunden" in countdown.visual_label
    assert "Start in 20 Sekunden" in countdown.accessible_label
    assert "Abbruch ist jederzeit möglich" in countdown.accessible_label

    active = signal_presentation(
        base_label="codex | CONTROL | screen",
        active_color=(255, 40, 60),
        grace_color=(12, 34, 56),
        grace_label_template="Start in {seconds} Sekunden",
        remaining_seconds=0,
    )
    assert active.phase == "active"
    assert active.seconds_remaining is None
    assert active.color == (255, 40, 60)


def test_default_config_matches_builtin_palette() -> None:
    cfg = SignalConfig()
    assert set(cfg.modes) == set(SessionMode)
    for mode, (label, color) in MODE_SIGNALS.items():
        mode_cfg = cfg.for_mode(mode)
        assert mode_cfg.color == color
        assert mode_cfg.label == label
        assert mode_cfg.enabled is True
        assert mode_cfg.border is True
        assert mode_cfg.cursor is True


def test_from_dict_partial_override_keeps_other_modes_default() -> None:
    cfg = SignalConfig.from_dict({
        "modes": {
            "control": {"color": [255, 0, 0], "border": False, "cursor": True}
        }
    })
    control = cfg.for_mode("control")
    assert control.color == (255, 0, 0)
    assert control.border is False
    assert control.cursor is True
    # untouched mode keeps the built-in default
    assert cfg.for_mode("observe").color == MODE_SIGNALS[SessionMode.OBSERVE][1]
    assert cfg.for_mode("observe").border is True


def test_usecase_cursor_only_when_controlling_no_border() -> None:
    """Usecase: Maus-Hervorhebung nur bei aktiver Steuerung, ohne Rahmen."""
    cfg = SignalConfig.from_dict({
        "modes": {
            "control": {"border": False, "cursor": True},
            "observe": {"enabled": False},
        }
    })
    renderer = SpyRenderer()
    indicator = ScreenSignalIndicator(renderer=renderer, config=cfg)

    indicator.show(agent="kimi", scope="screen", mode=SessionMode.CONTROL)
    assert renderer.calls[-1]["border"] is False
    assert renderer.calls[-1]["cursor"] is True

    indicator.show(agent="kimi", scope="screen", mode=SessionMode.OBSERVE)
    # disabled mode renders nothing and actively hides the overlay
    assert len(renderer.calls) == 1
    assert renderer.hidden == 1


def test_custom_label_overrides_builtin() -> None:
    cfg = SignalConfig.from_dict({
        "modes": {"control": {"label": "ACHTUNG EINGRIFF"}}
    })
    renderer = SpyRenderer()
    indicator = ScreenSignalIndicator(renderer=renderer, config=cfg)
    indicator.show(agent="kimi", scope="screen", mode=SessionMode.CONTROL)
    assert "ACHTUNG EINGRIFF" in renderer.calls[-1]["label"]


def test_config_round_trip_save_load(tmp_path) -> None:
    cfg = SignalConfig.from_dict({
        "thickness": 10,
        "abort_hotkey": "ctrl+alt+esc",
        "modes": {"handoff": {"color": [1, 2, 3], "cursor": False}},
    })
    path = tmp_path / "signal-config.json"
    cfg.save(path)

    loaded = SignalConfig.load(path)
    assert loaded.thickness == 10
    assert loaded.abort_hotkey == "ctrl+alt+esc"
    assert loaded.for_mode("handoff").color == (1, 2, 3)
    assert loaded.for_mode("handoff").cursor is False
    assert loaded.for_mode("paused").color == MODE_SIGNALS[SessionMode.PAUSED][1]


def test_config_rejects_bad_values(tmp_path) -> None:
    with pytest.raises(ValueError, match="color"):
        SignalConfig.from_dict({"modes": {"control": {"color": [300, 0, 0]}}})
    with pytest.raises(ValueError):
        SignalConfig.from_dict({"modes": {"notamode": {}}})
    with pytest.raises(ValueError, match="hotkey"):
        SignalConfig.from_dict({"abort_hotkey": "ctrl+"})
    with pytest.raises(ValueError, match="thickness"):
        SignalConfig(thickness=1)
    with pytest.raises(ValueError, match="seconds"):
        SignalConfig.from_dict({"pre_action_grace_label": "Bitte warten"})
    with pytest.raises(ValueError, match="color"):
        SignalConfig.from_dict({"pre_action_grace_color": [1, 2]})


def test_cli_signal_config_init_and_show(tmp_path, capsys) -> None:
    from open_compute import cli

    path = tmp_path / "cfg.json"
    cli.cmd_signal(["config", "--init", "--path", str(path)])
    written = json.loads(capsys.readouterr().out)
    assert written["written"] is True
    assert path.exists()

    cli.cmd_signal(["config", "--show", "--path", str(path)])
    shown = json.loads(capsys.readouterr().out)
    assert shown["modes"]["control"]["color"] == list(
        MODE_SIGNALS[SessionMode.CONTROL][1]
    )
    assert shown["modes"]["observe"]["border"] is True


def test_cli_signal_on_uses_config_file(tmp_path, monkeypatch, capsys) -> None:
    from open_compute import cli

    cfg = SignalConfig.from_dict({
        "thickness": 12,
        "modes": {"control": {"border": False, "cursor": True}},
    })
    path = tmp_path / "cfg.json"
    cfg.save(path)

    captured_ctor: dict = {}

    class SpyWin:
        def __init__(self, **kwargs) -> None:
            captured_ctor.update(kwargs)
            self.shown: list[dict] = []

        def show(self, *, color, label, border=True, cursor=True) -> None:
            self.shown.append({"border": border, "cursor": cursor})

        def hide(self) -> None:
            pass

        def is_visible(self) -> bool:
            return True

    monkeypatch.setattr(cli, "WindowsBorderOverlay", SpyWin, raising=False)
    cli.cmd_signal(
        ["on", "--mode", "control", "--for", "0", "--config", str(path)]
    )

    assert captured_ctor["thickness"] == 12
    out = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert out["visible"] is True
    assert out["mode"] == "control"
