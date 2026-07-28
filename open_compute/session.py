"""Fail-closed companion / handoff session state.

The module is deliberately backend-independent.  It stores only control
metadata and bounded audit events: no screenshots, typed text, or raw human
input.  A :class:`ControlLease` is both time- and scope-limited; desktop
mutations must pass this gate *and* the existing :mod:`open_compute.safety`
policy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class SessionMode(str, Enum):
    """Human/agent collaboration modes."""

    OBSERVE = "observe"
    COMPANION = "companion"
    ASSIST = "assist"
    HANDOFF = "handoff"
    CONTROL = "control"
    PAUSED = "paused"


@dataclass(frozen=True)
class ControlLease:
    """A short-lived capability for explicitly named scopes."""

    lease_id: str
    owner: str
    scopes: tuple[str, ...]
    requested_at: datetime
    expires_at: datetime
    granted_at: datetime | None = None
    heartbeat_at: datetime | None = None

    def is_expired(self, now: datetime) -> bool:
        return _as_utc(now) >= self.expires_at

    def covers(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes or any(
            item.endswith("*") and scope.startswith(item[:-1])
            for item in self.scopes
        )


@dataclass(frozen=True)
class SessionEvent:
    """Sanitized transition record."""

    at: datetime
    kind: str
    mode: SessionMode
    details: dict[str, str] = field(default_factory=dict)


@dataclass
class ControlSession:
    """State machine that never grants control implicitly."""

    mode: SessionMode = SessionMode.OBSERVE
    human_owner: str = ""
    lease: ControlLease | None = None
    events: list[SessionEvent] = field(default_factory=list)

    _MAX_EVENTS = 100

    @classmethod
    def companion(
        cls, owner: str, now: datetime | None = None
    ) -> "ControlSession":
        if not owner.strip():
            raise ValueError("owner must not be empty")
        session = cls(mode=SessionMode.COMPANION, human_owner=owner.strip())
        session._event("companion_started", now or _utc_now())
        return session

    def set_mode(self, mode: SessionMode | str, now: datetime | None = None) -> None:
        target = SessionMode(mode)
        if target in (SessionMode.HANDOFF, SessionMode.CONTROL):
            raise ValueError("use request_control/grant_control for control modes")
        self.lease = None
        self.mode = target
        self._event("mode_changed", now or _utc_now(), target=target.value)

    def request_control(
        self,
        owner: str,
        scopes: list[str] | tuple[str, ...],
        ttl_seconds: int = 60,
        now: datetime | None = None,
    ) -> ControlLease:
        at = _as_utc(now or _utc_now())
        clean_owner = owner.strip()
        clean_scopes = tuple(dict.fromkeys(item.strip() for item in scopes if item.strip()))
        if not clean_owner:
            raise ValueError("lease owner must not be empty")
        if not clean_scopes:
            raise ValueError("at least one scope is required")
        if not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be in 1..3600")
        lease = ControlLease(
            lease_id=uuid4().hex,
            owner=clean_owner,
            scopes=clean_scopes,
            requested_at=at,
            expires_at=at + timedelta(seconds=ttl_seconds),
        )
        self.lease = lease
        self.mode = SessionMode.HANDOFF
        self._event(
            "control_requested",
            at,
            lease_id=lease.lease_id,
            owner=clean_owner,
            scopes=",".join(clean_scopes),
        )
        return lease

    def grant_control(
        self, lease_id: str, now: datetime | None = None
    ) -> ControlLease:
        at = _as_utc(now or _utc_now())
        if self.mode is not SessionMode.HANDOFF or self.lease is None:
            raise PermissionError("no pending handoff lease")
        if self.lease.lease_id != lease_id:
            raise PermissionError("lease id does not match pending handoff")
        if self.lease.is_expired(at):
            self.pause("lease expired before grant", at)
            raise PermissionError("lease expired before grant")
        old = self.lease
        self.lease = ControlLease(
            lease_id=old.lease_id,
            owner=old.owner,
            scopes=old.scopes,
            requested_at=old.requested_at,
            expires_at=old.expires_at,
            granted_at=at,
            heartbeat_at=at,
        )
        self.mode = SessionMode.CONTROL
        self._event("control_granted", at, lease_id=old.lease_id)
        return self.lease

    def heartbeat(self, lease_id: str, now: datetime | None = None) -> None:
        at = _as_utc(now or _utc_now())
        allowed, reason = self.authorize("*", at, require_exact_scope=False)
        if not allowed or self.lease is None or self.lease.lease_id != lease_id:
            raise PermissionError(reason if not allowed else "lease id mismatch")
        old = self.lease
        self.lease = ControlLease(
            lease_id=old.lease_id,
            owner=old.owner,
            scopes=old.scopes,
            requested_at=old.requested_at,
            expires_at=old.expires_at,
            granted_at=old.granted_at,
            heartbeat_at=at,
        )
        self._event("heartbeat", at, lease_id=lease_id)

    def authorize(
        self,
        scope: str,
        now: datetime | None = None,
        *,
        require_exact_scope: bool = True,
    ) -> tuple[bool, str]:
        at = _as_utc(now or _utc_now())
        if self.mode is not SessionMode.CONTROL or self.lease is None:
            return False, "no active control lease"
        if self.lease.is_expired(at):
            self.pause("control lease expired", at)
            return False, "control lease expired"
        if require_exact_scope and not self.lease.covers(scope):
            return False, f"control lease does not cover scope {scope!r}"
        return True, "active control lease"

    def human_activity(self, now: datetime | None = None) -> None:
        """Yield immediately without recording the human input itself."""
        at = _as_utc(now or _utc_now())
        self.lease = None
        self.mode = SessionMode.PAUSED
        self._event("human_interrupt", at, reason="human activity detected")

    def pause(self, reason: str, now: datetime | None = None) -> None:
        at = _as_utc(now or _utc_now())
        self.lease = None
        self.mode = SessionMode.PAUSED
        self._event("paused", at, reason=reason[:200])

    def release(self, now: datetime | None = None) -> None:
        at = _as_utc(now or _utc_now())
        self.lease = None
        self.mode = SessionMode.COMPANION
        self._event("control_released", at)

    def _event(self, kind: str, at: datetime, **details: str) -> None:
        self.events.append(
            SessionEvent(
                at=_as_utc(at),
                kind=kind,
                mode=self.mode,
                details={key: str(value) for key, value in details.items()},
            )
        )
        del self.events[:-self._MAX_EVENTS]

    def to_dict(self) -> dict[str, Any]:
        def stamp(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        lease = None
        if self.lease is not None:
            lease = asdict(self.lease)
            for key in ("requested_at", "expires_at", "granted_at", "heartbeat_at"):
                lease[key] = stamp(lease[key])
            lease["scopes"] = list(self.lease.scopes)
        return {
            "schema": 1,
            "mode": self.mode.value,
            "human_owner": self.human_owner,
            "lease": lease,
            "events": [
                {
                    "at": event.at.isoformat(),
                    "kind": event.kind,
                    "mode": event.mode.value,
                    "details": event.details,
                }
                for event in self.events
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControlSession":
        if data.get("schema") != 1:
            raise ValueError("unsupported session schema")
        raw_lease = data.get("lease")
        lease = None
        if raw_lease is not None:
            lease = ControlLease(
                lease_id=str(raw_lease["lease_id"]),
                owner=str(raw_lease["owner"]),
                scopes=tuple(str(item) for item in raw_lease["scopes"]),
                requested_at=datetime.fromisoformat(raw_lease["requested_at"]),
                expires_at=datetime.fromisoformat(raw_lease["expires_at"]),
                granted_at=(
                    datetime.fromisoformat(raw_lease["granted_at"])
                    if raw_lease.get("granted_at")
                    else None
                ),
                heartbeat_at=(
                    datetime.fromisoformat(raw_lease["heartbeat_at"])
                    if raw_lease.get("heartbeat_at")
                    else None
                ),
            )
        events = [
            SessionEvent(
                at=datetime.fromisoformat(item["at"]),
                kind=str(item["kind"]),
                mode=SessionMode(item["mode"]),
                details={
                    str(key): str(value)
                    for key, value in dict(item.get("details", {})).items()
                },
            )
            for item in data.get("events", [])
        ]
        return cls(
            mode=SessionMode(data["mode"]),
            human_owner=str(data.get("human_owner", "")),
            lease=lease,
            events=events[-cls._MAX_EVENTS :],
        )


@dataclass(frozen=True)
class SessionStore:
    """Atomic JSON persistence for the small sanitized state document."""

    path: Path

    def load(self) -> ControlSession:
        if not self.path.exists():
            return ControlSession()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("session state must be a JSON object")
        return ControlSession.from_dict(data)

    def save(self, session: ControlSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(session.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

