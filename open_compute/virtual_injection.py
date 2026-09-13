"""Fail-closed Windows OS-pointer injection fallback.

The fallback is disabled by default.  It is not a general executor: it accepts
only one-shot virtual-pointer move/press/release requests after semantic
browser and accessibility dispatch have been exhausted.  Session, human
priority, emergency stop, focus/process, observation, DPI/frame, desktop/UIPI,
and final pre-click evidence are all injected ports and are rechecked before a
concrete Windows host may reach ``SendInput``.

No code in this module reads the physical cursor.  Virtual/overlay position is
carried in the receipt separately from the optional OS post-state supplied by
a verifier.  Tests replace the single native-call seam completely.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Protocol, Sequence
from uuid import uuid4

from .human_activity import (
    ActivityAssessment,
    HumanActivityClassifier,
    InputProvenance,
    LastInputSample,
)
from .preclick import WindowProbe, Win32WindowProbe
from .session import ControlSession
from .virtual_pointer import (
    PointerButton,
    PointerCoordinateFrame,
    PointerDispatchKind,
    PointerFrameKind,
    PointerOrigin,
    PointerAuditStamp,
    PointerPosition,
    PointerProvenance,
    PointerSourceState,
    PointerTransition,
)
from .virtual_target import SemanticFocusSnapshot, SemanticWindowBinding


_REQUIRED_GATES = frozenset(
    {
        "lease",
        "human_priority",
        "emergency_stop",
        "observation",
        "focus",
        "dpi_frame",
        "desktop_uipi",
        "preclick",
    }
)
_UNTRUSTED_ORIGINS = frozenset({PointerOrigin.SCREEN, PointerOrigin.UNKNOWN})


class InjectionFailure(str, Enum):
    DISABLED = "disabled"
    INVALID_REQUEST = "invalid_request"
    LEASE = "lease_denied"
    HUMAN_PRIORITY = "human_priority"
    EMERGENCY_STOP = "emergency_stop"
    OBSERVATION = "observation_invalid"
    FOCUS = "focus_binding_changed"
    DPI_FRAME = "dpi_frame_invalid"
    SECURE_DESKTOP = "secure_desktop"
    UIPI = "uipi_blocked"
    PRECLICK = "preclick_failed"
    NATIVE_ERROR = "native_error"
    PARTIAL = "partial_injection"
    POST_FOCUS = "post_focus_changed"
    VERIFY = "verification_failed"
    ADAPTER_ERROR = "adapter_error"


@dataclass(frozen=True)
class InjectionPolicy:
    """Explicit opt-in; construction without arguments can never inject."""

    enabled: bool = False
    allow_press_holds: bool = False


@dataclass(frozen=True)
class InjectionAuthorization:
    allowed: bool | None
    lease_id: str
    reason: str


@dataclass(frozen=True)
class InjectionObservation:
    observation_id: str
    window_token: str
    frame: PointerCoordinateFrame
    position: PointerPosition


@dataclass(frozen=True)
class DpiFrameEvidence:
    per_monitor_v2: bool | None
    frame: PointerCoordinateFrame | None
    stable: bool
    reason: str


@dataclass(frozen=True)
class InputSecurityEvidence:
    normal_input_desktop: bool | None
    uipi_allowed: bool | None
    reason: str


@dataclass(frozen=True)
class PreclickEvidence:
    verified: bool
    window_token: str
    point_px: tuple[int, int] | None
    reason: str


@dataclass(frozen=True)
class InjectionVerification:
    verified: bool
    applied: bool | None
    os_position: PointerPosition | None
    reason: str


@dataclass(frozen=True)
class NativeMousePacket:
    dx: int
    dy: int
    flags: int
    mouse_data: int = 0


@dataclass(frozen=True)
class NativeInjectionOutcome:
    requested_count: int
    inserted_count: int
    reason: str

    @property
    def complete(self) -> bool:
        return self.inserted_count == self.requested_count and self.reason in {
            "sendinput_complete",
            "cleanup_complete",
            "nothing_held",
        }


@dataclass(frozen=True)
class InjectionPermit:
    """Host-side proof that every required gate passed for this exact action."""

    permit_id: str
    action_id: str
    lease_id: str
    observation_id: str
    window_token: str
    frame_id: str
    frame_generation: int
    request_fingerprint: str
    gate_names: frozenset[str]
    one_shot: bool

    def authorizes(self, request: "VirtualInjectionRequest") -> bool:
        return (
            self.one_shot
            and self.gate_names == _REQUIRED_GATES
            and self.action_id == request.provenance.action_id
            and self.lease_id == request.lease_id
            and self.observation_id == request.observation_id
            and self.window_token == request.binding.window_token
            and self.frame_id == request.frame.frame_id
            and self.frame_generation == request.frame.generation
            and self.request_fingerprint == injection_request_fingerprint(request)
        )


@dataclass(frozen=True)
class VirtualInjectionRequest:
    sequence: int
    lease_id: str
    provenance: PointerProvenance
    transition: PointerTransition
    position: PointerPosition
    frame: PointerCoordinateFrame
    binding: SemanticWindowBinding
    observation_id: str
    button: PointerButton | None = None
    semantic_paths_exhausted: bool = False
    native_pointer_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "transition", PointerTransition(self.transition))
        if self.button is not None:
            object.__setattr__(self, "button", PointerButton(self.button))
        if self.provenance.dispatch_kind is not PointerDispatchKind.OS_INPUT_INJECTION:
            raise ValueError("OS fallback requires os_input_injection provenance")
        if self.transition not in {
            PointerTransition.MOVE,
            PointerTransition.PRESS,
            PointerTransition.RELEASE,
        }:
            raise ValueError("OS fallback supports only move, press, and release")
        if self.transition in {PointerTransition.PRESS, PointerTransition.RELEASE}:
            if self.button is None:
                raise ValueError("press and release require a button")
        elif self.button is not None:
            raise ValueError("move must not carry a button")
        if self.frame.kind is not PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX:
            raise ValueError("OS fallback requires a physical virtual-desktop frame")
        if not self.frame.contains(self.position):
            raise ValueError("position is outside the physical frame")
        if self.binding.frame != self.frame:
            raise ValueError("request and binding frames must match")
        if self.binding.window_token != self.provenance.window_token:
            raise ValueError("provenance and binding window_token must match")
        if self.provenance.observation_id != self.observation_id:
            raise ValueError("provenance and request observation_id must match")
        if self.provenance.target_id is None:
            raise ValueError("OS fallback requires an exact target_id")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        if not self.lease_id.strip() or not self.observation_id.strip():
            raise ValueError("lease_id and observation_id are required")


def injection_request_fingerprint(request: VirtualInjectionRequest) -> str:
    """Hash every immutable field that can authorize or change native effect."""

    frame = request.frame
    binding = request.binding
    provenance = request.provenance
    payload = {
        "sequence": request.sequence,
        "lease_id": request.lease_id,
        "transition": request.transition.value,
        "button": request.button.value if request.button is not None else None,
        "position": {"x": request.position.x, "y": request.position.y},
        "frame": {
            "frame_id": frame.frame_id,
            "kind": frame.kind.value,
            "left": frame.left,
            "top": frame.top,
            "width": frame.width,
            "height": frame.height,
            "generation": frame.generation,
            "scale_x": frame.scale_x,
            "scale_y": frame.scale_y,
            "context_id": frame.context_id,
        },
        "binding": {
            "window_token": binding.window_token,
            "hwnd": binding.hwnd,
            "pid": binding.pid,
            "process_token": binding.process_token,
            "title_sha256": binding.title_sha256,
            "context_id": binding.context_id,
        },
        "provenance": {
            "actor": provenance.actor,
            "origin": provenance.origin.value,
            "channel": provenance.channel,
            "instruction_id": provenance.instruction_id,
            "action_id": provenance.action_id,
            "dispatch_kind": provenance.dispatch_kind.value,
            "observation_id": provenance.observation_id,
            "window_token": provenance.window_token,
            "target_id": provenance.target_id,
        },
        "observation_id": request.observation_id,
        "semantic_paths_exhausted": request.semantic_paths_exhausted,
        "native_pointer_required": request.native_pointer_required,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class VirtualInjectionReceipt:
    receipt_id: str
    source_id: str
    session_id: str
    lease_id: str
    sequence: int
    provenance: PointerProvenance
    transition: PointerTransition
    button: PointerButton | None
    dispatch_kind: PointerDispatchKind
    window_token: str
    observation_id: str
    frame: PointerCoordinateFrame
    focus_before: SemanticFocusSnapshot | None
    focus_after: SemanticFocusSnapshot | None
    virtual_position: PointerPosition
    os_position: PointerPosition | None
    requested: bool
    applied: bool | None
    verified: bool
    uncertain: bool
    safe_to_retry: bool
    inserted_count: int
    requested_count: int
    failure: InjectionFailure | None
    reason: str
    cleanup_attempted: bool
    cleanup_complete: bool
    timestamp: datetime
    audit_previous_hash: str = "0" * 64
    audit_hash: str = "0" * 64
    audit_recorded: bool = False

    def __post_init__(self) -> None:
        if self.dispatch_kind is not PointerDispatchKind.OS_INPUT_INJECTION:
            raise ValueError("receipt dispatch kind must be os_input_injection")
        if self.uncertain and self.safe_to_retry:
            raise ValueError("uncertain injection is never safe to retry")
        if self.verified and self.applied is not True:
            raise ValueError("verified injection must be applied")


class InjectionSessionPort(Protocol):
    def authorize_os_injection(
        self, *, state: PointerSourceState, request: VirtualInjectionRequest
    ) -> InjectionAuthorization: ...

    def human_priority_pause(self, reason: str) -> None: ...


class HumanActivityPort(Protocol):
    def assess_activity(self) -> ActivityAssessment: ...


class EmergencyStopPort(Protocol):
    def is_triggered(self) -> bool: ...


class InjectionObservationPort(Protocol):
    def claim_observation(self, request: VirtualInjectionRequest) -> InjectionObservation: ...


class InjectionFocusPort(Protocol):
    def current_focus(self, expected: SemanticWindowBinding) -> SemanticFocusSnapshot: ...


class InjectionDpiFramePort(Protocol):
    def current_dpi_frame(self, expected: PointerCoordinateFrame) -> DpiFrameEvidence: ...


class InputSecurityPort(Protocol):
    def inspect_input_security(self, expected: SemanticWindowBinding) -> InputSecurityEvidence: ...


class InjectionPreclickPort(Protocol):
    def verify_preclick(self, request: VirtualInjectionRequest) -> PreclickEvidence: ...


class InjectionVerificationPort(Protocol):
    def verify_injection(
        self, request: VirtualInjectionRequest, outcome: NativeInjectionOutcome
    ) -> InjectionVerification: ...


class InjectionAuditPort(Protocol):
    def append_injection_receipt(
        self, receipt: VirtualInjectionReceipt
    ) -> PointerAuditStamp: ...


class ControlSessionInjectionAdapter:
    """Bind the fallback to an existing exact-scope :class:`ControlSession`."""

    def __init__(self, session: ControlSession) -> None:
        self.session = session

    def authorize_os_injection(
        self, *, state: PointerSourceState, request: VirtualInjectionRequest
    ) -> InjectionAuthorization:
        lease = self.session.lease
        if lease is None or lease.lease_id != state.lease_id or lease.lease_id != request.lease_id:
            return InjectionAuthorization(False, request.lease_id, "lease_identity_mismatch")
        allowed, reason = self.session.authorize(
            f"os_input:{request.binding.window_token}", require_exact_scope=True
        )
        return InjectionAuthorization(allowed, lease.lease_id, reason.replace(" ", "_"))

    def human_priority_pause(self, reason: str) -> None:
        self.session.human_activity()


class ClassifiedHumanActivityAdapter:
    """One-shot sample/classifier bridge; it installs no hook or watcher."""

    def __init__(
        self,
        sample: Callable[[], LastInputSample],
        classifier: HumanActivityClassifier,
    ) -> None:
        self._sample = sample
        self._classifier = classifier

    def assess_activity(self) -> ActivityAssessment:
        return self._classifier.assess(self._sample())


class WindowsInputSecurityProbe:
    """Fail-closed security adapter; a platform integrator must attest both facts."""

    def __init__(
        self,
        query: Callable[[SemanticWindowBinding], InputSecurityEvidence] | None = None,
    ) -> None:
        self._query = query

    def inspect_input_security(self, expected: SemanticWindowBinding) -> InputSecurityEvidence:
        if self._query is None:
            return InputSecurityEvidence(None, None, "security_context_not_attested")
        try:
            return self._query(expected)
        except Exception:
            return InputSecurityEvidence(None, None, "security_context_query_failed")


class WindowProbePreclickAdapter:
    """Concrete final physical-pixel/window check without performing input."""

    def __init__(self, probe: WindowProbe | None = None) -> None:
        self._probe = probe or Win32WindowProbe()

    def verify_preclick(self, request: VirtualInjectionRequest) -> PreclickEvidence:
        point = (int(round(request.position.x)), int(round(request.position.y)))
        actual = self._probe.window_at_point(*point)
        if actual is None:
            return PreclickEvidence(False, request.binding.window_token, point, "window_unresolvable")
        title = " ".join(str(actual.get("title", "")).split()).casefold()
        title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
        valid = (
            int(actual.get("hwnd", 0)) == request.binding.hwnd
            and int(actual.get("pid", 0)) == request.binding.pid
            and title_hash == request.binding.title_sha256
        )
        return PreclickEvidence(
            valid,
            request.binding.window_token,
            point,
            "preclick_verified" if valid else "window_identity_mismatch",
        )


MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000


def _native_send_input(packets: Sequence[NativeMousePacket]) -> int:
    """The only native-write seam.  Unit tests replace this function."""

    if sys.platform != "win32":
        raise RuntimeError("SendInput is Windows-only")
    from .drivers.local import _mouse_event, _send_input

    return int(
        _send_input(
            *(
                _mouse_event(packet.flags, packet.dx, packet.dy, packet.mouse_data)
                for packet in packets
            )
        )
    )


class WindowsSendInputHostAdapter:
    """Concrete, default-disabled pointer-only Windows ``SendInput`` adapter."""

    def __init__(
        self,
        policy: InjectionPolicy | None = None,
        *,
        send_input: Callable[[Sequence[NativeMousePacket]], int] | None = None,
    ) -> None:
        self.policy = policy or InjectionPolicy()
        self._send_input = send_input
        self._consumed_permits: set[str] = set()
        self._held: set[PointerButton] = set()

    @property
    def held_buttons(self) -> frozenset[PointerButton]:
        return frozenset(self._held)

    def inject(
        self, request: VirtualInjectionRequest, permit: InjectionPermit
    ) -> NativeInjectionOutcome:
        if not self.policy.enabled:
            return NativeInjectionOutcome(0, 0, "host_disabled")
        if permit.permit_id in self._consumed_permits or not permit.authorizes(request):
            return NativeInjectionOutcome(0, 0, "permit_invalid_or_consumed")
        if request.transition is PointerTransition.PRESS and not self.policy.allow_press_holds:
            return NativeInjectionOutcome(0, 0, "press_holds_disabled")
        packets = self._packets(request)
        self._consumed_permits.add(permit.permit_id)
        sender = self._send_input or _native_send_input
        try:
            inserted = int(sender(packets))
        except Exception:
            return NativeInjectionOutcome(len(packets), 0, "sendinput_failed")
        if inserted == len(packets):
            if request.transition is PointerTransition.PRESS and request.button is not None:
                self._held.add(request.button)
            elif request.transition is PointerTransition.RELEASE and request.button is not None:
                self._held.discard(request.button)
            return NativeInjectionOutcome(len(packets), inserted, "sendinput_complete")
        return NativeInjectionOutcome(len(packets), max(0, inserted), "sendinput_partial")

    def release_all(self) -> NativeInjectionOutcome:
        """Release only buttons this adapter proved it pressed; never move cursor."""

        if not self._held:
            return NativeInjectionOutcome(0, 0, "nothing_held")
        packets = tuple(
            NativeMousePacket(0, 0, self._button_flags(button)[1])
            for button in sorted(self._held, key=lambda value: value.value)
        )
        sender = self._send_input or _native_send_input
        try:
            inserted = int(sender(packets))
        except Exception:
            return NativeInjectionOutcome(len(packets), 0, "cleanup_failed")
        if inserted == len(packets):
            self._held.clear()
            return NativeInjectionOutcome(len(packets), inserted, "cleanup_complete")
        return NativeInjectionOutcome(len(packets), max(0, inserted), "cleanup_partial")

    @staticmethod
    def _button_flags(button: PointerButton) -> tuple[int, int]:
        return {
            PointerButton.LEFT: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            PointerButton.RIGHT: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            PointerButton.MIDDLE: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }[button]

    @classmethod
    def _packets(cls, request: VirtualInjectionRequest) -> tuple[NativeMousePacket, ...]:
        frame = request.frame
        nx = (request.position.x - frame.left) / max(1.0, frame.width - 1.0)
        ny = (request.position.y - frame.top) / max(1.0, frame.height - 1.0)
        dx = max(0, min(65535, int(round(nx * 65535))))
        dy = max(0, min(65535, int(round(ny * 65535))))
        move = NativeMousePacket(
            dx,
            dy,
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        )
        if request.transition is PointerTransition.MOVE:
            return (move,)
        assert request.button is not None
        down, up = cls._button_flags(request.button)
        if request.transition is PointerTransition.PRESS:
            return (move, NativeMousePacket(dx, dy, down | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK))
        return (NativeMousePacket(dx, dy, up | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK),)


@dataclass
class VirtualInjectionFallback:
    policy: InjectionPolicy
    session: InjectionSessionPort
    human_activity: HumanActivityPort
    emergency_stop: EmergencyStopPort
    observations: InjectionObservationPort
    focus: InjectionFocusPort
    dpi_frame: InjectionDpiFramePort
    security: InputSecurityPort
    preclick: InjectionPreclickPort
    verifier: InjectionVerificationPort
    audit: InjectionAuditPort
    host: WindowsSendInputHostAdapter
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def dispatch(
        self, state: PointerSourceState, request: VirtualInjectionRequest
    ) -> VirtualInjectionReceipt:
        gate_names: set[str] = set()
        requested = False
        outcome = NativeInjectionOutcome(0, 0, "not_attempted")
        focus_before: SemanticFocusSnapshot | None = None

        failure, reason = self._validate_request(state, request)
        if failure is not None:
            return self._receipt(state, request, outcome, failure, reason, False, False, None)

        try:
            authorization = self.session.authorize_os_injection(
                state=state, request=request
            )
        except Exception:
            return self._receipt(
                state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                "session_adapter_exception", False, False, None,
            )
        if authorization.allowed is not True or authorization.lease_id != request.lease_id:
            return self._receipt(state, request, outcome, InjectionFailure.LEASE, authorization.reason, False, False, None)
        gate_names.add("lease")

        try:
            activity = self.human_activity.assess_activity()
        except Exception:
            return self._receipt(
                state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                "human_activity_adapter_exception", False, False, None,
            )
        if activity.provenance is InputProvenance.HUMAN and activity.recent:
            try:
                self.session.human_priority_pause("recent_human_input")
            except Exception:
                return self._receipt(
                    state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                    "human_priority_pause_exception", False, False, None,
                )
            return self._receipt(state, request, outcome, InjectionFailure.HUMAN_PRIORITY, "recent_human_input", False, False, None)
        if activity.provenance is InputProvenance.UNKNOWN and activity.recent:
            return self._receipt(state, request, outcome, InjectionFailure.HUMAN_PRIORITY, "recent_input_unknown", False, False, None)
        gate_names.add("human_priority")

        try:
            emergency_stop_triggered = self.emergency_stop.is_triggered()
        except Exception:
            return self._receipt(
                state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                "emergency_stop_adapter_exception", False, False, None,
            )
        if emergency_stop_triggered:
            return self._receipt(state, request, outcome, InjectionFailure.EMERGENCY_STOP, "emergency_stop_latched", False, False, None)
        gate_names.add("emergency_stop")

        try:
            observation = self.observations.claim_observation(request)
        except Exception:
            return self._receipt(state, request, outcome, InjectionFailure.OBSERVATION, "observation_claim_failed", False, False, None)
        if not self._observation_matches(request, observation):
            return self._receipt(state, request, outcome, InjectionFailure.OBSERVATION, "observation_binding_mismatch", False, False, None)
        gate_names.add("observation")

        try:
            focus_before = self.focus.current_focus(request.binding)
        except Exception:
            return self._receipt(
                state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                "focus_adapter_exception", False, False, None,
            )
        if not self._focus_matches(request.binding, focus_before):
            return self._receipt(state, request, outcome, InjectionFailure.FOCUS, "focus_binding_mismatch", False, False, None, focus_before=focus_before)
        gate_names.add("focus")

        try:
            dpi = self.dpi_frame.current_dpi_frame(request.frame)
        except Exception:
            return self._receipt(
                state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                "dpi_frame_adapter_exception", False, False, None,
                focus_before=focus_before,
            )
        if dpi.per_monitor_v2 is not True or not dpi.stable or dpi.frame != request.frame:
            return self._receipt(state, request, outcome, InjectionFailure.DPI_FRAME, dpi.reason, False, False, None, focus_before=focus_before)
        gate_names.add("dpi_frame")

        try:
            security = self.security.inspect_input_security(request.binding)
        except Exception:
            return self._receipt(
                state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                "input_security_adapter_exception", False, False, None,
                focus_before=focus_before,
            )
        if security.normal_input_desktop is not True:
            return self._receipt(state, request, outcome, InjectionFailure.SECURE_DESKTOP, security.reason, False, False, None, focus_before=focus_before)
        if security.uipi_allowed is not True:
            return self._receipt(state, request, outcome, InjectionFailure.UIPI, security.reason, False, False, None, focus_before=focus_before)
        gate_names.add("desktop_uipi")

        try:
            final_focus = self.focus.current_focus(request.binding)
        except Exception:
            return self._receipt(
                state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                "final_focus_adapter_exception", False, False, None,
                focus_before=focus_before,
            )
        if not self._focus_matches(request.binding, final_focus):
            return self._receipt(state, request, outcome, InjectionFailure.FOCUS, "focus_changed_before_preclick", False, False, None, focus_before=focus_before, focus_after=final_focus)
        try:
            preclick = self.preclick.verify_preclick(request)
        except Exception:
            return self._receipt(
                state, request, outcome, InjectionFailure.ADAPTER_ERROR,
                "preclick_adapter_exception", False, False, None,
                focus_before=focus_before, focus_after=final_focus,
            )
        if not preclick.verified or preclick.window_token != request.binding.window_token:
            return self._receipt(state, request, outcome, InjectionFailure.PRECLICK, preclick.reason, False, False, None, focus_before=focus_before, focus_after=final_focus)
        gate_names.add("preclick")

        permit = InjectionPermit(
            permit_id=uuid4().hex,
            action_id=request.provenance.action_id,
            lease_id=request.lease_id,
            observation_id=request.observation_id,
            window_token=request.binding.window_token,
            frame_id=request.frame.frame_id,
            frame_generation=request.frame.generation,
            request_fingerprint=injection_request_fingerprint(request),
            gate_names=frozenset(gate_names),
            one_shot=True,
        )
        requested = True
        try:
            outcome = self.host.inject(request, permit)
        except Exception:
            cleanup = self._safe_cleanup()
            return self._receipt(
                state, request, outcome, InjectionFailure.NATIVE_ERROR,
                "host_adapter_exception", requested, True, None, cleanup,
                focus_before, final_focus,
            )
        if outcome.reason in {"host_disabled", "press_holds_disabled", "permit_invalid_or_consumed"}:
            return self._receipt(state, request, outcome, InjectionFailure.DISABLED, outcome.reason, requested, False, None, focus_before=focus_before, focus_after=final_focus)
        if outcome.inserted_count != outcome.requested_count:
            cleanup = self._safe_cleanup()
            return self._receipt(state, request, outcome, InjectionFailure.PARTIAL if outcome.inserted_count else InjectionFailure.NATIVE_ERROR, outcome.reason, requested, True, None, cleanup, focus_before, final_focus)

        try:
            focus_after = self.focus.current_focus(request.binding)
        except Exception:
            cleanup = self._safe_cleanup()
            return self._receipt(
                state, request, outcome, InjectionFailure.POST_FOCUS,
                "post_focus_adapter_exception", requested, True, None, cleanup,
                focus_before, None,
            )
        if not self._focus_matches(request.binding, focus_after):
            cleanup = self._safe_cleanup()
            return self._receipt(state, request, outcome, InjectionFailure.POST_FOCUS, "focus_changed_after_injection", requested, True, None, cleanup, focus_before, focus_after)
        try:
            verification = self.verifier.verify_injection(request, outcome)
        except Exception:
            cleanup = self._safe_cleanup()
            return self._receipt(
                state, request, outcome, InjectionFailure.VERIFY,
                "verification_adapter_exception", requested, True, None,
                cleanup, focus_before, focus_after,
            )
        if not verification.verified or verification.applied is not True:
            cleanup = self._safe_cleanup()
            return self._receipt(state, request, outcome, InjectionFailure.VERIFY, verification.reason, requested, verification.applied is None, verification, cleanup, focus_before, focus_after)
        return self._receipt(state, request, outcome, None, verification.reason, requested, False, verification, None, focus_before, focus_after)

    def abort(self) -> NativeInjectionOutcome:
        """Abort/recovery path: release proven holds; overlay cleanup is separate."""

        return self._safe_cleanup()

    def _safe_cleanup(self) -> NativeInjectionOutcome:
        try:
            return self.host.release_all()
        except Exception:
            return NativeInjectionOutcome(
                len(self.host.held_buttons), 0, "cleanup_adapter_exception"
            )

    def _validate_request(
        self, state: PointerSourceState, request: VirtualInjectionRequest
    ) -> tuple[InjectionFailure | None, str]:
        if not self.policy.enabled or not self.host.policy.enabled:
            return InjectionFailure.DISABLED, "os_injection_disabled"
        if request.provenance.origin in _UNTRUSTED_ORIGINS:
            return InjectionFailure.INVALID_REQUEST, "untrusted_origin"
        if not request.semantic_paths_exhausted or not request.native_pointer_required:
            return InjectionFailure.INVALID_REQUEST, "semantic_fallback_not_justified"
        if state.lease_id != request.lease_id or state.sequence + 1 != request.sequence:
            return InjectionFailure.INVALID_REQUEST, "source_sequence_or_lease_mismatch"
        if state.source_id.strip() == "" or request.provenance.action_id == state.last_action_id:
            return InjectionFailure.INVALID_REQUEST, "source_or_action_replay"
        return None, "request_valid"

    @staticmethod
    def _observation_matches(
        request: VirtualInjectionRequest, observation: InjectionObservation
    ) -> bool:
        return (
            observation.observation_id == request.observation_id
            and observation.window_token == request.binding.window_token
            and observation.frame == request.frame
            and observation.position == request.position
        )

    @staticmethod
    def _focus_matches(
        expected: SemanticWindowBinding, snapshot: SemanticFocusSnapshot
    ) -> bool:
        return snapshot.focused and snapshot.binding == expected

    def _receipt(
        self,
        state: PointerSourceState,
        request: VirtualInjectionRequest,
        outcome: NativeInjectionOutcome,
        failure: InjectionFailure | None,
        reason: str,
        requested: bool,
        uncertain: bool,
        verification: InjectionVerification | None,
        cleanup: NativeInjectionOutcome | None = None,
        focus_before: SemanticFocusSnapshot | None = None,
        focus_after: SemanticFocusSnapshot | None = None,
    ) -> VirtualInjectionReceipt:
        if failure is not None and cleanup is None and self.host.held_buttons:
            cleanup = self._safe_cleanup()
        applied = verification.applied if verification is not None else (
            None if uncertain else (outcome.complete if requested else False)
        )
        verified = verification.verified if verification is not None else False
        receipt = VirtualInjectionReceipt(
            receipt_id=uuid4().hex,
            source_id=state.source_id,
            session_id=state.session_id,
            lease_id=request.lease_id,
            sequence=request.sequence,
            provenance=request.provenance,
            transition=request.transition,
            button=request.button,
            dispatch_kind=PointerDispatchKind.OS_INPUT_INJECTION,
            window_token=request.binding.window_token,
            observation_id=request.observation_id,
            frame=request.frame,
            focus_before=focus_before,
            focus_after=focus_after,
            virtual_position=request.position,
            os_position=verification.os_position if verification is not None else None,
            requested=requested,
            applied=applied,
            verified=verified,
            uncertain=uncertain or applied is None,
            safe_to_retry=False,
            inserted_count=outcome.inserted_count,
            requested_count=outcome.requested_count,
            failure=failure,
            reason=reason,
            cleanup_attempted=cleanup is not None,
            cleanup_complete=cleanup is not None and cleanup.complete,
            timestamp=self.now(),
        )
        try:
            stamp = self.audit.append_injection_receipt(receipt)
        except Exception:
            return receipt
        return replace(
            receipt,
            audit_previous_hash=stamp.previous_hash,
            audit_hash=stamp.audit_hash,
            audit_recorded=True,
        )


__all__ = [
    "ClassifiedHumanActivityAdapter",
    "ControlSessionInjectionAdapter",
    "DpiFrameEvidence",
    "InjectionAuthorization",
    "InjectionAuditPort",
    "InjectionFailure",
    "InjectionObservation",
    "InjectionPermit",
    "InjectionPolicy",
    "InjectionVerification",
    "InputSecurityEvidence",
    "NativeInjectionOutcome",
    "NativeMousePacket",
    "PreclickEvidence",
    "VirtualInjectionFallback",
    "VirtualInjectionReceipt",
    "VirtualInjectionRequest",
    "WindowProbePreclickAdapter",
    "WindowsInputSecurityProbe",
    "WindowsSendInputHostAdapter",
    "injection_request_fingerprint",
]
