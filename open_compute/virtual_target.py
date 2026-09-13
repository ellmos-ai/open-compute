"""Headless semantic target activation for a virtual pointer source.

Dispatch is deliberately limited to an explicitly bound browser context or a
Windows UI Automation control pattern.  The module has no coordinate-click or
OS-input fallback.  All environment access is expressed through injected
ports so tests can prove ordering, one-shot claims, binding and receipts
without opening a browser or touching a desktop.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from .drivers.base import SemanticBrowserDriver
from .interaction import ObservationRegistry
from .virtual_pointer import (
    PointerAuditStamp,
    PointerCoordinateFrame,
    PointerDispatchKind,
    PointerFrameKind,
    PointerOrigin,
    PointerPhase,
    PointerProvenance,
    PointerSourceState,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMANTIC_KINDS = frozenset(
    {
        PointerDispatchKind.BROWSER_SEMANTIC,
        PointerDispatchKind.ACCESSIBILITY_SEMANTIC,
    }
)
_UNTRUSTED_ORIGINS = frozenset({PointerOrigin.SCREEN, PointerOrigin.UNKNOWN})


def _identifier(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be a stable opaque identifier")
    return normalized


def _hash(name: str, value: str) -> str:
    normalized = str(value).strip().lower()
    if not _HASH_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return normalized


def pointer_frame_record(frame: PointerCoordinateFrame) -> dict[str, object]:
    """Canonical mapping for :class:`ObservationRegistry` frame claims."""

    result = asdict(frame)
    result["kind"] = frame.kind.value
    return result


@dataclass(frozen=True)
class SemanticWindowBinding:
    """Exact expected window, process, context and coordinate-frame identity."""

    window_token: str
    hwnd: int
    pid: int
    process_token: str
    title_sha256: str
    context_id: str
    frame: PointerCoordinateFrame

    def __post_init__(self) -> None:
        for name in ("window_token", "process_token", "context_id"):
            object.__setattr__(self, name, _identifier(name, getattr(self, name)))
        object.__setattr__(self, "title_sha256", _hash("title_sha256", self.title_sha256))
        for name in ("hwnd", "pid"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.frame.context_id is not None and self.frame.context_id != self.context_id:
            raise ValueError("frame and binding context_id must match")


@dataclass(frozen=True)
class SemanticFocusSnapshot:
    binding: SemanticWindowBinding
    focused: bool

    def __post_init__(self) -> None:
        if not isinstance(self.focused, bool):
            raise ValueError("focused must be boolean")


@dataclass(frozen=True)
class SemanticTargetIdentity:
    target_id: str
    stable_id: str
    role: str
    context_id: str
    dispatch_kind: PointerDispatchKind

    def __post_init__(self) -> None:
        for name in ("target_id", "stable_id", "role", "context_id"):
            object.__setattr__(self, name, _identifier(name, getattr(self, name)))
        object.__setattr__(self, "dispatch_kind", PointerDispatchKind(self.dispatch_kind))
        if self.dispatch_kind not in _SEMANTIC_KINDS:
            raise ValueError("target identity requires a semantic dispatch kind")


@dataclass(frozen=True)
class SemanticTargetState:
    signature: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "signature", _hash("signature", self.signature))


@dataclass(frozen=True)
class SemanticObservation:
    observation_id: str
    window_token: str
    context_id: str
    frame: PointerCoordinateFrame
    target_id: str
    target_state_signature: str

    def __post_init__(self) -> None:
        for name in (
            "observation_id",
            "window_token",
            "context_id",
            "target_id",
        ):
            object.__setattr__(self, name, _identifier(name, getattr(self, name)))
        object.__setattr__(
            self,
            "target_state_signature",
            _hash("target_state_signature", self.target_state_signature),
        )


@dataclass(frozen=True)
class SemanticActivationRequest:
    sequence: int
    provenance: PointerProvenance
    binding: SemanticWindowBinding
    target_query: str = field(repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or int(self.sequence) != self.sequence:
            raise ValueError("semantic sequence must be a positive integer")
        if self.sequence <= 0:
            raise ValueError("semantic sequence must be a positive integer")
        object.__setattr__(self, "sequence", int(self.sequence))
        if self.provenance.dispatch_kind not in _SEMANTIC_KINDS:
            raise ValueError("semantic activation requires a semantic dispatch kind")
        for name in ("observation_id", "window_token", "target_id"):
            if getattr(self.provenance, name) is None:
                raise ValueError(f"semantic activation requires {name}")
        if self.provenance.window_token != self.binding.window_token:
            raise ValueError("provenance and binding window_token must match")
        if not isinstance(self.target_query, str) or not self.target_query.strip():
            raise ValueError("target_query is required")


class SemanticResolutionStatus(str, Enum):
    EXACT = "exact"
    NOT_APPLICABLE = "not_applicable"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SemanticResolvedTarget:
    identity: SemanticTargetIdentity
    pre_state: SemanticTargetState
    opaque_handle: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class SemanticTargetResolution:
    status: SemanticResolutionStatus
    reason: str
    target: SemanticResolvedTarget | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", SemanticResolutionStatus(self.status))
        object.__setattr__(self, "reason", _identifier("reason", self.reason))
        if (self.status is SemanticResolutionStatus.EXACT) != (self.target is not None):
            raise ValueError("only an exact resolution may contain one target")


@dataclass(frozen=True)
class SemanticDispatchOutcome:
    applied: bool | None
    reason: str

    def __post_init__(self) -> None:
        if self.applied not in {True, False, None}:
            raise ValueError("applied must be true, false, or null")
        object.__setattr__(self, "reason", _identifier("reason", self.reason))


@dataclass(frozen=True)
class SemanticVerification:
    verified: bool
    post_state: SemanticTargetState | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.verified, bool):
            raise ValueError("verified must be boolean")
        if self.verified != (self.post_state is not None):
            raise ValueError("verified results require exactly one post-state")
        object.__setattr__(self, "reason", _identifier("reason", self.reason))


@dataclass(frozen=True)
class SemanticActivationAuthorization:
    allowed: bool | None
    lease_id: str
    reason: str

    def __post_init__(self) -> None:
        if self.allowed not in {True, False, None}:
            raise ValueError("allowed must be true, false, or null")
        object.__setattr__(self, "lease_id", _identifier("lease_id", self.lease_id))
        object.__setattr__(self, "reason", _identifier("reason", self.reason))


@dataclass(frozen=True)
class SemanticActivationReceipt:
    receipt_id: str
    source_id: str
    session_id: str
    lease_id: str
    sequence: int
    actor: str
    origin: PointerOrigin
    channel: str
    instruction_id: str
    action_id: str
    dispatch_kind: PointerDispatchKind
    requested_operation: str
    target_id: str
    target_identity: SemanticTargetIdentity | None
    window_token: str
    observation_id: str
    frame: PointerCoordinateFrame
    focus_before: SemanticFocusSnapshot | None
    focus_after: SemanticFocusSnapshot | None
    pre_state: SemanticTargetState | None
    post_state: SemanticTargetState | None
    requested: bool
    applied: bool | None
    verified: bool
    uncertain: bool
    safe_to_retry: bool
    reason: str
    timestamp: datetime
    audit_previous_hash: str = "0" * 64
    audit_hash: str = "0" * 64
    audit_recorded: bool = False

    def __post_init__(self) -> None:
        for name in (
            "receipt_id",
            "source_id",
            "session_id",
            "lease_id",
            "actor",
            "channel",
            "instruction_id",
            "action_id",
            "requested_operation",
            "target_id",
            "window_token",
            "observation_id",
            "reason",
        ):
            object.__setattr__(self, name, _identifier(name, getattr(self, name)))
        object.__setattr__(self, "origin", PointerOrigin(self.origin))
        object.__setattr__(self, "dispatch_kind", PointerDispatchKind(self.dispatch_kind))
        if self.dispatch_kind not in _SEMANTIC_KINDS:
            raise ValueError("receipt requires a semantic dispatch kind")
        if self.requested_operation != "semantic_activate":
            raise ValueError("semantic receipts cannot claim a physical click")
        if self.applied not in {True, False, None}:
            raise ValueError("applied must be true, false, or null")
        for name in ("requested", "verified", "uncertain", "safe_to_retry"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if self.uncertain and self.safe_to_retry:
            raise ValueError("uncertain activation is never safe to retry")
        if self.verified and self.applied is not True:
            raise ValueError("only an applied activation can be verified")
        object.__setattr__(self, "audit_previous_hash", _hash("audit_previous_hash", self.audit_previous_hash))
        object.__setattr__(self, "audit_hash", _hash("audit_hash", self.audit_hash))
        if self.timestamp.tzinfo is None:
            raise ValueError("receipt timestamp must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        def _value(value: object) -> object:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc).isoformat()
            if hasattr(value, "__dataclass_fields__"):
                return {key: _value(item) for key, item in asdict(value).items()}
            if isinstance(value, dict):
                return {str(key): _value(item) for key, item in value.items()}
            return value

        return {key: _value(value) for key, value in asdict(self).items()}


@runtime_checkable
class SemanticActivationSessionPort(Protocol):
    def authorize_semantic_activation(
        self,
        *,
        state: PointerSourceState,
        request: SemanticActivationRequest,
    ) -> SemanticActivationAuthorization: ...


@runtime_checkable
class SemanticObservationPort(Protocol):
    def claim_semantic_observation(
        self,
        request: SemanticActivationRequest,
    ) -> SemanticObservation: ...


@runtime_checkable
class SemanticBindingPort(Protocol):
    def current_focus(
        self,
        expected: SemanticWindowBinding,
    ) -> SemanticFocusSnapshot: ...


@runtime_checkable
class SemanticTargetAdapter(Protocol):
    @property
    def dispatch_kind(self) -> PointerDispatchKind: ...

    def resolve_exact(
        self,
        observation: SemanticObservation,
        request: SemanticActivationRequest,
    ) -> SemanticTargetResolution: ...

    def activate(
        self,
        target: SemanticResolvedTarget,
        request: SemanticActivationRequest,
    ) -> SemanticDispatchOutcome: ...

    def verify(
        self,
        target: SemanticResolvedTarget,
        request: SemanticActivationRequest,
    ) -> SemanticVerification: ...


@runtime_checkable
class SemanticActivationAuditPort(Protocol):
    def append_semantic_receipt(
        self,
        receipt: SemanticActivationReceipt,
    ) -> PointerAuditStamp: ...


@dataclass
class CallbackSemanticTargetAdapter:
    """Backend-/system-agnostic adapter for DOM/WebDriver/CDP or accessibility."""

    dispatch_kind: PointerDispatchKind
    resolve_callback: Callable[[SemanticObservation, SemanticActivationRequest], SemanticTargetResolution]
    activate_callback: Callable[[SemanticResolvedTarget, SemanticActivationRequest], SemanticDispatchOutcome]
    verify_callback: Callable[[SemanticResolvedTarget, SemanticActivationRequest], SemanticVerification]

    def __post_init__(self) -> None:
        self.dispatch_kind = PointerDispatchKind(self.dispatch_kind)
        if self.dispatch_kind not in _SEMANTIC_KINDS:
            raise ValueError("callback adapter must be browser or accessibility semantic")

    def resolve_exact(self, observation, request):
        return self.resolve_callback(observation, request)

    def activate(self, target, request):
        return self.activate_callback(target, request)

    def verify(self, target, request):
        return self.verify_callback(target, request)


@dataclass(frozen=True)
class RegistryObservationSnapshot:
    observation: SemanticObservation
    payload: object = field(repr=False)
    window: Mapping[str, Any] = field(repr=False)


@dataclass
class RegistrySemanticObservationPort:
    """Use the existing one-shot ObservationRegistry behind the semantic port."""

    registry: ObservationRegistry
    snapshot_provider: Callable[[SemanticActivationRequest], RegistryObservationSnapshot]
    _claimed_pairs: set[tuple[str, str]] = field(default_factory=set, init=False)

    def claim_semantic_observation(
        self,
        request: SemanticActivationRequest,
    ) -> SemanticObservation:
        observation_id = request.provenance.observation_id
        window_token = request.provenance.window_token
        assert observation_id is not None and window_token is not None
        pair = (observation_id, window_token)
        if pair in self._claimed_pairs:
            raise RuntimeError("semantic observation/window token pair already consumed")
        self._claimed_pairs.add(pair)
        snapshot = self.snapshot_provider(request)
        if snapshot.observation.observation_id != observation_id:
            raise RuntimeError("snapshot observation_id mismatch")
        self.registry.claim(
            observation_id,
            payload=snapshot.payload,
            window=snapshot.window,
            frame=pointer_frame_record(snapshot.observation.frame),
        )
        return snapshot.observation


@dataclass(frozen=True)
class _BrowserSemanticHandle:
    target_handle: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_handle",
            _identifier("target_handle", self.target_handle),
        )


@dataclass
class BrowserDriverSemanticAdapter:
    """Concrete adapter for the SemanticBrowserDriver DOM/WebDriver/CDP seam."""

    driver: SemanticBrowserDriver
    postcondition: Callable[[SemanticTargetState, SemanticTargetState], bool]
    dispatch_kind: PointerDispatchKind = field(
        default=PointerDispatchKind.BROWSER_SEMANTIC,
        init=False,
    )
    _claimed_contexts: set[tuple[str, str, str, str, str, int]] = field(
        default_factory=set,
        init=False,
    )

    @staticmethod
    def _claim_key(
        observation: SemanticObservation,
        request: SemanticActivationRequest,
    ) -> tuple[str, str, str, str, str, int]:
        return (
            request.binding.context_id,
            observation.observation_id,
            request.binding.window_token,
            observation.target_id,
            request.binding.frame.frame_id,
            request.binding.frame.generation,
        )

    @staticmethod
    def _mapping(value: object, code: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(code)
        return value

    @staticmethod
    def _context_matches(
        value: Mapping[str, Any],
        observation: SemanticObservation,
        request: SemanticActivationRequest,
    ) -> bool:
        return (
            value.get("context_id") == request.binding.context_id
            and value.get("observation_id") == observation.observation_id
            and value.get("window_token") == request.binding.window_token
            and value.get("frame") == pointer_frame_record(request.binding.frame)
        )

    def _current_context_matches(
        self,
        observation: SemanticObservation,
        request: SemanticActivationRequest,
    ) -> bool:
        current = self._mapping(
            self.driver.semantic_context(),
            "browser-context-invalid",
        )
        return self._context_matches(current, observation, request)

    def resolve_exact(self, observation, request):
        if request.binding.frame.kind is not PointerFrameKind.BROWSER_VIEWPORT_CSS_PX:
            return SemanticTargetResolution(
                SemanticResolutionStatus.NOT_APPLICABLE,
                "desktop-frame-not-browser",
            )
        claim_key = self._claim_key(observation, request)
        if claim_key in self._claimed_contexts:
            return SemanticTargetResolution(
                SemanticResolutionStatus.REJECTED,
                "browser-context-claim-replayed",
            )
        self._claimed_contexts.add(claim_key)
        try:
            if not self._current_context_matches(observation, request):
                raise ValueError("browser context changed")
            raw = self._mapping(
                self.driver.resolve_semantic_target(
                    context_id=request.binding.context_id,
                    observation_id=observation.observation_id,
                    window_token=request.binding.window_token,
                    target_id=observation.target_id,
                    query=request.target_query,
                    frame=pointer_frame_record(request.binding.frame),
                    exact=True,
                ),
                "browser-target-invalid",
            )
            if not self._context_matches(raw, observation, request):
                raise ValueError("browser target binding changed")
            if raw.get("exact") is not True or raw.get("unique") is not True:
                raise ValueError("browser target is not exact and unique")
            if raw.get("target_id") != observation.target_id:
                raise ValueError("browser target id changed")
            identity = SemanticTargetIdentity(
                target_id=str(raw["target_id"]),
                stable_id=str(raw["stable_id"]),
                role=str(raw["role"]),
                context_id=str(raw["context_id"]),
                dispatch_kind=self.dispatch_kind,
            )
            state = SemanticTargetState(str(raw["state_sha256"]))
            handle = _BrowserSemanticHandle(str(raw["target_handle"]))
        except Exception:
            return SemanticTargetResolution(
                SemanticResolutionStatus.REJECTED,
                "browser-exact-resolution-failed",
            )
        return SemanticTargetResolution(
            SemanticResolutionStatus.EXACT,
            "browser-exact-target",
            SemanticResolvedTarget(identity, state, handle),
        )

    def activate(self, target, request):
        handle = target.opaque_handle
        if not isinstance(handle, _BrowserSemanticHandle):
            return SemanticDispatchOutcome(None, "browser-target-handle-invalid")
        observation = SemanticObservation(
            observation_id=request.provenance.observation_id or "missing-observation",
            window_token=request.binding.window_token,
            context_id=request.binding.context_id,
            frame=request.binding.frame,
            target_id=target.identity.target_id,
            target_state_signature=target.pre_state.signature,
        )
        try:
            if not self._current_context_matches(observation, request):
                raise ValueError("browser context changed before activation")
            raw = self._mapping(
                self.driver.activate_semantic_target(
                    context_id=request.binding.context_id,
                    target_handle=handle.target_handle,
                    action_id=request.provenance.action_id,
                ),
                "browser-activation-invalid",
            )
            if (
                raw.get("context_id") != request.binding.context_id
                or raw.get("target_handle") != handle.target_handle
                or raw.get("action_id") != request.provenance.action_id
                or raw.get("applied") not in {True, False, None}
            ):
                raise ValueError("browser activation receipt changed")
            applied = raw["applied"]
            reason = _identifier("reason", str(raw.get("reason", "browser-result")))
        except Exception:
            return SemanticDispatchOutcome(None, "browser-activation-uncertain")
        return SemanticDispatchOutcome(applied, reason)

    def verify(self, target, request):
        handle = target.opaque_handle
        if not isinstance(handle, _BrowserSemanticHandle):
            return SemanticVerification(False, None, "browser-target-handle-invalid")
        observation = SemanticObservation(
            observation_id=request.provenance.observation_id or "missing-observation",
            window_token=request.binding.window_token,
            context_id=request.binding.context_id,
            frame=request.binding.frame,
            target_id=target.identity.target_id,
            target_state_signature=target.pre_state.signature,
        )
        try:
            if not self._current_context_matches(observation, request):
                raise ValueError("browser context changed before verification")
            raw = self._mapping(
                self.driver.semantic_target_state(
                    context_id=request.binding.context_id,
                    target_handle=handle.target_handle,
                ),
                "browser-post-state-invalid",
            )
            if (
                not self._context_matches(raw, observation, request)
                or raw.get("target_id") != target.identity.target_id
                or raw.get("stable_id") != target.identity.stable_id
                or raw.get("target_handle") != handle.target_handle
            ):
                raise ValueError("browser post-state binding changed")
            after = SemanticTargetState(str(raw["state_sha256"]))
            verified = bool(self.postcondition(target.pre_state, after))
        except Exception:
            return SemanticVerification(False, None, "browser-post-state-unavailable")
        return SemanticVerification(
            verified,
            after if verified else None,
            "browser-post-state-verified" if verified else "browser-postcondition-failed",
        )


def uia_target_stable_id(target: object) -> str:
    """Opaque exact identity from the existing UIA Target's stable fields."""

    name = str(getattr(target, "name"))
    role = str(getattr(target, "role"))
    rect = tuple(int(value) for value in getattr(target, "rect_px"))
    material = repr((name.casefold(), role.casefold(), rect)).encode("utf-8")
    return "uia_1_" + hashlib.sha256(material).hexdigest()[:24]


