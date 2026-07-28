from __future__ import annotations

import json

import pytest

from open_compute.window_control import (
    WindowAmbiguousError,
    WindowController,
    WindowNotFoundError,
    resolve_window,
)


WINDOWS = [
    {
        "title": "Editor - alpha.txt",
        "hwnd": 41,
        "pid": 100,
        "rect": {"left": 0, "top": 0, "width": 800, "height": 600},
    },
    {
        "title": "Editor - beta.txt",
        "hwnd": 42,
        "pid": 101,
        "rect": {"left": 100, "top": 100, "width": 900, "height": 700},
    },
]


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_windows(self):
        return WINDOWS

    def show(self, hwnd: int, operation: str) -> None:
        self.calls.append(("show", hwnd, operation))

    def move(self, hwnd: int, left: int, top: int, width: int, height: int) -> None:
        self.calls.append(("move", hwnd, left, top, width, height))


def test_title_substring_must_resolve_uniquely() -> None:
    with pytest.raises(WindowAmbiguousError) as exc:
        resolve_window(WINDOWS, title="Editor")

    assert [item["hwnd"] for item in exc.value.candidates] == [41, 42]


def test_exact_hwnd_or_pid_resolves_without_guessing() -> None:
    assert resolve_window(WINDOWS, hwnd=42)["title"].endswith("beta.txt")
    assert resolve_window(WINDOWS, pid=100)["hwnd"] == 41


def test_missing_window_is_explicit() -> None:
    with pytest.raises(WindowNotFoundError, match="No window"):
        resolve_window(WINDOWS, title="browser")


def test_mutation_requires_active_scoped_session() -> None:
    adapter = FakeAdapter()
    controller = WindowController(adapter)

    with pytest.raises(PermissionError, match="control"):
        controller.apply(
            operation="minimize",
            hwnd=42,
            authorize=lambda scope: (False, "no active control lease"),
        )

    assert adapter.calls == []


def test_move_is_executed_only_after_scope_authorization() -> None:
    adapter = FakeAdapter()
    controller = WindowController(adapter)

    result = controller.apply(
        operation="move",
        hwnd=42,
        rect=(10, 20, 640, 480),
        authorize=lambda scope: (scope == "window:42", "ok"),
    )

    assert result["hwnd"] == 42
    assert adapter.calls == [("move", 42, 10, 20, 640, 480)]


def test_window_cli_requires_lease_and_confirmation(tmp_path, monkeypatch, capsys) -> None:
    from open_compute import cli
    from open_compute.session import ControlSession, SessionStore

    adapter = FakeAdapter()
    monkeypatch.setattr(cli, "_session_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "Win32WindowAdapter", lambda: adapter, raising=False)

    session = ControlSession.companion(owner="local-user")
    lease = session.request_control("agent-a", ["window:42"], ttl_seconds=60)
    session.grant_control(lease.lease_id)
    SessionStore(tmp_path / "control-session.json").save(session)

    cli.cmd_window(["minimize", "--hwnd", "42", "--yes"])
    result = json.loads(capsys.readouterr().out)

    assert result["operation"] == "minimize"
    assert adapter.calls == [("show", 42, "minimize")]
