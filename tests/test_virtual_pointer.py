from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from open_compute.virtual_pointer import (
    PointerAuditStamp,
    PointerAuthorization,
    PointerButton,
    PointerCoordinateFrame,
    PointerDispatchKind,
    PointerFrameKind,
    PointerOrigin,
    PointerPhase,
    PointerPosition,
    PointerProvenance,
    PointerSourceState,
    PointerTransition,
    PointerTransitionRequest,
    VirtualPointerCore,
)
import open_compute.virtual_pointer as virtual_pointer_module


NOW = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
ZERO_HASH = "0" * 64


class FakeSession:
    def __init__(
        self,
        decision: PointerAuthorization | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.decision = decision or PointerAuthorization(True, "lease-1", "allowed")
        self.fail = fail
        self.calls: list[PointerTransitionRequest] = []

    def authorize_pointer_transition(self, *, state, request):
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("session unavailable")
        return self.decision


class FakeOwnership:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.published: list[PointerSourceState] = []
        self.cleared: list[PointerSourceState] = []

    def publish_pointer(self, *, state, request) -> None:
        if self.fail:
            raise RuntimeError("ownership unavailable")
        self.published.append(state)

    def clear_pointer(self, *, state, request) -> None:
        if self.fail:
            raise RuntimeError("ownership unavailable")
        self.cleared.append(state)


class HashAudit:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.receipts = []
        self.last_hash = ZERO_HASH

    def append_pointer_receipt(self, receipt):
        if self.fail:
            raise RuntimeError("audit unavailable")
        payload = json.dumps(
            receipt.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        previous = self.last_hash
        self.last_hash = sha256(previous.encode("ascii") + payload).hexdigest()
        self.receipts.append(receipt)
        return PointerAuditStamp(previous, self.last_hash)


def _frame(
    *,
    frame_id: str = "desktop-frame-1",
    generation: int = 1,
    left: float = -1920,
    top: float = 0,
    width: float = 3840,
    height: float = 1080,
) -> PointerCoordinateFrame:
    return PointerCoordinateFrame(
        frame_id=frame_id,
        kind=PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX,
        left=left,
        top=top,
        width=width,
        height=height,
        generation=generation,
        scale_x=1.25,
        scale_y=1.25,
    )


def _provenance(
    action_id: str,
    *,
    origin: PointerOrigin = PointerOrigin.AGENT,
    target_id: str | None = "target-1",
    dispatch_kind: PointerDispatchKind = PointerDispatchKind.OVERLAY_PREVIEW,
) -> PointerProvenance:
    return PointerProvenance(
        actor="agent-a",
        origin=origin,
        channel="headless-core",
        instruction_id="instruction-1",
        action_id=action_id,
        dispatch_kind=dispatch_kind,
        observation_id="observation-1",
        window_token="window-token-1",
        target_id=target_id,
    )


def test_phase1_pointer_transition_rejects_semantic_dispatch_kind() -> None:
    core, session, ownership, _ = _core()
    frame = _frame()
    request = PointerTransitionRequest(
        sequence=1,
        transition=PointerTransition.MOVE,
        provenance=_provenance(
            "semantic-through-pointer-core",
            dispatch_kind=PointerDispatchKind.BROWSER_SEMANTIC,
        ),
        position=PointerPosition(-100, 400),
        frame=frame,
    )

    receipt = core.apply(request)

    assert receipt.applied is False
    assert receipt.reason == "pointer_transition_requires_overlay_preview"
    assert session.calls == []
    assert ownership.published == []


def _request(
    sequence: int,
    transition: PointerTransition,
    action_id: str,
    *,
    origin: PointerOrigin = PointerOrigin.AGENT,
    position: PointerPosition | None = None,
    frame: PointerCoordinateFrame | None = None,
    button: PointerButton | None = None,
) -> PointerTransitionRequest:
    return PointerTransitionRequest(
        sequence=sequence,
        transition=transition,
        provenance=_provenance(action_id, origin=origin),
        position=position,
        frame=frame,
        button=button,
    )


def _core(
    *,
    session: FakeSession | None = None,
    ownership: FakeOwnership | None = None,
    audit: HashAudit | None = None,
):
    session = session or FakeSession()
    ownership = ownership or FakeOwnership()
    audit = audit or HashAudit()
    counter = iter(range(1, 100))
    core = VirtualPointerCore(
        state=PointerSourceState(
            source_id="llm-pointer-1",
            pointer_id="pointer-1",
            session_id="session-1",
            lease_id="lease-1",
        ),
        session=session,
        ownership=ownership,
        audit=audit,
        now=lambda: NOW,
        receipt_id=lambda: f"receipt-{next(counter)}",
    )
    return core, session, ownership, audit


def _move(sequence: int, action_id: str, frame=None, position=None):
    frame = frame or _frame()
    position = position or PointerPosition(-100, 400)
    return _request(
        sequence,
        PointerTransition.MOVE,
        action_id,
        position=position,
        frame=frame,
    )


def test_move_updates_only_virtual_state_and_emits_attributed_receipt() -> None:
    core, session, ownership, audit = _core()

    receipt = core.apply(_move(1, "action-move-1"))

    assert core.state.position == PointerPosition(-100, 400)
    assert core.state.frame == _frame()
    assert core.state.phase is PointerPhase.PREVIEW
    assert receipt.applied is True
    assert receipt.verified is True
    assert receipt.origin is PointerOrigin.AGENT
    assert receipt.actor == "agent-a"
    assert receipt.channel == "headless-core"
    assert receipt.instruction_id == "instruction-1"
    assert receipt.action_id == "action-move-1"
    assert receipt.audit_recorded is True
    assert receipt.audit_previous_hash == ZERO_HASH
    assert len(session.calls) == 1
    assert ownership.published == [core.state]
    assert len(audit.receipts) == 1
    json.dumps(receipt.to_dict())


def test_press_and_release_follow_webdriver_button_state_semantics() -> None:
    core, _, _, _ = _core()
    frame = _frame()
    core.apply(_move(1, "move-1", frame=frame))

    pressed = core.apply(
        _request(2, PointerTransition.PRESS, "press-1", frame=frame, button="left")
    )
    repeated_press = core.apply(
        _request(3, PointerTransition.PRESS, "press-2", frame=frame, button="left")
    )
    released = core.apply(
        _request(4, PointerTransition.RELEASE, "release-1", frame=frame, button="left")
    )
    repeated_release = core.apply(
        _request(5, PointerTransition.RELEASE, "release-2", frame=frame, button="left")
    )

    assert pressed.reason == "button_pressed"
    assert pressed.buttons_after == (PointerButton.LEFT,)
    assert repeated_press.applied is True
    assert repeated_press.reason == "button_already_pressed"
    assert released.reason == "button_released"
    assert released.phase_after is PointerPhase.PREVIEW
    assert repeated_release.applied is True
    assert repeated_release.reason == "button_not_pressed"
    assert core.state.pressed_buttons == frozenset()


def test_replay_and_out_of_order_requests_fail_closed_without_advancing_state() -> None:
    core, session, ownership, audit = _core()
    request = _move(1, "move-1")
    assert core.apply(request).applied is True

    replay = core.apply(request)
    gap = core.apply(_move(3, "move-gap"))
    recovered_order = core.apply(_move(2, "move-2"))

    assert replay.applied is False
    assert replay.reason == "action_replay"
    assert gap.applied is False
    assert gap.reason == "sequence_out_of_order"
    assert recovered_order.applied is True
    assert core.state.sequence == 2
    assert len(session.calls) == 2
    assert len(ownership.published) == 2
    assert len(audit.receipts) == 4


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"width": 0}, "width and height"),
        ({"height": float("nan")}, "finite"),
        ({"generation": -1}, "generation"),
        ({"scale_x": 0}, "scales"),
    ],
)
def test_invalid_coordinate_frames_are_rejected(overrides, message) -> None:
    values = {
        "frame_id": "frame-1",
        "kind": PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX,
        "left": 0,
        "top": 0,
        "width": 100,
        "height": 100,
        "generation": 1,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        PointerCoordinateFrame(**values)


def test_outside_and_stale_frames_are_rejected_without_ownership_update() -> None:
    core, session, ownership, audit = _core()
    frame = _frame()

    outside = core.apply(
        _move(1, "outside", frame=frame, position=PointerPosition(5000, 400))
    )
    assert outside.applied is False
    assert outside.reason == "position_outside_frame"

    assert core.apply(_move(1, "move-1", frame=frame)).applied is True
    stale = _frame(frame_id="desktop-frame-2", generation=2)
    stale_press = core.apply(
        _request(2, PointerTransition.PRESS, "press-stale", frame=stale, button="left")
    )
    assert stale_press.applied is False
    assert stale_press.reason == "stale_coordinate_frame"
    assert core.state.sequence == 1
    assert len(session.calls) == 1
    assert len(ownership.published) == 1
    assert len(audit.receipts) == 3


@pytest.mark.parametrize("origin", [PointerOrigin.SCREEN, PointerOrigin.UNKNOWN])
def test_screen_and_unknown_origins_never_authorize(origin) -> None:
    core, session, ownership, audit = _core()
    request = _request(
        1,
        PointerTransition.MOVE,
        f"move-{origin.value}",
        origin=origin,
        position=PointerPosition(10, 10),
        frame=_frame(left=0, width=100),
    )

    receipt = core.apply(request)

    assert receipt.applied is False
    assert receipt.reason == f"origin_{origin.value}_cannot_authorize"
    assert core.state.sequence == 0
    assert session.calls == []
    assert ownership.published == []
    assert len(audit.receipts) == 1


def test_uncertain_session_result_is_not_retryable_and_requires_cleanup() -> None:
    session = FakeSession(PointerAuthorization(None, "lease-1", "lease-store-timeout"))
    core, _, ownership, audit = _core(session=session)

    request = _move(1, "move-1")
    uncertain = core.apply(request)
    replay = core.apply(request)
    blocked = core.apply(_move(2, "move-2"))
    cleanup = core.apply(
        _request(
            2,
            PointerTransition.CLEANUP,
            "cleanup-1",
            origin=PointerOrigin.SYSTEM,
        )
    )

    assert uncertain.applied is None
    assert uncertain.safe_to_retry is False
    assert uncertain.phase_after is PointerPhase.UNCERTAIN
    assert core.state.phase is PointerPhase.PAUSED
    assert replay.reason == "action_replay"
    assert blocked.reason == "cleanup_required_after_uncertain"
    assert cleanup.applied is True
    assert cleanup.cleanup_result == "already_clear"
    assert len(session.calls) == 1
    assert ownership.published == []
    assert ownership.cleared == [core.state]
    assert len(audit.receipts) == 4


def test_ownership_failure_becomes_uncertain_after_internal_transition() -> None:
    ownership = FakeOwnership(fail=True)
    core, _, _, audit = _core(ownership=ownership)

    receipt = core.apply(_move(1, "move-1"))

    assert receipt.applied is None
    assert receipt.reason == "ownership_update_uncertain"
    assert receipt.phase_after is PointerPhase.UNCERTAIN
    assert receipt.position_after is None
    assert core.state.phase is PointerPhase.UNCERTAIN
    assert core.state.position is None
    assert len(audit.receipts) == 1


def test_cleanup_is_idempotent_and_always_clears_virtual_holds() -> None:
    core, session, ownership, _ = _core()
    frame = _frame()
    core.apply(_move(1, "move-1", frame=frame))
    core.apply(
        _request(2, PointerTransition.PRESS, "press-1", frame=frame, button="right")
    )

    first = core.apply(
        _request(3, PointerTransition.CLEANUP, "cleanup-1", origin=PointerOrigin.SYSTEM)
    )
    second = core.apply(
        _request(4, PointerTransition.CLEANUP, "cleanup-2", origin=PointerOrigin.SYSTEM)
    )

    assert first.cleanup_result == "cleared"
    assert second.cleanup_result == "already_clear"
    assert core.state.phase is PointerPhase.PAUSED
    assert core.state.position is None
    assert core.state.frame is None
    assert core.state.pressed_buttons == frozenset()
    assert len(session.calls) == 2
    assert len(ownership.cleared) == 2
    assert ownership.cleared[0].phase is PointerPhase.PAUSED
    assert ownership.cleared[1] == core.state


def test_user_pause_has_priority_blocks_moves_and_requires_user_resume() -> None:
    core, session, ownership, _ = _core()
    core.apply(_move(1, "move-1"))

    pause = core.apply(
        _request(2, PointerTransition.PAUSE, "pause-user", origin=PointerOrigin.USER)
    )
    blocked = core.apply(_move(3, "move-while-paused"))
    agent_resume = core.apply(
        _request(3, PointerTransition.RESUME, "resume-agent")
    )
    user_resume = core.apply(
        _request(3, PointerTransition.RESUME, "resume-user", origin=PointerOrigin.USER)
    )

    assert pause.user_interrupt is True
    assert pause.reason == "user_paused"
    assert pause.cleanup_result == "cleared"
    assert blocked.reason == "pointer_paused"
    assert agent_resume.reason == "resume_requires_user_origin"
    assert user_resume.applied is True
    assert core.state.phase is PointerPhase.IDLE
    assert len(session.calls) == 2
    assert ownership.cleared[0].phase is PointerPhase.PAUSED


def test_audit_failure_fails_closed_without_retry_claim() -> None:
    audit = HashAudit(fail=True)
    core, _, ownership, _ = _core(audit=audit)

    receipt = core.apply(_move(1, "move-1"))

    assert receipt.applied is None
    assert receipt.verified is False
    assert receipt.safe_to_retry is False
    assert receipt.reason == "audit_append_uncertain"
    assert receipt.audit_recorded is False
    assert core.state.phase is PointerPhase.UNCERTAIN
    assert len(ownership.published) == 1


def test_button_frame_cannot_change_while_pressed() -> None:
    core, _, _, _ = _core()
    frame = _frame()
    core.apply(_move(1, "move-1", frame=frame))
    core.apply(
        _request(2, PointerTransition.PRESS, "press-1", frame=frame, button="left")
    )

    changed = _frame(frame_id="desktop-frame-2", generation=2)
    receipt = core.apply(_move(3, "drag-frame-change", frame=changed))

    assert receipt.applied is False
    assert receipt.reason == "frame_change_while_pressed"
    assert core.state.frame == frame
    assert core.state.pressed_buttons == frozenset({PointerButton.LEFT})


def test_module_has_no_os_input_capture_or_renderer_dependency() -> None:
    tree = ast.parse(inspect.getsource(virtual_pointer_module))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots <= {
        "__future__",
        "dataclasses",
        "datetime",
        "enum",
        "math",
        "re",
        "typing",
        "uuid",
    }
    source = inspect.getsource(virtual_pointer_module)
    assert "GetCursorPos" not in source
    assert "SendInput" not in source
    assert "open_compute.drivers" not in source
    assert "open_compute.indicator" not in source
