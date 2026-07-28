"""Headless perceive/stabilize/act/verify coordination primitives.

This module contains no capture, rendering, input, focus, window, microphone,
or speech implementation.  Every side effect is behind an injected protocol,
which keeps the workflow useful for pure/mock tests and lets a future live
adapter remain separately gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Callable, Protocol

from .human_activity import ActivityAssessment, InputProvenance
from .session import ControlSession, SessionMode


_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:*/-]{0,199}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class InstructionOrigin(str, Enum):
    USER = "user"
    SYSTEM = "system"
    SCREEN = "screen"
    UNKNOWN = "unknown"


class WorkflowOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    PAUSED = "paused"
    UNCERTAIN = "uncertain"
    ALREADY_COMPLETED = "already_completed"


@dataclass(frozen=True)
class ObservationDigest:
    digest: str
    stable: bool
    untrusted_instruction_detected: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.digest) != 64:
            raise ValueError("observation digest must be a SHA-256 hex digest")
        int(self.digest, 16)
        prohibited = {"text", "prompt", "content", "screenshot", "image", "audio"}
        if prohibited.intersection(key.casefold() for key in self.metadata):
            raise ValueError("observation metadata must not contain raw content")


@dataclass(frozen=True)
class WorkflowPlan:
    workflow_id: str
    action_id: str
    scope: str
    expected_change: str
    instruction_origin: InstructionOrigin = InstructionOrigin.USER
    max_attempts: int = 1

    def __post_init__(self) -> None:
        for name in ("workflow_id", "action_id", "scope"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} must not be empty")
            if not _STABLE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a stable opaque identifier")
        if not self.expected_change.strip():
            raise ValueError("expected_change must not be empty")
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be in 1..3")


@dataclass(frozen=True)
class ActionReceipt:
    """Result of an injected action adapter.

    ``applied=None`` means the adapter cannot prove whether state changed.  The
    orchestrator then fails closed and never retries the action.
    """

    applied: bool | None
    safe_to_retry: bool = False
    reason: str = ""


@dataclass(frozen=True)
class VerificationResult:
    matched: bool
    reason: str


@dataclass(frozen=True)
class WorkflowResult:
    workflow_id: str
    action_id: str
    outcome: WorkflowOutcome
    attempts: int
    reason: str
    before_digest: str | None = None
    after_digest: str | None = None


class PerceivePort(Protocol):
    def perceive(self, scope: str) -> ObservationDigest: ...


class StabilizePort(Protocol):
    def stabilize(
        self, scope: str, observation: ObservationDigest
    ) -> ObservationDigest: ...


class ActPort(Protocol):
    def act(self, plan: WorkflowPlan) -> ActionReceipt: ...


class VerifyPort(Protocol):
    def verify(
        self,
        before: ObservationDigest,
        after: ObservationDigest,
        expected_change: str,
    ) -> VerificationResult: ...


class OwnershipIndicator(Protocol):
    """Interface only.  This release intentionally ships no renderer."""

    def show(self, *, agent: str, scope: str, mode: SessionMode) -> None: ...

    def clear(self) -> None: ...


class InputCleanup(Protocol):
    def release_all(self) -> Any: ...


class EmergencyStop(Protocol):
    def is_triggered(self) -> bool: ...


@dataclass
class NullOwnershipIndicator:
    """Non-rendering default used by headless workflows."""

    def show(self, *, agent: str, scope: str, mode: SessionMode) -> None:
        return None

    def clear(self) -> None:
        return None


@dataclass
class LatchedEmergencyStop:
    """Pure in-memory Not-Aus interface; no hotkey or OS hook."""

    triggered: bool = False

    def trigger(self) -> None:
        self.triggered = True

    def reset(self) -> None:
        self.triggered = False

    def is_triggered(self) -> bool:
        return self.triggered


_PROHIBITED_AUDIT_KEYS = frozenset(
    {
        "audio",
        "content",
        "credential",
        "image",
        "key",
        "keystroke",
        "password",
        "prompt",
        "screenshot",
        "text",
        "transcript",
    }
)
_ALLOWED_AUDIT_KEYS = frozenset(
    {
        "action_id",
        "adapter_errors",
        "after_digest",
        "applied",
        "attempt",
        "before_digest",
        "matched",
        "origin",
        "outcome",
        "reason",
        "reason_code",
        "safe_to_retry",
        "scope",
    }
)
_SAFE_AUDIT_CODE = re.compile(r"^[a-z0-9_.:-]{1,80}$")


def _audit_code(value: object) -> str:
    """Keep stable codes readable and hash every other free-form value."""

    text = str(value).strip().casefold()
    if _SAFE_AUDIT_CODE.fullmatch(text):
        return text
    return f"sha256:{sha256(str(value).encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    at: datetime
    kind: str
    workflow_id: str
    details: dict[str, str]
    previous_hash: str
    event_hash: str


@dataclass
class HashChainAudit:
    """Append-only, content-sanitized in-memory audit chain."""

    events: list[AuditEvent] = field(default_factory=list)
    now: Callable[[], datetime] = _utc_now

    def append(
        self, kind: str, workflow_id: str, **details: object
    ) -> AuditEvent:
        if not kind.strip() or not workflow_id.strip():
            raise ValueError("kind and workflow_id must not be empty")
        clean: dict[str, str] = {}
        for key, value in details.items():
            normalized = key.casefold()
            if any(token in normalized for token in _PROHIBITED_AUDIT_KEYS):
                raise ValueError(f"raw/private audit field prohibited: {key}")
            if normalized not in _ALLOWED_AUDIT_KEYS:
                raise ValueError(f"unregistered audit field: {key}")
            clean[str(key)] = (
                _audit_code(value)
                if normalized in {"reason", "reason_code"}
                else str(value)[:300]
            )
        sequence = len(self.events) + 1
        at = _as_utc(self.now())
        previous = self.events[-1].event_hash if self.events else "0" * 64
        payload = {
            "sequence": sequence,
            "at": at.isoformat(),
            "kind": kind,
            "workflow_id": workflow_id,
            "details": clean,
            "previous_hash": previous,
        }
        digest = sha256(
            json.dumps(
                payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        event = AuditEvent(
            sequence=sequence,
            at=at,
            kind=kind,
            workflow_id=workflow_id,
            details=clean,
            previous_hash=previous,
            event_hash=digest,
        )
        self.events.append(event)
        return event

    def verify(self) -> bool:
        previous = "0" * 64
        for expected_sequence, event in enumerate(self.events, start=1):
            payload = {
                "sequence": event.sequence,
                "at": event.at.isoformat(),
                "kind": event.kind,
                "workflow_id": event.workflow_id,
                "details": event.details,
                "previous_hash": event.previous_hash,
            }
            digest = sha256(
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous
                or event.event_hash != digest
            ):
                return False
            previous = event.event_hash
        return True


@dataclass
class WorkflowState:
    """Bounded idempotency state.

    Uncertain actions are remembered like completed actions because replaying
    an action that may already have succeeded is unsafe.
    """

    completed_action_ids: list[str] = field(default_factory=list)
    uncertain_action_ids: list[str] = field(default_factory=list)
    max_entries: int = 1_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_entries <= 100_000:
            raise ValueError("max_entries must be in 1..100000")
        self._trim()

    def seen(self, action_id: str) -> bool:
        return (
            action_id in self.completed_action_ids
            or action_id in self.uncertain_action_ids
        )

    def mark_completed(self, action_id: str) -> None:
        if action_id not in self.completed_action_ids:
            self.completed_action_ids.append(action_id)
        self._trim()

    def mark_uncertain(self, action_id: str) -> None:
        if action_id not in self.uncertain_action_ids:
            self.uncertain_action_ids.append(action_id)
        self._trim()

    def _trim(self) -> None:
        del self.completed_action_ids[:-self.max_entries]
        del self.uncertain_action_ids[:-self.max_entries]


class ScreenPromptInjectionGuard:
    """Fail closed when control is sourced from or redirected by screen text."""

    def authorize(
        self, plan: WorkflowPlan, observation: ObservationDigest | None = None
    ) -> tuple[bool, str]:
        if plan.instruction_origin in {
            InstructionOrigin.SCREEN,
            InstructionOrigin.UNKNOWN,
        }:
            return False, "screen/unknown instructions cannot authorize control"
        if observation is not None and observation.untrusted_instruction_detected:
            return False, "untrusted on-screen instruction signal detected"
        return True, "trusted instruction origin"


@dataclass
class CleanupCoordinator:
    session: ControlSession
    indicator: OwnershipIndicator
    input_cleanup: InputCleanup
    audit: HashChainAudit

    def cleanup(self, workflow_id: str, reason: str) -> None:
        errors: list[str] = []
        try:
            self.input_cleanup.release_all()
        except BaseException as exc:  # cleanup must continue through every adapter
            errors.append(type(exc).__name__)
        try:
            self.indicator.clear()
        except BaseException as exc:
            errors.append(type(exc).__name__)
        if self.session.mode in {SessionMode.CONTROL, SessionMode.HANDOFF}:
            self.session.pause(reason)
        self.audit.append(
            "cleanup",
            workflow_id,
            reason=reason,
            adapter_errors=",".join(errors),
        )


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    created_at: datetime
    expires_at: datetime
    digest: str

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise ValueError("artifact_id must not be empty")
        if _as_utc(self.expires_at) <= _as_utc(self.created_at):
            raise ValueError("expires_at must be after created_at")
        if len(self.digest) != 64:
            raise ValueError("artifact digest must be SHA-256")
        int(self.digest, 16)


@dataclass
class RetentionLedger:
    """Content-free retention metadata with explicit deletion callbacks."""

    max_records: int = 100
    records: list[ArtifactRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 1 <= self.max_records <= 10_000:
            raise ValueError("max_records must be in 1..10000")
        self._trim()

    def register(
        self,
        artifact_id: str,
        payload: bytes,
        *,
        created_at: datetime,
        ttl_seconds: int,
        delete: Callable[[str], None],
    ) -> ArtifactRecord:
        if not 1 <= ttl_seconds <= 86_400:
            raise ValueError("ttl_seconds must be in 1..86400")
        created = _as_utc(created_at)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            created_at=created,
            expires_at=created + timedelta(seconds=ttl_seconds),
            digest=sha256(payload).hexdigest(),
        )
        while len(self.records) >= self.max_records:
            victim = self.records[0]
            # Delete first.  If deletion fails, keep the record so retention
            # state never claims an artifact vanished when it may still exist.
            delete(victim.artifact_id)
            self.records.pop(0)
        self.records.append(record)
        return record

    def purge(
        self, *, now: datetime, delete: Callable[[str], None]
    ) -> tuple[str, ...]:
        at = _as_utc(now)
        expired = [
            record for record in self.records if _as_utc(record.expires_at) <= at
        ]
        for record in expired:
            delete(record.artifact_id)
        expired_ids = {record.artifact_id for record in expired}
        self.records = [
            record for record in self.records if record.artifact_id not in expired_ids
        ]
        return tuple(record.artifact_id for record in expired)

    def _trim(self) -> None:
        if len(self.records) > self.max_records:
            raise ValueError(
                "preloaded records exceed max_records; purge them explicitly"
            )


@dataclass
class CooperativeOrchestrator:
    session: ControlSession
    agent: str
    perceive: PerceivePort
    stabilize: StabilizePort
    actor: ActPort
    verifier: VerifyPort
    activity: Callable[[], ActivityAssessment | None]
    indicator: OwnershipIndicator
    input_cleanup: InputCleanup
    emergency_stop: EmergencyStop
    audit: HashChainAudit = field(default_factory=HashChainAudit)
    state: WorkflowState = field(default_factory=WorkflowState)
    injection_guard: ScreenPromptInjectionGuard = field(
        default_factory=ScreenPromptInjectionGuard
    )

    def run(self, plan: WorkflowPlan) -> WorkflowResult:
        self.audit.append(
            "workflow_started",
            plan.workflow_id,
            action_id=plan.action_id,
            scope=plan.scope,
            origin=plan.instruction_origin.value,
        )
        cleanup = CleanupCoordinator(
            session=self.session,
            indicator=self.indicator,
            input_cleanup=self.input_cleanup,
            audit=self.audit,
        )
        if self.state.seen(plan.action_id):
            return self._result(
                plan,
                WorkflowOutcome.ALREADY_COMPLETED,
                0,
                "action_id was already completed or uncertain",
            )
        allowed, reason = self.injection_guard.authorize(plan)
        if not allowed:
            cleanup.cleanup(plan.workflow_id, reason)
            return self._result(plan, WorkflowOutcome.BLOCKED, 0, reason)
        if self.emergency_stop.is_triggered():
            return self._pause(
                plan, "emergency stop is latched", 0, cleanup
            )
        activity = self.activity()
        if (
            activity is not None
            and activity.recent
            and activity.provenance is InputProvenance.HUMAN
        ):
            return self._pause(
                plan, "recent human activity detected", 0, cleanup
            )
        authorized, lease_reason = self.session.authorize(plan.scope)
        if not authorized:
            self.audit.append(
                "blocked", plan.workflow_id, reason=lease_reason
            )
            return self._result(
                plan, WorkflowOutcome.BLOCKED, 0, lease_reason
            )

        before: ObservationDigest | None = None
        attempts = 0
        try:
            self.indicator.show(
                agent=self.agent, scope=plan.scope, mode=self.session.mode
            )
            before = self.perceive.perceive(plan.scope)
            before = self.stabilize.stabilize(plan.scope, before)
            allowed, reason = self.injection_guard.authorize(plan, before)
            if not allowed:
                cleanup.cleanup(plan.workflow_id, reason)
                return self._result(
                    plan,
                    WorkflowOutcome.BLOCKED,
                    attempts,
                    reason,
                    before=before,
                )

            for attempts in range(1, plan.max_attempts + 1):
                if self.emergency_stop.is_triggered():
                    cleanup.cleanup(plan.workflow_id, "emergency stop")
                    return self._result(
                        plan,
                        WorkflowOutcome.PAUSED,
                        attempts - 1,
                        "emergency stop is latched",
                        before=before,
                    )
                activity = self.activity()
                if (
                    activity is not None
                    and activity.recent
                    and activity.provenance is InputProvenance.HUMAN
                ):
                    cleanup.cleanup(plan.workflow_id, "human activity")
                    return self._result(
                        plan,
                        WorkflowOutcome.PAUSED,
                        attempts - 1,
                        "recent human activity detected",
                        before=before,
                    )

                receipt = self.actor.act(plan)
                self.audit.append(
                    "action_attempt",
                    plan.workflow_id,
                    action_id=plan.action_id,
                    attempt=attempts,
                    applied=receipt.applied,
                    safe_to_retry=receipt.safe_to_retry,
                    reason=receipt.reason,
                )
                if receipt.applied is None:
                    self.state.mark_uncertain(plan.action_id)
                    cleanup.cleanup(plan.workflow_id, "uncertain action result")
                    return self._result(
                        plan,
                        WorkflowOutcome.UNCERTAIN,
                        attempts,
                        "action may have succeeded; retry prohibited",
                        before=before,
                    )
                if receipt.applied is False:
                    if receipt.safe_to_retry and attempts < plan.max_attempts:
                        continue
                    cleanup.cleanup(plan.workflow_id, "action not applied")
                    return self._result(
                        plan,
                        WorkflowOutcome.FAILED,
                        attempts,
                        receipt.reason or "action not applied",
                        before=before,
                    )

                self.state.mark_completed(plan.action_id)
                after = self.perceive.perceive(plan.scope)
                after = self.stabilize.stabilize(plan.scope, after)
                verification = self.verifier.verify(
                    before, after, plan.expected_change
                )
                self.audit.append(
                    "verification",
                    plan.workflow_id,
                    matched=verification.matched,
                    reason=verification.reason,
                    before_digest=before.digest,
                    after_digest=after.digest,
                )
                if verification.matched:
                    self.indicator.clear()
                    return self._result(
                        plan,
                        WorkflowOutcome.SUCCEEDED,
                        attempts,
                        verification.reason,
                        before=before,
                        after=after,
                    )
                cleanup.cleanup(plan.workflow_id, "verification failed")
                return self._result(
                    plan,
                    WorkflowOutcome.FAILED,
                    attempts,
                    "applied action failed verification; retry prohibited",
                    before=before,
                    after=after,
                )
        except BaseException:
            cleanup.cleanup(plan.workflow_id, "workflow exception")
            raise

        raise AssertionError("unreachable workflow state")

    def _pause(
        self,
        plan: WorkflowPlan,
        reason: str,
        attempts: int,
        cleanup: CleanupCoordinator,
    ) -> WorkflowResult:
        self.session.human_activity() if "human" in reason else self.session.pause(reason)
        cleanup.cleanup(plan.workflow_id, reason)
        self.audit.append("paused", plan.workflow_id, reason=reason)
        return self._result(
            plan, WorkflowOutcome.PAUSED, attempts, reason
        )

    def _result(
        self,
        plan: WorkflowPlan,
        outcome: WorkflowOutcome,
        attempts: int,
        reason: str,
        *,
        before: ObservationDigest | None = None,
        after: ObservationDigest | None = None,
    ) -> WorkflowResult:
        return WorkflowResult(
            workflow_id=plan.workflow_id,
            action_id=plan.action_id,
            outcome=outcome,
            attempts=attempts,
            reason=reason,
            before_digest=before.digest if before else None,
            after_digest=after.digest if after else None,
        )
