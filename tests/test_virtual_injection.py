from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

import open_compute.virtual_injection as injection_module
from open_compute.human_activity import ActivityAssessment, InputProvenance
from open_compute.virtual_injection import (
    DpiFrameEvidence,
    InjectionAuthorization,
    InjectionFailure,
    InjectionObservation,
    InjectionPermit,
    InjectionPolicy,
    InjectionVerification,
    InputSecurityEvidence,
    PreclickEvidence,
    VirtualInjectionFallback,
    VirtualInjectionRequest,
    WindowsSendInputHostAdapter,
)
from open_compute.virtual_pointer import (
    PointerButton,
    PointerCoordinateFrame,
    PointerDispatchKind,
    PointerFrameKind,
    PointerOrigin,
    PointerAuditStamp,
    PointerPhase,
    PointerPosition,
    PointerProvenance,
    PointerSourceState,
    PointerTransition,
)
from open_compute.virtual_target import SemanticFocusSnapshot, SemanticWindowBinding


def _title_hash(title: str) -> str:
    normalized = " ".join(title.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def frame(generation: int = 7) -> PointerCoordinateFrame:
    return PointerCoordinateFrame(
        frame_id="desktop-frame",
        kind=PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX,
        left=-1920,
        top=-200,
        width=4480,
        height=1640,
        generation=generation,
        context_id="desktop-context",
    )


def binding(current_frame: PointerCoordinateFrame | None = None) -> SemanticWindowBinding:
    return SemanticWindowBinding(
        window_token="window-token",
        hwnd=100,
        pid=200,
        process_token="process-token",
        title_sha256=_title_hash("Bound Window"),
        context_id="desktop-context",
        frame=current_frame or frame(),
    )


def provenance(
    *, origin: PointerOrigin = PointerOrigin.USER, action_id: str = "action-1"
) -> PointerProvenance:
    return PointerProvenance(
        actor="model-source",
        origin=origin,
        channel="explicit-user-channel",
        instruction_id="instruction-1",
        action_id=action_id,
        dispatch_kind=PointerDispatchKind.OS_INPUT_INJECTION,
        observation_id="observation-1",
        window_token="window-token",
        target_id="target-1",
    )


def request(
    *,
    transition: PointerTransition = PointerTransition.MOVE,
    button: PointerButton | None = None,
    origin: PointerOrigin = PointerOrigin.USER,
) -> VirtualInjectionRequest:
    current_frame = frame()
    return VirtualInjectionRequest(
        sequence=2,
        lease_id="lease-1",
        provenance=provenance(origin=origin),
        transition=transition,
        position=PointerPosition(-1000, 500),
        frame=current_frame,
        binding=binding(current_frame),
        observation_id="observation-1",
        button=button,
        semantic_paths_exhausted=True,
        native_pointer_required=True,
    )


def state() -> PointerSourceState:
    return PointerSourceState(
        source_id="source-1",
        pointer_id="pointer-1",
        session_id="session-1",
        lease_id="lease-1",
        sequence=1,
        phase=PointerPhase.ARMED,
        position=PointerPosition(-1000, 500),
        frame=frame(),
    )


class Session:
    allowed: bool | None = True
    paused = False
    error = False
    pause_error = False

    def authorize_os_injection(self, *, state, request):
        if self.error:
            raise RuntimeError("session adapter failed")
        return InjectionAuthorization(self.allowed, request.lease_id, "lease_result")

    def human_priority_pause(self, reason):
        if self.pause_error:
            raise RuntimeError("pause adapter failed")
        self.paused = True


class Activity:
    assessment = ActivityAssessment(False, InputProvenance.UNKNOWN, 5000, "unknown")
    error = False

    def assess_activity(self):
        if self.error:
            raise RuntimeError("activity adapter failed")
        return self.assessment


class EStop:
    triggered = False
    error = False

    def is_triggered(self):
        if self.error:
            raise RuntimeError("E-stop adapter failed")
        return self.triggered


class Observations:
    used = False
    mismatch = False
    error = False

    def claim_observation(self, current_request):
        if self.error:
            raise RuntimeError("observation adapter failed")
        if self.used:
            raise PermissionError("one-shot observation already consumed")
        self.used = True
        return InjectionObservation(
            current_request.observation_id,
            "different-window" if self.mismatch else current_request.binding.window_token,
            current_request.frame,
            current_request.position,
        )


class Focus:
    calls = 0
    fail_at: int | None = None
    raise_at: int | None = None

    def current_focus(self, expected):
        self.calls += 1
        if self.calls == self.raise_at:
            raise RuntimeError("focus adapter failed")
        return SemanticFocusSnapshot(expected, self.calls != self.fail_at)


class Dpi:
    valid = True
    error = False

    def current_dpi_frame(self, expected):
        if self.error:
            raise RuntimeError("DPI adapter failed")
        return DpiFrameEvidence(
            True if self.valid else None,
            expected if self.valid else replace(expected, generation=expected.generation + 1),
            self.valid,
            "dpi_frame_current" if self.valid else "monitor_generation_changed",
        )


class Security:
    desktop: bool | None = True
    uipi: bool | None = True
    error = False

    def inspect_input_security(self, expected):
        if self.error:
            raise RuntimeError("security adapter failed")
        return InputSecurityEvidence(self.desktop, self.uipi, "security_result")


class Preclick:
    valid = True
    error = False

    def verify_preclick(self, current_request):
        if self.error:
            raise RuntimeError("preclick adapter failed")
        return PreclickEvidence(
            self.valid,
            current_request.binding.window_token,
            (-1000, 500),
            "preclick_verified" if self.valid else "preclick_mismatch",
        )


class Verify:
    result = InjectionVerification(True, True, None, "post_state_verified")
    error = False

    def verify_injection(self, current_request, outcome):
        if self.error:
            raise RuntimeError("verification adapter failed")
        return self.result


class Audit:
    receipts = []

    def __init__(self):
        self.receipts = []

    def append_injection_receipt(self, receipt):
        self.receipts.append(receipt)
        return PointerAuditStamp("0" * 64, "1" * 64)


def core(*, enabled: bool = True, holds: bool = False):
    dependencies = {
        "session": Session(),
        "human_activity": Activity(),
        "emergency_stop": EStop(),
        "observations": Observations(),
        "focus": Focus(),
        "dpi_frame": Dpi(),
        "security": Security(),
        "preclick": Preclick(),
        "verifier": Verify(),
        "audit": Audit(),
    }
    policy = InjectionPolicy(enabled=enabled, allow_press_holds=holds)
    fallback = VirtualInjectionFallback(
        policy=policy,
        host=WindowsSendInputHostAdapter(policy),
        **dependencies,
    )
    return fallback, dependencies


def test_default_policy_and_host_are_disabled(monkeypatch):
    native_calls = []
    monkeypatch.setattr(injection_module, "_native_send_input", native_calls.append)
    fallback, _ = core(enabled=False)

    receipt = fallback.dispatch(state(), request())

    assert receipt.failure is InjectionFailure.DISABLED
    assert receipt.requested is False
    assert native_calls == []


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        ("lease", InjectionFailure.LEASE),
        ("human", InjectionFailure.HUMAN_PRIORITY),
        ("estop", InjectionFailure.EMERGENCY_STOP),
        ("observation", InjectionFailure.OBSERVATION),
        ("focus", InjectionFailure.FOCUS),
        ("dpi", InjectionFailure.DPI_FRAME),
        ("secure_desktop", InjectionFailure.SECURE_DESKTOP),
        ("uipi", InjectionFailure.UIPI),
        ("preclick", InjectionFailure.PRECLICK),
    ],
)
def test_no_native_call_when_any_gate_fails(monkeypatch, gate, expected):
    native_calls = []
    monkeypatch.setattr(injection_module, "_native_send_input", native_calls.append)
    fallback, deps = core()
    if gate == "lease":
        deps["session"].allowed = False
    elif gate == "human":
        deps["human_activity"].assessment = ActivityAssessment(
            True, InputProvenance.HUMAN, 10, "pointer"
        )
    elif gate == "estop":
        deps["emergency_stop"].triggered = True
    elif gate == "observation":
        deps["observations"].mismatch = True
    elif gate == "focus":
        deps["focus"].fail_at = 1
    elif gate == "dpi":
        deps["dpi_frame"].valid = False
    elif gate == "secure_desktop":
        deps["security"].desktop = False
    elif gate == "uipi":
        deps["security"].uipi = False
    elif gate == "preclick":
        deps["preclick"].valid = False

    receipt = fallback.dispatch(state(), request())

    assert receipt.failure is expected
    assert native_calls == []
    if gate == "human":
        assert deps["session"].paused is True


