from __future__ import annotations

import json

import pytest

from open_compute.adaptive_capture import capture_until_stable


def test_window_scoped_series_stops_after_stable_duplicates() -> None:
    frames = iter([b"first", b"second", b"second", b"second"])

    result = capture_until_stable(
        lambda: next(frames),
        scope="window:42",
        max_frames=10,
        stable_frames=2,
        interval_seconds=0,
    )

    assert result.reason == "stable"
    assert result.captured_count == 4
    assert result.unique_count == 2
    assert result.frames == (b"first", b"second")


def test_series_is_hard_bounded_and_ring_buffered() -> None:
    counter = iter(range(20))

    result = capture_until_stable(
        lambda: str(next(counter)).encode(),
        scope="window:42",
        max_frames=6,
        stable_frames=3,
        max_unique=3,
        interval_seconds=0,
    )

    assert result.reason == "max_frames"
    assert result.captured_count == 6
    assert result.unique_count == 6
    assert result.frames == (b"3", b"4", b"5")


def test_fullscreen_requires_explicit_opt_in() -> None:
    with pytest.raises(PermissionError, match="full-screen"):
        capture_until_stable(
            lambda: b"frame",
            scope="fullscreen",
            max_frames=2,
            stable_frames=1,
            interval_seconds=0,
        )


def test_invalid_capture_bounds_are_rejected() -> None:
    with pytest.raises(ValueError, match="max_frames"):
        capture_until_stable(
            lambda: b"frame",
            scope="window:42",
            max_frames=0,
            interval_seconds=0,
        )


def test_capture_series_cli_saves_only_unique_frames(tmp_path, monkeypatch, capsys) -> None:
    from open_compute import cli

    frames = iter([b"a", b"b", b"b"])
    monkeypatch.setattr(cli, "_capture_window_bytes", lambda _window: next(frames))
    cli.cmd_capture_series(
        [
            "--window",
            "Editor",
            "--max-frames",
            "3",
            "--stable-frames",
            "1",
            "--interval",
            "0",
            "--out-dir",
            str(tmp_path),
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert result["captured_count"] == 3
    assert result["unique_count"] == 2
    assert len(result["paths"]) == 2