@dataclass
class UiaTargeterSemanticAdapter:
    """Bind an existing UIA Targeter without enabling coordinate fallback."""

    targeter: object
    state_probe: Callable[[object, SemanticActivationRequest], SemanticTargetState]
    postcondition: Callable[[SemanticTargetState, SemanticTargetState], bool]
    window_hint: Callable[[SemanticActivationRequest], str | None] = lambda _request: None
    dispatch_kind: PointerDispatchKind = field(
        default=PointerDispatchKind.ACCESSIBILITY_SEMANTIC,
        init=False,
    )

    def resolve_exact(self, observation, request):
        if request.binding.frame.kind is PointerFrameKind.BROWSER_VIEWPORT_CSS_PX:
            return SemanticTargetResolution(
                SemanticResolutionStatus.NOT_APPLICABLE,
                "browser-frame-not-uia",
            )
        try:
            target = self.targeter.resolve_detailed(
                request.target_query,
                window=self.window_hint(request),
                exact=True,
                min_score=1.0,
            )
            stable_id = uia_target_stable_id(target)
            identity = SemanticTargetIdentity(
                target_id=observation.target_id,
                stable_id=stable_id,
                role=str(target.role),
                context_id=request.binding.context_id,
                dispatch_kind=self.dispatch_kind,
            )
            state = self.state_probe(target, request)
        except Exception:
            return SemanticTargetResolution(
                SemanticResolutionStatus.REJECTED,
                "uia-exact-resolution-failed",
            )
        return SemanticTargetResolution(
            SemanticResolutionStatus.EXACT,
            "uia-exact-target",
            SemanticResolvedTarget(identity, state, target),
        )

    def activate(self, target, request):
        try:
            applied = bool(
                self.targeter.invoke_target(
                    target.opaque_handle,
                    window=self.window_hint(request),
                )
            )
        except Exception:
            return SemanticDispatchOutcome(None, "uia-invocation-uncertain")
        return SemanticDispatchOutcome(
            applied,
            "uia-pattern-applied" if applied else "uia-pattern-unavailable",
        )

    def verify(self, target, request):
        try:
            current = self.targeter.resolve_detailed(
                request.target_query,
                window=self.window_hint(request),
                exact=True,
                min_score=1.0,
            )
            if uia_target_stable_id(current) != target.identity.stable_id:
                return SemanticVerification(False, None, "uia-target-changed")
            after = self.state_probe(current, request)
            verified = bool(self.postcondition(target.pre_state, after))
        except Exception:
            return SemanticVerification(False, None, "uia-post-state-unavailable")
        return SemanticVerification(
            verified,
            after if verified else None,
            "uia-post-state-verified" if verified else "uia-postcondition-failed",
        )


