"""Headless state machine for a virtual model pointer.

The virtual pointer is an independent input source.  This module never reads or
moves an operating-system cursor, injects input, captures a screen, or renders an
overlay.  Integrations are expressed through three small ports:

``PointerSessionPort``
    Authorizes state-changing transitions against a caller-owned session/lease.
``PointerOwnershipPort``
    Receives the new virtual state for a later renderer or ownership surface.
``PointerAuditPort``
    Appends a sanitized receipt and returns its hash-chain stamp.

Coordinates are explicit values inside an explicit coordinate frame.  Pointer
press/release behavior follows the WebDriver Actions state model: pressing an
already pressed button and releasing an unpressed button are successful
idempotent operations.  No such operation implies a native or physical click.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Protocol, runtime_checkable
from uuid import uuid4


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _identifier(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be a stable opaque identifier")
    return normalized


def _optional_identifier(name: str, value: str | None) -> str | None:
    return None if value is None else _identifier(name, value)


def _finite(name: str, value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _jsonable(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class PointerFrameKind(str, Enum):
    """Coordinate units and ownership boundary for a pointer position."""

    VIRTUAL_DESKTOP_PHYSICAL_PX = "virtual_desktop_physical_px"
    WINDOW_CLIENT_PHYSICAL_PX = "window_client_physical_px"
    BROWSER_VIEWPORT_CSS_PX = "browser_viewport_css_px"


class PointerButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class PointerPhase(str, Enum):
    IDLE = "idle"
    PREVIEW = "preview"
    ARMED = "armed"
    PRESSED = "pressed"
    PAUSED = "paused"
    ABORTED = "aborted"
    UNCERTAIN = "uncertain"


class PointerOrigin(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    SCREEN = "screen"
    UNKNOWN = "unknown"


class PointerDispatchKind(str, Enum):
    """Dispatch vocabulary; OS injection remains a separately gated fallback."""

    OVERLAY_PREVIEW = "overlay_preview"
    BROWSER_SEMANTIC = "browser_semantic"
    ACCESSIBILITY_SEMANTIC = "accessibility_semantic"
    OS_INPUT_INJECTION = "os_input_injection"


class PointerTransition(str, Enum):
    MOVE = "move"
    PRESS = "press"
    RELEASE = "release"
    PAUSE = "pause"
    RESUME = "resume"
    ABORT = "abort"
    CLEANUP = "cleanup"


@dataclass(frozen=True)
class PointerPosition:
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite("x", self.x))
        object.__setattr__(self, "y", _finite("y", self.y))


@dataclass(frozen=True)
class PointerCoordinateFrame:
    """A versioned coordinate frame; no implicit desktop/window conversion."""

    frame_id: str
    kind: PointerFrameKind
    left: float
    top: float
    width: float
    height: float
    generation: int
    scale_x: float = 1.0
    scale_y: float = 1.0
    context_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_id", _identifier("frame_id", self.frame_id))
        object.__setattr__(self, "kind", PointerFrameKind(self.kind))
        object.__setattr__(self, "left", _finite("left", self.left))
        object.__setattr__(self, "top", _finite("top", self.top))
        object.__setattr__(self, "width", _finite("width", self.width))
        object.__setattr__(self, "height", _finite("height", self.height))
        object.__setattr__(self, "scale_x", _finite("scale_x", self.scale_x))
        object.__setattr__(self, "scale_y", _finite("scale_y", self.scale_y))
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame width and height must be greater than zero")
        if self.scale_x <= 0 or self.scale_y <= 0:
            raise ValueError("frame scales must be greater than zero")
        if isinstance(self.generation, bool) or int(self.generation) != self.generation:
            raise ValueError("frame generation must be a non-negative integer")
        if self.generation < 0:
            raise ValueError("frame generation must be a non-negative integer")
        object.__setattr__(self, "generation", int(self.generation))
        object.__setattr__(
            self,
            "context_id",
            _optional_identifier("context_id", self.context_id),
        )
        if (
            self.kind is not PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX
            and self.context_id is None
        ):
            raise ValueError("window and browser frames require context_id")

    def contains(self, position: PointerPosition) -> bool:
        return (
            self.left <= position.x < self.left + self.width
            and self.top <= position.y < self.top + self.height
        )


@dataclass(frozen=True)
class PointerProvenance:
    """Opaque attribution only; no free-form screen or user content."""

    actor: str
    origin: PointerOrigin
    channel: str
    instruction_id: str
    action_id: str
    dispatch_kind: PointerDispatchKind = PointerDispatchKind.OVERLAY_PREVIEW
    observation_id: str | None = None
    window_token: str | None = None
    target_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor", _identifier("actor", self.actor))
        object.__setattr__(self, "origin", PointerOrigin(self.origin))
        object.__setattr__(self, "channel", _identifier("channel", self.channel))
        object.__setattr__(
            self,
            "instruction_id",
            _identifier("instruction_id", self.instruction_id),
        )
        object.__setattr__(self, "action_id", _identifier("action_id", self.action_id))
        object.__setattr__(
            self,
            "dispatch_kind",
            PointerDispatchKind(self.dispatch_kind),
        )
        for name in ("observation_id", "window_token", "target_id"):
            object.__setattr__(
                self,
                name,
                _optional_identifier(name, getattr(self, name)),
            )


@dataclass(frozen=True)
class PointerTransitionRequest:
    sequence: int
    transition: PointerTransition
    provenance: PointerProvenance
    position: PointerPosition | None = None
    frame: PointerCoordinateFrame | None = None
    button: PointerButton | None = None

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or int(self.sequence) != self.sequence:
            raise ValueError("sequence must be a positive integer")
        if self.sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        object.__setattr__(self, "sequence", int(self.sequence))
        object.__setattr__(self, "transition", PointerTransition(self.transition))
        if self.button is not None:
            object.__setattr__(self, "button", PointerButton(self.button))


@dataclass(frozen=True)
class PointerSourceState:
    source_id: str
    pointer_id: str
    session_id: str
    lease_id: str
    sequence: int = 0
    phase: PointerPhase = PointerPhase.IDLE
    position: PointerPosition | None = None
    frame: PointerCoordinateFrame | None = None
    pressed_buttons: frozenset[PointerButton] = field(default_factory=frozenset)
    last_action_id: str | None = None
    last_verified_target_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("source_id", "pointer_id", "session_id", "lease_id"):
            object.__setattr__(self, name, _identifier(name, getattr(self, name)))
        if (
            isinstance(self.sequence, bool)
            or int(self.sequence) != self.sequence
            or self.sequence < 0
        ):
            raise ValueError("state sequence must be a non-negative integer")
        object.__setattr__(self, "sequence", int(self.sequence))
        object.__setattr__(self, "phase", PointerPhase(self.phase))
        object.__setattr__(
            self,
            "pressed_buttons",
            frozenset(PointerButton(button) for button in self.pressed_buttons),
        )
        object.__setattr__(
            self,
            "last_action_id",
            _optional_identifier("last_action_id", self.last_action_id),
        )
        object.__setattr__(
            self,
            "last_verified_target_id",
            _optional_identifier(
                "last_verified_target_id", self.last_verified_target_id
            ),
        )
        if (self.position is None) is not (self.frame is None):
            raise ValueError("position and frame must be present or absent together")
        if self.position is not None and not self.frame.contains(self.position):
            raise ValueError("state position is outside its coordinate frame")
        if self.pressed_buttons and self.position is None:
            raise ValueError("pressed buttons require a framed position")
        if self.pressed_buttons and self.phase is not PointerPhase.PRESSED:
            raise ValueError("pressed buttons require phase=pressed")
        if self.phase is PointerPhase.PRESSED and not self.pressed_buttons:
            raise ValueError("phase=pressed requires at least one pressed button")


@dataclass(frozen=True)
class PointerAuthorization:
    """``allowed=None`` means the session result itself is uncertain."""

    allowed: bool | None
    lease_id: str
    reason: str = "session-decision"

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, (bool, type(None))):
            raise ValueError("allowed must be true, false, or none")
        object.__setattr__(self, "lease_id", _identifier("lease_id", self.lease_id))
        object.__setattr__(self, "reason", _identifier("reason", self.reason))


@dataclass(frozen=True)
class PointerAuditStamp:
    previous_hash: str
    audit_hash: str

    def __post_init__(self) -> None:
        if not _HASH_RE.fullmatch(self.previous_hash):
            raise ValueError("previous_hash must be a lowercase SHA-256 hex digest")
        if not _HASH_RE.fullmatch(self.audit_hash):
            raise ValueError("audit_hash must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True)
class PointerReceipt:
    receipt_id: str
    timestamp: str
    source_id: str
    pointer_id: str
    session_id: str
    lease_id: str
    sequence: int
    actor: str
    origin: PointerOrigin
    channel: str
    instruction_id: str
    action_id: str
    dispatch_kind: PointerDispatchKind
    requested_transition: PointerTransition
    position_before: PointerPosition | None
    position_after: PointerPosition | None
    frame_before: PointerCoordinateFrame | None
    frame_after: PointerCoordinateFrame | None
    buttons_before: tuple[PointerButton, ...]
    buttons_after: tuple[PointerButton, ...]
    phase_before: PointerPhase
    phase_after: PointerPhase
    observation_id: str | None
    window_token: str | None
    target_id: str | None
    applied: bool | None
    verified: bool
    safe_to_retry: bool
    reason: str
    user_interrupt: bool
    cleanup_result: str | None
    audit_recorded: bool = False
    audit_previous_hash: str | None = None
    audit_hash: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable, content-sanitized receipt."""

        return _jsonable(asdict(self))