@pytest.mark.parametrize("origin", [PointerOrigin.SCREEN, PointerOrigin.UNKNOWN])
def test_untrusted_origin_never_authorizes_or_injects(monkeypatch, origin):
    native_calls = []
    monkeypatch.setattr(injection_module, "_native_send_input", native_calls.append)
    fallback, _ = core()

    receipt = fallback.dispatch(state(), request(origin=origin))

    assert receipt.failure is InjectionFailure.INVALID_REQUEST
    assert native_calls == []


def test_semantic_exhaustion_and_native_need_are_both_required(monkeypatch):
    native_calls = []
    monkeypatch.setattr(injection_module, "_native_send_input", native_calls.append)
    fallback, _ = core()

    first = fallback.dispatch(state(), replace(request(), semantic_paths_exhausted=False))
    second = fallback.dispatch(state(), replace(request(), native_pointer_required=False))

    assert first.failure is InjectionFailure.INVALID_REQUEST
    assert second.failure is InjectionFailure.INVALID_REQUEST
    assert native_calls == []


def test_all_gates_issue_one_shot_permit_and_call_native_seam(monkeypatch):
    packets_seen = []

    def fake_send_input(packets):
        packets_seen.append(tuple(packets))
        return len(packets)

    monkeypatch.setattr(injection_module, "_native_send_input", fake_send_input)
    fallback, _ = core()

    receipt = fallback.dispatch(state(), request())

    assert receipt.failure is None
    assert receipt.requested is True
    assert receipt.applied is True
    assert receipt.verified is True
    assert receipt.audit_recorded is True
    assert receipt.virtual_position == PointerPosition(-1000, 500)
    assert receipt.os_position is None
    assert len(packets_seen) == 1
    packet = packets_seen[0][0]
    assert 0 <= packet.dx <= 65535
    assert 0 <= packet.dy <= 65535
    assert packet.flags & injection_module.MOUSEEVENTF_VIRTUALDESK
    assert packet.flags & injection_module.MOUSEEVENTF_ABSOLUTE