class VirtualTargetActivationCore:
    """Browser-first, UIA-second semantic dispatcher with no OS fallback."""

    def __init__(
        self,
        *,
        state: PointerSourceState,
        session: SemanticActivationSessionPort,
        observations: SemanticObservationPort,
        binding: SemanticBindingPort,
        browser: SemanticTargetAdapter,
        accessibility: SemanticTargetAdapter,
        audit: SemanticActivationAuditPort,
        now: Callable[[], datetime] | None = None,
        receipt_id: Callable[[], str] | None = None,
    ) -> None:
        if browser.dispatch_kind is not PointerDispatchKind.BROWSER_SEMANTIC:
            raise ValueError("browser adapter must declare browser_semantic")
        if accessibility.dispatch_kind is not PointerDispatchKind.ACCESSIBILITY_SEMANTIC:
            raise ValueError("accessibility adapter must declare accessibility_semantic")
        self._state = state
        self._session = session
        self._observations = observations
        self._binding = binding
        self._browser = browser
        self._accessibility = accessibility
        self._audit = audit
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._receipt_id = receipt_id or (lambda: uuid4().hex)
        self._seen_action_ids: set[str] = set()
        self._consumed_claims: set[tuple[str, str]] = set()

    @property
    def state(self) -> PointerSourceState:
        return self._state

    def activate(self, request: SemanticActivationRequest) -> SemanticActivationReceipt:
        before = self._state
        provenance = request.provenance
        early_reason = self._validate_before_claim(before, request)
        if provenance.action_id in self._seen_action_ids:
            early_reason = "action-replay"
        else:
            self._seen_action_ids.add(provenance.action_id)
        if early_reason is not None:
            return self._receipt(before, request, reason=early_reason, applied=False)

        try:
            authorization = self._session.authorize_semantic_activation(
                state=before,
                request=request,
            )
        except Exception:
            return self._uncertain(before, request, "session-port-failed")
        if not isinstance(authorization, SemanticActivationAuthorization):
            return self._uncertain(before, request, "session-port-invalid")
        if authorization.lease_id != before.lease_id:
            return self._receipt(before, request, reason="lease-mismatch", applied=False)
        if authorization.allowed is None:
            return self._uncertain(before, request, "session-authorization-uncertain")
        if not authorization.allowed:
            return self._receipt(before, request, reason="session-denied", applied=False)

        observation_id = provenance.observation_id
        window_token = provenance.window_token
        assert observation_id is not None and window_token is not None
        claim_key = (observation_id, window_token)
        if claim_key in self._consumed_claims:
            return self._receipt(
                before,
                request,
                reason="observation-window-token-replayed",
                applied=False,
            )
        self._consumed_claims.add(claim_key)
        try:
            observation = self._observations.claim_semantic_observation(request)
        except Exception:
            return self._receipt(before, request, reason="observation-claim-failed", applied=False)
        self._advance(request, uncertain=False)
        observation_error = self._validate_observation(observation, request)
        if observation_error is not None:
            return self._receipt(before, request, reason=observation_error, applied=False)

        try:
            focus_before = self._binding.current_focus(request.binding)
        except Exception:
            return self._receipt(before, request, reason="focus-precheck-failed", applied=False)
        if focus_before.binding != request.binding or not focus_before.focused:
            return self._receipt(
                before,
                request,
                reason="focus-binding-mismatch-before",
                applied=False,
                focus_before=focus_before,
            )

        selected: SemanticTargetAdapter | None = None
        resolution: SemanticTargetResolution | None = None
        for adapter in (self._browser, self._accessibility):
            try:
                candidate = adapter.resolve_exact(observation, request)
            except Exception:
                return self._receipt(
                    before,
                    request,
                    reason="semantic-resolution-failed",
                    applied=False,
                    focus_before=focus_before,
                )
            if not isinstance(candidate, SemanticTargetResolution):
                return self._receipt(
                    before,
                    request,
                    reason="semantic-resolution-invalid",
                    applied=False,
                    focus_before=focus_before,
                )
            if candidate.status is SemanticResolutionStatus.NOT_APPLICABLE:
                continue
            selected, resolution = adapter, candidate
            break
        if selected is None or resolution is None:
            return self._receipt(
                before,
                request,
                reason="no-semantic-path-no-os-fallback",
                applied=False,
                focus_before=focus_before,
            )
        if resolution.status is SemanticResolutionStatus.REJECTED:
            return self._receipt(
                before,
                request,
                reason=resolution.reason,
                applied=False,
                focus_before=focus_before,
            )
        target = resolution.target
        assert target is not None
        target_error = self._validate_target(target, observation, request, selected)
        if target_error is not None:
            return self._receipt(
                before,
                request,
                reason=target_error,
                applied=False,
                focus_before=focus_before,
                target=target,
            )

        try:
            outcome = selected.activate(target, request)
        except Exception:
            return self._uncertain(
                before,
                request,
                "semantic-dispatch-uncertain",
                focus_before=focus_before,
                target=target,
                dispatch_kind=selected.dispatch_kind,
            )
        if not isinstance(outcome, SemanticDispatchOutcome) or outcome.applied is None:
            return self._uncertain(
                before,
                request,
                "semantic-dispatch-uncertain",
                focus_before=focus_before,
                target=target,
                dispatch_kind=selected.dispatch_kind,
            )
        if outcome.applied is False:
            return self._receipt(
                before,
                request,
                reason=outcome.reason,
                applied=False,
                focus_before=focus_before,
                target=target,
                dispatch_kind=selected.dispatch_kind,
                pre_state=target.pre_state,
            )

        try:
            focus_after = self._binding.current_focus(request.binding)
        except Exception:
            return self._uncertain(
                before,
                request,
                "focus-postcheck-failed",
                focus_before=focus_before,
                target=target,
                dispatch_kind=selected.dispatch_kind,
            )
        if focus_after.binding != request.binding or not focus_after.focused:
            return self._uncertain(
                before,
                request,
                "focus-binding-mismatch-after",
                focus_before=focus_before,
                focus_after=focus_after,
                target=target,
                dispatch_kind=selected.dispatch_kind,
            )
        try:
            verification = selected.verify(target, request)
        except Exception:
            verification = None
        if not isinstance(verification, SemanticVerification) or not verification.verified:
            return self._uncertain(
                before,
                request,
                "post-verification-failed",
                focus_before=focus_before,
                focus_after=focus_after,
                target=target,
                dispatch_kind=selected.dispatch_kind,
            )
        return self._receipt(
            before,
            request,
            reason=verification.reason,
            applied=True,
            verified=True,
            focus_before=focus_before,
            focus_after=focus_after,
            target=target,
            dispatch_kind=selected.dispatch_kind,
            pre_state=target.pre_state,
            post_state=verification.post_state,
        )

    @staticmethod
    def _validate_before_claim(
        state: PointerSourceState,
        request: SemanticActivationRequest,
    ) -> str | None:
        if request.sequence < state.sequence + 1:
            return "sequence-replay"
        if request.sequence > state.sequence + 1:
            return "sequence-out-of-order"
        if request.provenance.origin in _UNTRUSTED_ORIGINS:
            return f"origin-{request.provenance.origin.value}-cannot-authorize"
        if state.phase is PointerPhase.UNCERTAIN:
            return "cleanup-required-after-uncertain"
        if state.phase not in {PointerPhase.PREVIEW, PointerPhase.ARMED}:
            return "pointer-not-active"
        if state.pressed_buttons:
            return "semantic-activation-requires-no-virtual-holds"
        if state.frame != request.binding.frame:
            return "pointer-frame-binding-mismatch"
        return None

    @staticmethod
    def _validate_observation(
        observation: SemanticObservation,
        request: SemanticActivationRequest,
    ) -> str | None:
        provenance = request.provenance
        if observation.observation_id != provenance.observation_id:
            return "observation-id-mismatch"
        if observation.window_token != provenance.window_token:
            return "observation-window-token-mismatch"
        if observation.context_id != request.binding.context_id:
            return "observation-context-mismatch"
        if observation.frame != request.binding.frame:
            return "observation-frame-mismatch"
        if observation.target_id != provenance.target_id:
            return "observation-target-mismatch"
        return None

    @staticmethod
    def _validate_target(
        target: SemanticResolvedTarget,
        observation: SemanticObservation,
        request: SemanticActivationRequest,
        adapter: SemanticTargetAdapter,
    ) -> str | None:
        identity = target.identity
        if identity.dispatch_kind is not adapter.dispatch_kind:
            return "target-dispatch-kind-mismatch"
        if (
            adapter.dispatch_kind is PointerDispatchKind.BROWSER_SEMANTIC
            and request.binding.frame.kind is not PointerFrameKind.BROWSER_VIEWPORT_CSS_PX
        ):
            return "browser-dispatch-requires-browser-context"
        if (
            adapter.dispatch_kind is PointerDispatchKind.ACCESSIBILITY_SEMANTIC
            and request.binding.frame.kind is PointerFrameKind.BROWSER_VIEWPORT_CSS_PX
        ):
            return "uia-dispatch-requires-desktop-frame"
        if identity.target_id != observation.target_id:
            return "resolved-target-id-mismatch"
        if identity.context_id != request.binding.context_id:
            return "resolved-target-context-mismatch"
        if target.pre_state.signature != observation.target_state_signature:
            return "resolved-target-state-changed"
        return None

    def _advance(self, request: SemanticActivationRequest, *, uncertain: bool) -> None:
        self._state = replace(
            self._state,
            sequence=request.sequence,
            phase=PointerPhase.UNCERTAIN if uncertain else self._state.phase,
            last_action_id=request.provenance.action_id,
        )

    def _uncertain(
        self,
        before: PointerSourceState,
        request: SemanticActivationRequest,
        reason: str,
        **kwargs,
    ) -> SemanticActivationReceipt:
        self._advance(request, uncertain=True)
        return self._receipt(before, request, reason=reason, applied=None, uncertain=True, **kwargs)

    def _receipt(
        self,
        before: PointerSourceState,
        request: SemanticActivationRequest,
        *,
        reason: str,
        applied: bool | None,
        verified: bool = False,
        uncertain: bool = False,
        focus_before: SemanticFocusSnapshot | None = None,
        focus_after: SemanticFocusSnapshot | None = None,
        target: SemanticResolvedTarget | None = None,
        dispatch_kind: PointerDispatchKind | None = None,
        pre_state: SemanticTargetState | None = None,
        post_state: SemanticTargetState | None = None,
    ) -> SemanticActivationReceipt:
        provenance = request.provenance
        receipt = SemanticActivationReceipt(
            receipt_id=self._receipt_id(),
            source_id=before.source_id,
            session_id=before.session_id,
            lease_id=before.lease_id,
            sequence=request.sequence,
            actor=provenance.actor,
            origin=provenance.origin,
            channel=provenance.channel,
            instruction_id=provenance.instruction_id,
            action_id=provenance.action_id,
            dispatch_kind=dispatch_kind or provenance.dispatch_kind,
            requested_operation="semantic_activate",
            target_id=provenance.target_id or "missing-target",
            target_identity=target.identity if target is not None else None,
            window_token=provenance.window_token or "missing-window",
            observation_id=provenance.observation_id or "missing-observation",
            frame=request.binding.frame,
            focus_before=focus_before,
            focus_after=focus_after,
            pre_state=pre_state,
            post_state=post_state,
            requested=True,
            applied=applied,
            verified=verified,
            uncertain=uncertain,
            safe_to_retry=False,
            reason=reason,
            timestamp=self._now(),
        )
        try:
            stamp = self._audit.append_semantic_receipt(receipt)
            if not isinstance(stamp, PointerAuditStamp):
                raise TypeError("audit port returned an invalid stamp")
            return replace(
                receipt,
                audit_previous_hash=stamp.previous_hash,
                audit_hash=stamp.audit_hash,
                audit_recorded=True,
            )
        except Exception:
            self._state = replace(self._state, phase=PointerPhase.UNCERTAIN)
            return replace(
                receipt,
                applied=None,
                verified=False,
                uncertain=True,
                safe_to_retry=False,
                reason="audit-port-failed",
            )


__all__ = [
    "BrowserDriverSemanticAdapter",
    "CallbackSemanticTargetAdapter",
    "RegistryObservationSnapshot",
    "RegistrySemanticObservationPort",
    "SemanticActivationAuditPort",
    "SemanticActivationAuthorization",
    "SemanticActivationReceipt",
    "SemanticActivationRequest",
    "SemanticActivationSessionPort",
    "SemanticBindingPort",
    "SemanticDispatchOutcome",
    "SemanticFocusSnapshot",
    "SemanticObservation",
    "SemanticObservationPort",
    "SemanticResolutionStatus",
    "SemanticResolvedTarget",
    "SemanticTargetAdapter",
    "SemanticTargetIdentity",
    "SemanticTargetResolution",
    "SemanticTargetState",
    "SemanticVerification",
    "SemanticWindowBinding",
    "UiaTargeterSemanticAdapter",
    "VirtualTargetActivationCore",
    "pointer_frame_record",
    "uia_target_stable_id",
]
