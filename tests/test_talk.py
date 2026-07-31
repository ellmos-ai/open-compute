from __future__ import annotations

import json

import pytest

from open_compute.indicator import parse_hotkey
from open_compute.talk import TalkResult, record_push_to_talk


def test_parse_hotkey_modifier_combo() -> None:
    mods, vk = parse_hotkey("ctrl+alt+esc")
    assert mods == 0x0002 | 0x0001
    assert vk == 0x1B


def test_parse_hotkey_single_function_key() -> None:
    mods, vk = parse_hotkey("F9")
    assert mods == 0
    assert vk == 0x78


def test_parse_hotkey_letter_and_case_insensitive() -> None:
    assert parse_hotkey("shift+a") == (0x0004, ord("A"))


def test_parse_hotkey_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_hotkey("")
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+alt")  # no real key
    with pytest.raises(ValueError, match="unknown hotkey key"):
        parse_hotkey("ctrl+definitelynotakey")


class ScriptedKeys:
    """key_down probe driven by a script of booleans."""

    def __init__(self, script: list[bool]) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self, vk: int) -> bool:
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return False


class RecordingMci:
    def __init__(self, fail_on: str | None = None) -> None:
        self.commands: list[str] = []
        self.fail_on = fail_on

    def __call__(self, command: str) -> int:
        self.commands.append(command)
        if self.fail_on and self.fail_on in command:
            return 7
        return 0


def test_ptt_records_while_key_held_and_saves() -> None:
    keys = ScriptedKeys([False, True, True, True, False])
    mci = RecordingMci()

    result = record_push_to_talk(
        vk=0x78,
        out_path="out.wav",
        mci=mci,
        key_down=keys,
        sleep=lambda _s: None,
        poll_seconds=0.01,
    )

    assert result.recorded is True
    assert result.path == "out.wav"
    assert result.reason == "released"
    assert mci.commands == [
        "open new type waveaudio alias ocptt",
        "record ocptt",
        "stop ocptt",
        'save ocptt "out.wav"',
        "close ocptt",
    ]


def test_ptt_wait_timeout_records_nothing() -> None:
    keys = ScriptedKeys([False, False, False])
    mci = RecordingMci()

    result = record_push_to_talk(
        vk=0x78,
        out_path="out.wav",
        mci=mci,
        key_down=keys,
        sleep=lambda _s: None,
        poll_seconds=0.01,
        wait_timeout=0.02,
    )

    assert result == TalkResult(False, None, 0.0, "wait_timeout")
    assert mci.commands == []


def test_ptt_max_seconds_caps_recording() -> None:
    keys = ScriptedKeys([True] * 100)  # key never released
    mci = RecordingMci()

    result = record_push_to_talk(
        vk=0x78,
        out_path="out.wav",
        mci=mci,
        key_down=keys,
        sleep=lambda _s: None,
        poll_seconds=0.01,
        max_seconds=0.03,
    )

    assert result.recorded is True
    assert result.reason == "max_seconds"


def test_ptt_closes_device_even_on_mci_failure() -> None:
    keys = ScriptedKeys([True, False])
    mci = RecordingMci(fail_on="record")

    with pytest.raises(RuntimeError, match="record ocptt"):
        record_push_to_talk(
            vk=0x78,
            out_path="out.wav",
            mci=mci,
            key_down=keys,
            sleep=lambda _s: None,
            poll_seconds=0.01,
        )

    assert mci.commands[-1] == "close ocptt"


def test_ptt_rejects_bad_bounds() -> None:
    with pytest.raises(ValueError, match="max_seconds"):
        record_push_to_talk(
            vk=1, out_path="x", mci=lambda _c: 0,
            key_down=lambda _v: False, max_seconds=0,
        )


def test_cli_talk_uses_injected_impl(monkeypatch, capsys, tmp_path) -> None:
    from open_compute import cli

    seen: dict = {}

    def fake_rpt(*, vk, out_path, mci, key_down, max_seconds, wait_timeout):
        seen.update(vk=vk, out_path=out_path, max_seconds=max_seconds)
        return TalkResult(True, out_path, 1.5, "released")

    monkeypatch.setattr(cli, "record_push_to_talk", fake_rpt, raising=False)
    monkeypatch.setattr(cli, "winmm_mci", lambda: (lambda _c: 0), raising=False)
    monkeypatch.setattr(
        cli, "async_key_down", lambda: (lambda _v: False), raising=False
    )

    out = tmp_path / "clip.wav"
    cli.cmd_talk(["--key", "F9", "--out", str(out), "--max-seconds", "5"])

    lines = capsys.readouterr().out.strip().splitlines()
    assert json.loads(lines[0]) == {"listening": True, "key": "F9"}
    result = json.loads(lines[1])
    assert result["recorded"] is True
    assert result["wav"] == str(out)
    assert result["reason"] == "released"
    assert seen["vk"] == 0x78
    assert seen["max_seconds"] == 5.0


def test_cli_talk_rejects_modifier_key(capsys) -> None:
    from open_compute import cli

    with pytest.raises(SystemExit):
        cli.cmd_talk(["--key", "ctrl+a"])