def test_host_rejects_missing_gate_and_consumed_permit_before_native(monkeypatch):
    native_calls = []

    def fake_send_input(packets):
        native_calls.append(tuple(packets))
        return len(packets)

    monkeypatch.setattr(injection_module, "_native_send_input", fake_send_input)
    current = request()
    host = WindowsSendInputHostAdapter(InjectionPolicy(enabled=True))
    permit = InjectionPermit(
        permit_id="permit-1",
        action_id=current.provenance.action_id,
        lease_id=current.lease_id,
        observation_id=current.observation_id,
        window_token=current.binding.window_token,
        frame_id=current.frame.frame_id,
        frame_generation=current.frame.generation,
        request_fingerprint=injection_module.injection_request_fingerprint(current),
        gate_names=frozenset({"lease"}),
        one_shot=True,
    )
    assert host.inject(current, permit).reason == "permit_invalid_or_consumed"
    assert native_calls == []

    valid = replace(permit, permit_id="permit-2", gate_names=injection_module._REQUIRED_GATES)
    assert host.inject(current, valid).complete
    assert host.inject(current, valid).reason == "permit_invalid_or_consumed"
    assert len(native_calls) == 1


def test_permit_fingerprint_rejects_every_native_effect_mutation(monkeypatch):
    native_calls = []

    def fake_send_input(packets):
        native_calls.append(tuple(packets))
        return len(packets)

    monkeypatch.setattr(injection_module, "_native_send_input", fake_send_input)
    current = request()
    host = WindowsSendInputHostAdapter(
        InjectionPolicy(enabled=True, allow_press_holds=True)
    )
    permit = InjectionPermit(
        permit_id="permit-exact-request",
        action_id=current.provenance.action_id,
        lease_id=current.lease_id,
        observation_id=current.observation_id,
        window_token=current.binding.window_token,
        frame_id=current.frame.frame_id,
        frame_generation=current.frame.generation,
        request_fingerprint=injection_module.injection_request_fingerprint(current),
        gate_names=injection_module._REQUIRED_GATES,
        one_shot=True,
    )
    changed_frame = replace(current.frame, left=-1919)
    mutations = [
        replace(current, position=PointerPosition(-999, 500)),
        replace(current, transition=PointerTransition.PRESS, button=PointerButton.LEFT),
        replace(current, binding=replace(current.binding, hwnd=101)),
        replace(current, binding=replace(current.binding, pid=201)),
        replace(current, binding=replace(current.binding, process_token="other-process")),
        replace(current, binding=replace(current.binding, title_sha256="2" * 64)),
        replace(
            current,
            provenance=replace(current.provenance, target_id="other-target"),
        ),
        replace(current, frame=changed_frame, binding=replace(current.binding, frame=changed_frame)),
    ]

    for mutated in mutations:
        assert host.inject(mutated, permit).reason == "permit_invalid_or_consumed"

    assert native_calls == []
    assert host.inject(current, permit).complete
    assert len(native_calls) == 1


