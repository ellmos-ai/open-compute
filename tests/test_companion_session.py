from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from open_compute.session import (
    ControlSession,
    SessionMode,
    SessionStore,
)


NOW = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)


def test_companion_is_observational_and_cannot_mutate() -> None:
    session = ControlSession.companion(owner="local-user", now=NOW)

    assert session.mode is SessionMode.COMPANION
    allowed, reason = session.authorize("window:42", now=NOW)
    assert allowed is False
    assert "control" in reason.lower()


def test_handoff_requires_matching_grant_and_scope() -> None:
    session = ControlSession.companion(owner="local-user", now=NOW)
    lease = session.request_control(
        owner="agent-a",
        scopes=["window:42"],
        ttl_seconds=30,
        now=NOW,
    )

    assert session.mode is SessionMode.HANDOFF
    with pytest.raises(PermissionError, match="lease"):
        session.grant_control("wrong-id", now=NOW)

    session.grant_control(lease.lease_id, now=NOW)
    assert session.mode is SessionMode.CONTROL
    assert session.authorize("window:42", now=NOW)[0] is True
    assert session.authorize("window:99", now=NOW)[0] is False


def test_expired_lease_fails_closed_and_pauses() -> None:
    session = ControlSession.companion(owner="local-user", now=NOW)
    lease = session.request_control(
        owner="agent-a",
        scopes=["window:42"],
        ttl_seconds=5,
        now=NOW,
    )
    session.grant_control(lease.lease_id, now=NOW)

    allowed, reason = session.authorize(
        "window:42", now=NOW + timedelta(seconds=6)
    )

    assert allowed is False
    assert "expired" in reason.lower()
    assert session.mode is SessionMode.PAUSED


def test_human_activity_interrupts_control_without_recording_input() -> None:
    session = ControlSession.companion(owner="local-user", now=NOW)
    lease = session.request_control(
        owner="agent-a",
        scopes=["window:*"],
        ttl_seconds=30,
        now=NOW,
    )
    session.grant_control(lease.lease_id, now=NOW)

    session.human_activity(now=NOW + timedelta(seconds=1))

    assert session.mode is SessionMode.PAUSED
    assert session.lease is None
    assert session.events[-1].kind == "human_interrupt"
    assert set(session.events[-1].details) == {"reason"}


def test_store_round_trip_is_atomic_and_contains_no_capture_data(tmp_path) -> None:
    path = tmp_path / "control-session.json"
    store = SessionStore(path)
    session = ControlSession.companion(owner="local-user", now=NOW)
    lease = session.request_control(
        owner="agent-a",
        scopes=["window:42"],
        ttl_seconds=30,
        now=NOW,
    )
    session.grant_control(lease.lease_id, now=NOW)

    store.save(session)
    loaded = store.load()

    assert loaded.to_dict() == session.to_dict()
    text = path.read_text(encoding="utf-8")
    assert "screenshot" not in text.lower()
    assert "keystroke" not in text.lower()


def test_session_cli_request_then_grant(tmp_path, monkeypatch, capsys) -> None:
    from open_compute import cli

    monkeypatch.setattr(cli, "_session_dir", lambda: tmp_path)
    cli.cmd_session(["companion", "--owner", "local-user"])
    cli.cmd_session(
        [
            "request-control",
            "--owner",
            "agent-a",
            "--scope",
            "window:42",
            "--ttl",
            "60",
        ]
    )
    requested = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    cli.cmd_session(["grant", "--lease-id", requested["lease_id"]])
    granted = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert granted["mode"] == "control"
    assert granted["lease"]["scopes"] == ["window:42"]