@runtime_checkable
class PointerSessionPort(Protocol):
    def authorize_pointer_transition(
        self,
        *,
        state: PointerSourceState,
        request: PointerTransitionRequest,
    ) -> PointerAuthorization: ...


@runtime_checkable
class PointerOwnershipPort(Protocol):
    def publish_pointer(
        self,
        *,
        state: PointerSourceState,
        request: PointerTransitionRequest,
    ) -> None: ...

    def clear_pointer(
        self,
        *,
        state: PointerSourceState,
        request: PointerTransitionRequest,
    ) -> None: ...


@runtime_checkable
class PointerAuditPort(Protocol):
    def append_pointer_receipt(self, receipt: PointerReceipt) -> PointerAuditStamp: ...


_STOP_TRANSITIONS = frozenset(
    {PointerTransition.PAUSE, PointerTransition.ABORT, PointerTransition.CLEANUP}
)
_ACTIVE_TRANSITIONS = frozenset(
    {
        PointerTransition.MOVE,
        PointerTransition.PRESS,
        PointerTransition.RELEASE,
        PointerTransition.RESUME,
    }
)
_UNTRUSTED_ORIGINS = frozenset({PointerOrigin.SCREEN, PointerOrigin.UNKNOWN})


class VirtualPointerCore:
    """Deterministic virtual pointer reducer with fail-closed injected ports."""

    def __init__(
        self,
        *,
        state: PointerSourceState,
        session: PointerSessionPort,
        ownership: PointerOwnershipPort,
        audit: PointerAuditPort,
        now: Callable[[], datetime] | None = None,
        receipt_id: Callable[[], str] | None = None,
    ) -> None:
        self._state = state
        self._session = session
        self._ownership = ownership
        self._audit = audit
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._receipt_id = receipt_id or (lambda: uuid4().hex)
        self._seen_action_ids: set[str] = set()

    @property
    def state(self) -> PointerSourceState:
        return self._state

    def apply(self, request: PointerTransitionRequest) -> PointerReceipt:
        before = self._state
        provenance = request.provenance

        if provenance.action_id in self._seen_action_ids:
            return self._receipt(
                before,
                before,
                request,
                applied=False,
                verified=False,
                reason="action_replay",
            )
        self._seen_action_ids.add(provenance.action_id)

        expected_sequence = before.sequence + 1
        if request.sequence < expected_sequence:
            return self._receipt(
                before,
                before,
                request,
                applied=False,
                verified=False,
                reason="sequence_replay",
            )
        if request.sequence > expected_sequence:
            return self._receipt(
                before,
                before,
                request,
                applied=False,
                verified=False,
                reason="sequence_out_of_order",
            )
        if provenance.origin in _UNTRUSTED_ORIGINS:
            return self._receipt(
                before,
                before,
                request,
                applied=False,
                verified=False,
                reason=f"origin_{provenance.origin.value}_cannot_authorize",
            )

        shape_error = self._validate_shape(before, request)
        if shape_error is not None:
            return self._receipt(
                before,
                before,
                request,
                applied=False,
                verified=False,
                reason=shape_error,
            )

        if request.transition in _ACTIVE_TRANSITIONS:
            authorization = self._authorize(before, request)
            if authorization is None:
                return self._uncertain(before, request, "session_port_failed")
            if authorization.allowed is None:
                return self._uncertain(
                    before,
                    request,
                    f"session_uncertain:{authorization.reason}",
                )
            if authorization.lease_id != before.lease_id:
                return self._receipt(
                    before,
                    before,
                    request,
                    applied=False,
                    verified=False,
                    reason="lease_mismatch",
                )
            if not authorization.allowed:
                return self._receipt(
                    before,
                    before,
                    request,
                    applied=False,
                    verified=False,
                    reason=f"session_denied:{authorization.reason}",
                )

        after, reason, cleanup_result = self._reduce(before, request)
        self._state = after
        try:
            if request.transition in _STOP_TRANSITIONS:
                self._ownership.clear_pointer(state=after, request=request)
            else:
                self._ownership.publish_pointer(state=after, request=request)
        except Exception:  # noqa: BLE001 - adapter failure becomes an uncertain receipt
            uncertain = self._safe_state(after, PointerPhase.UNCERTAIN)
            self._state = uncertain
            return self._receipt(
                before,
                uncertain,
                request,
                applied=None,
                verified=False,
                reason="ownership_update_uncertain",
                cleanup_result=cleanup_result,
            )

        return self._receipt(
            before,
            after,
            request,
            applied=True,
            verified=True,
            reason=reason,
            cleanup_result=cleanup_result,
        )

    def _authorize(
        self,
        state: PointerSourceState,
        request: PointerTransitionRequest,
    ) -> PointerAuthorization | None:
        try:
            decision = self._session.authorize_pointer_transition(
                state=state,
                request=request,
            )
        except Exception:  # noqa: BLE001 - fail closed at the port boundary
            return None
        if not isinstance(decision, PointerAuthorization):
            return None
        return decision

    @staticmethod
    def _validate_shape(
        state: PointerSourceState,
        request: PointerTransitionRequest,
    ) -> str | None:
        if request.provenance.dispatch_kind is not PointerDispatchKind.OVERLAY_PREVIEW:
            return "pointer_transition_requires_overlay_preview"
        transition = request.transition
        if state.phase is PointerPhase.UNCERTAIN and transition is not PointerTransition.CLEANUP:
            return "cleanup_required_after_uncertain"
        if state.phase is PointerPhase.ABORTED and transition is not PointerTransition.CLEANUP:
            return "cleanup_required_after_abort"
        if state.phase is PointerPhase.PAUSED and transition not in {
            PointerTransition.RESUME,
            PointerTransition.PAUSE,
            PointerTransition.ABORT,
            PointerTransition.CLEANUP,
        }:
            return "pointer_paused"
        if transition is PointerTransition.RESUME:
            if state.phase is not PointerPhase.PAUSED:
                return "resume_requires_paused_state"
            if request.provenance.origin is not PointerOrigin.USER:
                return "resume_requires_user_origin"

        if transition is PointerTransition.MOVE:
            if request.position is None or request.frame is None or request.button is not None:
                return "move_requires_position_and_frame_only"
            if not request.frame.contains(request.position):
                return "position_outside_frame"
            if state.pressed_buttons and request.frame != state.frame:
                return "frame_change_while_pressed"
            return None

        if transition in {PointerTransition.PRESS, PointerTransition.RELEASE}:
            if request.button is None or request.frame is None or request.position is not None:
                return "button_transition_requires_button_and_frame_only"
            if state.position is None or state.frame is None:
                return "button_transition_requires_position"
            if request.frame != state.frame:
                return "stale_coordinate_frame"
            return None

        if transition in {
            PointerTransition.PAUSE,
            PointerTransition.RESUME,
            PointerTransition.ABORT,
            PointerTransition.CLEANUP,
        }:
            if request.position is not None or request.frame is not None or request.button is not None:
                return "lifecycle_transition_cannot_carry_pointer_payload"
            return None
        return "unsupported_transition"

    @staticmethod
    def _reduce(
        state: PointerSourceState,
        request: PointerTransitionRequest,
    ) -> tuple[PointerSourceState, str, str | None]:
        common = {
            "sequence": request.sequence,
            "last_action_id": request.provenance.action_id,
        }
        transition = request.transition
        if transition is PointerTransition.MOVE:
            phase = PointerPhase.PRESSED if state.pressed_buttons else PointerPhase.PREVIEW
            return (
                replace(
                    state,
                    position=request.position,
                    frame=request.frame,
                    phase=phase,
                    **common,
                ),
                "pointer_moved",
                None,
            )
        if transition is PointerTransition.PRESS:
            buttons = set(state.pressed_buttons)
            already_pressed = request.button in buttons
            buttons.add(request.button)
            return (
                replace(
                    state,
                    pressed_buttons=frozenset(buttons),
                    phase=PointerPhase.PRESSED,
                    **common,
                ),
                "button_already_pressed" if already_pressed else "button_pressed",
                None,
            )
        if transition is PointerTransition.RELEASE:
            buttons = set(state.pressed_buttons)
            was_pressed = request.button in buttons
            buttons.discard(request.button)
            return (
                replace(
                    state,
                    pressed_buttons=frozenset(buttons),
                    phase=PointerPhase.PRESSED if buttons else PointerPhase.PREVIEW,
                    **common,
                ),
                "button_released" if was_pressed else "button_not_pressed",
                None,
            )
        if transition is PointerTransition.PAUSE:
            had_state = bool(state.pressed_buttons or state.position is not None)
            return (
                replace(
                    state,
                    phase=PointerPhase.PAUSED,
                    position=None,
                    frame=None,
                    pressed_buttons=frozenset(),
                    **common,
                ),
                "user_paused" if request.provenance.origin is PointerOrigin.USER else "paused",
                "cleared" if had_state else "already_clear",
            )
        if transition is PointerTransition.RESUME:
            return (
                replace(state, phase=PointerPhase.IDLE, **common),
                "user_resumed",
                None,
            )
        if transition is PointerTransition.ABORT:
            had_state = bool(state.pressed_buttons or state.position is not None)
            return (
                replace(
                    state,
                    phase=PointerPhase.ABORTED,
                    position=None,
                    frame=None,
                    pressed_buttons=frozenset(),
                    **common,
                ),
                "aborted",
                "cleared" if had_state else "already_clear",
            )
        had_state = bool(state.pressed_buttons or state.position is not None)
        return (
            replace(
                state,
                phase=PointerPhase.PAUSED,
                position=None,
                frame=None,
                pressed_buttons=frozenset(),
                **common,
            ),
            "cleanup_complete",
            "cleared" if had_state else "already_clear",
        )

    @staticmethod
    def _safe_state(
        state: PointerSourceState,
        phase: PointerPhase,
    ) -> PointerSourceState:
        return replace(
            state,
            phase=phase,
            position=None,
            frame=None,
            pressed_buttons=frozenset(),
        )

    def _uncertain(
        self,
        before: PointerSourceState,
        request: PointerTransitionRequest,
        reason: str,
    ) -> PointerReceipt:
        after = self._safe_state(
            replace(
                before,
                sequence=request.sequence,
                last_action_id=request.provenance.action_id,
            ),
            PointerPhase.UNCERTAIN,
        )
        self._state = after
        return self._receipt(
            before,
            after,
            request,
            applied=None,
            verified=False,
            reason=reason,
        )

    def _receipt(
        self,
        before: PointerSourceState,
        after: PointerSourceState,
        request: PointerTransitionRequest,
        *,
        applied: bool | None,
        verified: bool,
        reason: str,
        cleanup_result: str | None = None,
    ) -> PointerReceipt:
        provenance = request.provenance
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("receipt clock must return a timezone-aware datetime")
        provisional = PointerReceipt(
            receipt_id=_identifier("receipt_id", self._receipt_id()),
            timestamp=timestamp.astimezone(timezone.utc).isoformat(),
            source_id=before.source_id,
            pointer_id=before.pointer_id,
            session_id=before.session_id,
            lease_id=before.lease_id,
            sequence=request.sequence,
            actor=provenance.actor,
            origin=provenance.origin,
            channel=provenance.channel,
            instruction_id=provenance.instruction_id,
            action_id=provenance.action_id,
            dispatch_kind=provenance.dispatch_kind,
            requested_transition=request.transition,
            position_before=before.position,
            position_after=after.position,
            frame_before=before.frame,
            frame_after=after.frame,
            buttons_before=tuple(sorted(before.pressed_buttons, key=lambda item: item.value)),
            buttons_after=tuple(sorted(after.pressed_buttons, key=lambda item: item.value)),
            phase_before=before.phase,
            phase_after=after.phase,
            observation_id=provenance.observation_id,
            window_token=provenance.window_token,
            target_id=provenance.target_id,
            applied=applied,
            verified=verified,
            safe_to_retry=False,
            reason=_identifier("reason", reason),
            user_interrupt=(
                request.transition is PointerTransition.PAUSE
                and provenance.origin is PointerOrigin.USER
            ),
            cleanup_result=cleanup_result,
        )
        try:
            stamp = self._audit.append_pointer_receipt(provisional)
            if not isinstance(stamp, PointerAuditStamp):
                raise TypeError("audit port returned an invalid stamp")
        except Exception:  # noqa: BLE001 - caller receives fail-closed uncertainty
            if applied is not False:
                self._state = self._safe_state(after, PointerPhase.UNCERTAIN)
                provisional = replace(
                    provisional,
                    position_after=None,
                    frame_after=None,
                    buttons_after=(),
                    phase_after=PointerPhase.UNCERTAIN,
                    applied=None,
                    verified=False,
                    reason="audit_append_uncertain",
                )
            return provisional
        return replace(
            provisional,
            audit_recorded=True,
            audit_previous_hash=stamp.previous_hash,
            audit_hash=stamp.audit_hash,
        )


__all__ = [
    "PointerAuditPort",
    "PointerAuditStamp",
    "PointerAuthorization",
    "PointerButton",
    "PointerCoordinateFrame",
    "PointerDispatchKind",
    "PointerFrameKind",
    "PointerOrigin",
    "PointerOwnershipPort",
    "PointerPhase",
    "PointerPosition",
    "PointerProvenance",
    "PointerReceipt",
    "PointerSessionPort",
    "PointerSourceState",
    "PointerTransition",
    "PointerTransitionRequest",
    "VirtualPointerCore",
]