def test_partial_sendinput_is_uncertain_fail_closed(monkeypatch):
    monkeypatch.setattr(injection_module, "_native_send_input", lambda packets: 1)
    fallback, _ = core(holds=True)
    current = request(transition=PointerTransition.PRESS, button=PointerButton.LEFT)

    receipt = fallback.dispatch(state(), current)

    assert receipt.failure is InjectionFailure.PARTIAL
    assert receipt.uncertain is True
    assert receipt.safe_to_retry is False
    assert receipt.inserted_count == 1
    assert receipt.requested_count == 2


def test_press_hold_is_released_on_abort_without_moving_pointer(monkeypatch):
    batches = []

    def fake_send_input(packets):
        batches.append(tuple(packets))
        return len(packets)

    monkeypatch.setattr(injection_module, "_native_send_input", fake_send_input)
    fallback, _ = core(holds=True)
    current = request(transition=PointerTransition.PRESS, button=PointerButton.LEFT)
    assert fallback.dispatch(state(), current).applied is True
    assert fallback.host.held_buttons == frozenset({PointerButton.LEFT})

    cleanup = fallback.abort()

    assert cleanup.complete
    assert fallback.host.held_buttons == frozenset()
    assert len(batches) == 2
    assert batches[1][0].flags == injection_module.MOUSEEVENTF_LEFTUP


def test_focus_change_after_native_is_uncertain_and_cleanup_runs(monkeypatch):
    monkeypatch.setattr(injection_module, "_native_send_input", lambda packets: len(packets))
    fallback, deps = core(holds=True)
    deps["focus"].fail_at = 3
    current = request(transition=PointerTransition.PRESS, button=PointerButton.LEFT)

    receipt = fallback.dispatch(state(), current)

    assert receipt.failure is InjectionFailure.POST_FOCUS
    assert receipt.uncertain is True
    assert receipt.cleanup_attempted is True
    assert receipt.cleanup_complete is True
    assert fallback.host.held_buttons == frozenset()


@pytest.mark.parametrize(
    "adapter",
    [
        "session",
        "human_activity",
        "emergency_stop",
        "observations",
        "focus",
        "dpi_frame",
        "security",
        "preclick",
    ],
)
def test_pre_native_adapter_exception_returns_receipt_without_native_call(
    monkeypatch, adapter
):
    calls = []
    monkeypatch.setattr(injection_module, "_native_send_input", calls.append)
    fallback, deps = core()
    if adapter == "focus":
        deps[adapter].raise_at = 1
    else:
        deps[adapter].error = True

    receipt = fallback.dispatch(state(), request())

    assert receipt.requested is False
    assert receipt.applied is False
    assert receipt.verified is False
    assert receipt.failure in {
        InjectionFailure.ADAPTER_ERROR,
        InjectionFailure.OBSERVATION,
    }
    assert "exception" in receipt.reason or receipt.reason == "observation_claim_failed"
    assert calls == []


def test_human_pause_adapter_exception_still_blocks_native_call(monkeypatch):
    calls = []
    monkeypatch.setattr(injection_module, "_native_send_input", calls.append)
    fallback, deps = core()
    deps["human_activity"].assessment = ActivityAssessment(
        True, InputProvenance.HUMAN, 10, "pointer"
    )
    deps["session"].pause_error = True

    receipt = fallback.dispatch(state(), request())

    assert receipt.failure is InjectionFailure.ADAPTER_ERROR
    assert receipt.reason == "human_priority_pause_exception"
    assert calls == []


@pytest.mark.parametrize(
    ("post_adapter", "expected_failure", "expected_reason"),
    [
        ("focus", InjectionFailure.POST_FOCUS, "post_focus_adapter_exception"),
        ("verifier", InjectionFailure.VERIFY, "verification_adapter_exception"),
    ],
)
def test_post_native_adapter_exception_is_uncertain_and_releases_hold(
    monkeypatch, post_adapter, expected_failure, expected_reason
):
    batches = []

    def fake_send_input(packets):
        batches.append(tuple(packets))
        return len(packets)

    monkeypatch.setattr(injection_module, "_native_send_input", fake_send_input)
    fallback, deps = core(holds=True)
    if post_adapter == "focus":
        deps["focus"].raise_at = 3
    else:
        deps["verifier"].error = True
    current = request(transition=PointerTransition.PRESS, button=PointerButton.LEFT)

    receipt = fallback.dispatch(state(), current)

    assert receipt.failure is expected_failure
    assert receipt.reason == expected_reason
    assert receipt.requested is True
    assert receipt.applied is None
    assert receipt.uncertain is True
    assert receipt.safe_to_retry is False
    assert receipt.cleanup_attempted is True
    assert receipt.cleanup_complete is True
    assert fallback.host.held_buttons == frozenset()
    assert len(batches) == 2
    assert batches[1][0].flags == injection_module.MOUSEEVENTF_LEFTUP


def test_one_shot_observation_blocks_replay_before_second_native_call(monkeypatch):
    calls = []

    def fake_send_input(packets):
        calls.append(tuple(packets))
        return len(packets)

    monkeypatch.setattr(injection_module, "_native_send_input", fake_send_input)
    fallback, _ = core()

    assert fallback.dispatch(state(), request()).failure is None
    replay = fallback.dispatch(state(), request())

    assert replay.failure is InjectionFailure.OBSERVATION
    assert len(calls) == 1


def test_module_has_no_physical_cursor_read_or_legacy_input_api():
    source = injection_module.__file__
    text = open(source, encoding="utf-8").read()
    assert "GetCursorPos" not in text
    assert "SetCursorPos" not in text
    assert ".mouse_event(" not in text
    assert "keybd_event" not in text
