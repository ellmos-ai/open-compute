from __future__ import annotations

import inspect
from datetime import datetime, timezone
from hashlib import sha256

import pytest

import open_compute.virtual_target as target_module
from open_compute.drivers.base import SemanticBrowserDriver
from open_compute.interaction import ObservationRegistry, describe_window
from open_compute.virtual_pointer import (
    PointerAuditStamp,
    PointerCoordinateFrame,
    PointerDispatchKind,
    PointerFrameKind,
    PointerOrigin,
    PointerPhase,
    PointerPosition,
    PointerProvenance,
    PointerSourceState,
)
from open_compute.virtual_target import (
    BrowserDriverSemanticAdapter,
    RegistryObservationSnapshot,
    RegistrySemanticObservationPort,
    SemanticActivationAuthorization,
    SemanticActivationRequest,
    SemanticDispatchOutcome,
    SemanticFocusSnapshot,
    SemanticObservation,
    SemanticResolutionStatus,
    SemanticResolvedTarget,
    SemanticTargetIdentity,
    SemanticTargetResolution,
    SemanticTargetState,
    SemanticVerification,
    SemanticWindowBinding,
    UiaTargeterSemanticAdapter,
    VirtualTargetActivationCore,
    pointer_frame_record,
)


NOW = datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc)
ZERO_HASH = "0" * 64
PRE_HASH = "1" * 64
POST_HASH = "2" * 64
TITLE_HASH = "3" * 64


def _frame(
    *,
    kind: PointerFrameKind = PointerFrameKind.BROWSER_VIEWPORT_CSS_PX,
    context_id: str = "context-1",
    generation: int = 1,
) -> PointerCoordinateFrame:
    return PointerCoordinateFrame(
        frame_id=f"frame-{generation}",
        kind=kind,
        left=0,
        top=0,
        width=1280,
        height=720,
        generation=generation,
        context_id=context_id,
    )


def _binding(frame: PointerCoordinateFrame | None = None) -> SemanticWindowBinding:
    return SemanticWindowBinding(
        window_token="window-token-1",
        hwnd=42,
        pid=7001,
        process_token="process-start-1",
        title_sha256=TITLE_HASH,
        context_id="context-1",
        frame=frame or _frame(),
    )


def _state(frame: PointerCoordinateFrame | None = None, **overrides) -> PointerSourceState:
    frame = frame or _frame()
    values = {
        "source_id": "llm-source-1",
        "pointer_id": "pointer-1",
        "session_id": "session-1",
        "lease_id": "lease-1",
        "sequence": 0,
        "phase": PointerPhase.PREVIEW,
        "position": PointerPosition(100, 100),
        "frame": frame,
    }
    values.update(overrides)
    return PointerSourceState(**values)


def _provenance(
    action_id: str = "action-1",
    *,
    origin: PointerOrigin = PointerOrigin.AGENT,
    dispatch_kind: PointerDispatchKind = PointerDispatchKind.BROWSER_SEMANTIC,
) -> PointerProvenance:
    return PointerProvenance(
        actor="agent-a",
        origin=origin,
        channel="headless-semantic",
        instruction_id="instruction-1",
        action_id=action_id,
        dispatch_kind=dispatch_kind,
        observation_id="observation-1",
        window_token="window-token-1",
        target_id="target-1",
    )


def _request(
    *,
    sequence: int = 1,
    provenance: PointerProvenance | None = None,
    binding: SemanticWindowBinding | None = None,
) -> SemanticActivationRequest:
    return SemanticActivationRequest(
        sequence=sequence,
        provenance=provenance or _provenance(),
        binding=binding or _binding(),
        target_query="Save:Button",
    )


def _observation(frame: PointerCoordinateFrame | None = None, **overrides) -> SemanticObservation:
    values = {
        "observation_id": "observation-1",
        "window_token": "window-token-1",
        "context_id": "context-1",
        "frame": frame or _frame(),
        "target_id": "target-1",
        "target_state_signature": PRE_HASH,
    }
    values.update(overrides)
    return SemanticObservation(**values)


class AllowSession:
    def __init__(self, allowed=True) -> None:
        self.allowed = allowed
        self.calls = []

    def authorize_semantic_activation(self, *, state, request):
        self.calls.append((state, request))
        return SemanticActivationAuthorization(self.allowed, state.lease_id, "allowed")


class FakeObservations:
    def __init__(self, observation: SemanticObservation | None = None) -> None:
        self.observation = observation or _observation()
        self.calls = []

    def claim_semantic_observation(self, request):
        self.calls.append(request)
        return self.observation


class FakeBinding:
    def __init__(self, *snapshots: SemanticFocusSnapshot) -> None:
        self.snapshots = list(snapshots)
        self.calls = []

    def current_focus(self, expected):
        self.calls.append(expected)
        return self.snapshots.pop(0) if self.snapshots else SemanticFocusSnapshot(expected, True)


class FakeAdapter:
    def __init__(
        self,
        kind: PointerDispatchKind,
        *,
        status: SemanticResolutionStatus = SemanticResolutionStatus.EXACT,
        applied: bool | None = True,
        verified: bool = True,
        target_id: str = "target-1",
        context_id: str = "context-1",
        pre_hash: str = PRE_HASH,
    ) -> None:
        self.dispatch_kind = kind
        self.status = status
        self.applied = applied
        self.verified = verified
        self.target = SemanticResolvedTarget(
            SemanticTargetIdentity(
                target_id=target_id,
                stable_id=f"stable-{kind.value}",
                role="Button",
                context_id=context_id,
                dispatch_kind=kind,
            ),
            SemanticTargetState(pre_hash),
            object(),
        )
        self.calls = []

    def resolve_exact(self, observation, request):
        self.calls.append("resolve")
        if self.status is SemanticResolutionStatus.EXACT:
            return SemanticTargetResolution(self.status, "exact-target", self.target)
        return SemanticTargetResolution(self.status, "not-applicable")

    def activate(self, target, request):
        self.calls.append("activate")
        return SemanticDispatchOutcome(self.applied, "semantic-result")

    def verify(self, target, request):
        self.calls.append("verify")
        return SemanticVerification(
            self.verified,
            SemanticTargetState(POST_HASH) if self.verified else None,
            "post-state-verified" if self.verified else "post-state-failed",
        )


class FakeSemanticBrowserDriver:
    def __init__(self, frame: PointerCoordinateFrame | None = None) -> None:
        self.frame = frame or _frame()
        self.calls = []
        self.context_overrides = {}
        self.resolve_overrides = {}
        self.activation_overrides = {}
        self.state_overrides = {}

    def _context(self):
        values = {
            "context_id": "context-1",
            "observation_id": "observation-1",
            "window_token": "window-token-1",
            "frame": pointer_frame_record(self.frame),
        }
        values.update(self.context_overrides)
        return values

    def semantic_context(self):
        self.calls.append("semantic_context")
        return self._context()

    def resolve_semantic_target(self, **kwargs):
        self.calls.append(("resolve_semantic_target", kwargs))
        values = {
            **self._context(),
            "exact": True,
            "unique": True,
            "target_id": "target-1",
            "stable_id": "dom-node-7",
            "role": "Button",
            "state_sha256": PRE_HASH,
            "target_handle": "engine-handle-7",
        }
        values.update(self.resolve_overrides)
        return values

    def activate_semantic_target(self, **kwargs):
        self.calls.append(("activate_semantic_target", kwargs))
        values = {
            "context_id": "context-1",
            "target_handle": "engine-handle-7",
            "action_id": "action-1",
            "applied": True,
            "reason": "dom-action-applied",
        }
        values.update(self.activation_overrides)
        return values

    def semantic_target_state(self, **kwargs):
        self.calls.append(("semantic_target_state", kwargs))
        values = {
            **self._context(),
            "target_id": "target-1",
            "stable_id": "dom-node-7",
            "target_handle": "engine-handle-7",
            "state_sha256": POST_HASH,
        }
        values.update(self.state_overrides)
        return values

    def goto(self, url):
        raise AssertionError("navigation is outside semantic activation")

    def execute(self, action):
        raise AssertionError("generic/coordinate BrowserDriver.execute is forbidden")

    def close(self):
        raise AssertionError("driver lifetime is outside semantic activation")


class HashAudit:
    def __init__(self) -> None:
        self.receipts = []
        self.last_hash = ZERO_HASH

    def append_semantic_receipt(self, receipt):
        previous = self.last_hash
        self.last_hash = sha256(
            previous.encode("ascii") + repr(receipt.to_dict()).encode("utf-8")
        ).hexdigest()
        self.receipts.append(receipt)
        return PointerAuditStamp(previous, self.last_hash)


def _core(
    *,
    state: PointerSourceState | None = None,
    session=None,
    observations=None,
    binding=None,
    browser=None,
    accessibility=None,
):
    frame = state.frame if state is not None else _frame()
    state = state or _state(frame)
    expected_binding = _binding(frame)
    session = session or AllowSession()
    observations = observations or FakeObservations(_observation(frame))
    binding = binding or FakeBinding(
        SemanticFocusSnapshot(expected_binding, True),
        SemanticFocusSnapshot(expected_binding, True),
    )
    browser = browser or FakeAdapter(PointerDispatchKind.BROWSER_SEMANTIC)
    accessibility = accessibility or FakeAdapter(
        PointerDispatchKind.ACCESSIBILITY_SEMANTIC,
        status=SemanticResolutionStatus.NOT_APPLICABLE,
    )
    audit = HashAudit()
    core = VirtualTargetActivationCore(
        state=state,
        session=session,
        observations=observations,
        binding=binding,
        browser=browser,
        accessibility=accessibility,
        audit=audit,
        now=lambda: NOW,
        receipt_id=lambda: f"receipt-{len(audit.receipts) + 1}",
    )
    return core, session, observations, binding, browser, accessibility, audit


def test_browser_semantic_path_runs_first_and_records_verified_receipt() -> None:
    core, _, observations, binding, browser, accessibility, audit = _core()

    receipt = core.activate(_request())

    assert browser.calls == ["resolve", "activate", "verify"]
    assert accessibility.calls == []
    assert len(observations.calls) == 1
    assert len(binding.calls) == 2
    assert receipt.dispatch_kind is PointerDispatchKind.BROWSER_SEMANTIC
    assert receipt.requested_operation == "semantic_activate"
    assert receipt.source_id == "llm-source-1"
    assert receipt.target_id == "target-1"
    assert receipt.window_token == "window-token-1"
    assert receipt.frame == _frame()
    assert receipt.applied is True and receipt.verified is True
    assert receipt.uncertain is False and receipt.safe_to_retry is False
    assert receipt.audit_recorded is True
    assert audit.receipts[0].audit_recorded is False
    assert "click" not in repr(receipt.to_dict()).casefold()


def test_concrete_browser_driver_adapter_resolves_acts_and_verifies_semantically() -> None:
    driver = FakeSemanticBrowserDriver()
    assert isinstance(driver, SemanticBrowserDriver)
    adapter = BrowserDriverSemanticAdapter(
        driver,
        postcondition=lambda before, after: before != after,
    )
    core, *_ = _core(browser=adapter)

    receipt = core.activate(_request())

    assert receipt.applied is True and receipt.verified is True
    assert receipt.dispatch_kind is PointerDispatchKind.BROWSER_SEMANTIC
    assert receipt.target_identity.stable_id == "dom-node-7"
    names = [call if isinstance(call, str) else call[0] for call in driver.calls]
    assert names == [
        "semantic_context",
        "resolve_semantic_target",
        "semantic_context",
        "activate_semantic_target",
        "semantic_context",
        "semantic_target_state",
    ]
    resolve_args = driver.calls[1][1]
    assert resolve_args["exact"] is True
    assert resolve_args["context_id"] == "context-1"
    assert resolve_args["observation_id"] == "observation-1"
    assert resolve_args["window_token"] == "window-token-1"
    assert resolve_args["target_id"] == "target-1"
    assert resolve_args["frame"] == pointer_frame_record(_frame())


def test_browser_adapter_rejects_changed_context_before_target_resolution() -> None:
    driver = FakeSemanticBrowserDriver()
    driver.context_overrides["window_token"] = "changed-window"
    adapter = BrowserDriverSemanticAdapter(driver, lambda before, after: before != after)

    resolution = adapter.resolve_exact(_observation(), _request())

    assert resolution.status is SemanticResolutionStatus.REJECTED
    assert resolution.reason == "browser-exact-resolution-failed"
    assert driver.calls == ["semantic_context"]


def test_browser_adapter_requires_exact_unique_target_and_one_shot_context_claim() -> None:
    driver = FakeSemanticBrowserDriver()
    driver.resolve_overrides["unique"] = False
    adapter = BrowserDriverSemanticAdapter(driver, lambda before, after: before != after)
    first = adapter.resolve_exact(_observation(), _request())
    second = adapter.resolve_exact(_observation(), _request())

    assert first.status is SemanticResolutionStatus.REJECTED
    assert first.reason == "browser-exact-resolution-failed"
    assert second.status is SemanticResolutionStatus.REJECTED
    assert second.reason == "browser-context-claim-replayed"
    assert not any(
        isinstance(call, tuple) and call[0] == "activate_semantic_target"
        for call in driver.calls
    )


def test_browser_adapter_post_binding_change_becomes_uncertain_in_core() -> None:
    driver = FakeSemanticBrowserDriver()
    driver.state_overrides["stable_id"] = "different-node"
    adapter = BrowserDriverSemanticAdapter(driver, lambda before, after: before != after)
    core, *_ = _core(browser=adapter)

    receipt = core.activate(_request())

    assert receipt.applied is None
    assert receipt.verified is False and receipt.uncertain is True
    assert receipt.safe_to_retry is False
    assert receipt.reason == "post-verification-failed"


def test_concrete_browser_adapter_never_calls_generic_execute_or_coordinates() -> None:
    source = inspect.getsource(BrowserDriverSemanticAdapter)

    assert ".execute(" not in source
    for forbidden in (
        "LocalExecutor",
        "SendInput",
        "GetCursorPos",
        "SetCursorPos",
        "mouse_event",
        "center_norm",
        "rect_px",
    ):
        assert forbidden not in source


def test_browser_not_applicable_falls_back_to_uia_but_never_os() -> None:
    frame = _frame(kind=PointerFrameKind.WINDOW_CLIENT_PHYSICAL_PX)
    browser = FakeAdapter(
        PointerDispatchKind.BROWSER_SEMANTIC,
        status=SemanticResolutionStatus.NOT_APPLICABLE,
    )
    uia = FakeAdapter(PointerDispatchKind.ACCESSIBILITY_SEMANTIC)
    core, *_ = _core(state=_state(frame), browser=browser, accessibility=uia)

    receipt = core.activate(_request(binding=_binding(frame)))

    assert browser.calls == ["resolve"]
    assert uia.calls == ["resolve", "activate", "verify"]
    assert receipt.dispatch_kind is PointerDispatchKind.ACCESSIBILITY_SEMANTIC
    assert receipt.applied is True


def test_browser_rejection_does_not_fall_through_to_uia() -> None:
    browser = FakeAdapter(
        PointerDispatchKind.BROWSER_SEMANTIC,
        status=SemanticResolutionStatus.REJECTED,
    )
    uia = FakeAdapter(PointerDispatchKind.ACCESSIBILITY_SEMANTIC)
    core, *_ = _core(browser=browser, accessibility=uia)

    receipt = core.activate(_request())

    assert browser.calls == ["resolve"]
    assert uia.calls == []
    assert receipt.applied is False


def test_no_semantic_adapter_never_creates_an_os_fallback() -> None:
    browser = FakeAdapter(
        PointerDispatchKind.BROWSER_SEMANTIC,
        status=SemanticResolutionStatus.NOT_APPLICABLE,
    )
    uia = FakeAdapter(
        PointerDispatchKind.ACCESSIBILITY_SEMANTIC,
        status=SemanticResolutionStatus.NOT_APPLICABLE,
    )
    core, *_ = _core(browser=browser, accessibility=uia)

    receipt = core.activate(_request())

    assert receipt.reason == "no-semantic-path-no-os-fallback"
    assert receipt.applied is False
    assert browser.calls == ["resolve"] and uia.calls == ["resolve"]


@pytest.mark.parametrize("origin", [PointerOrigin.SCREEN, PointerOrigin.UNKNOWN])
def test_untrusted_origin_never_claims_or_dispatches(origin) -> None:
    core, session, observations, binding, browser, accessibility, _ = _core()
    request = _request(provenance=_provenance(origin=origin))

    receipt = core.activate(request)

    assert receipt.applied is False
    assert receipt.reason == f"origin-{origin.value}-cannot-authorize"
    assert session.calls == observations.calls == binding.calls == []
    assert browser.calls == accessibility.calls == []


def test_focus_mismatch_before_dispatch_consumes_observation_but_does_not_act() -> None:
    expected = _binding()
    binding = FakeBinding(SemanticFocusSnapshot(expected, False))
    core, _, observations, _, browser, _, _ = _core(binding=binding)

    receipt = core.activate(_request())

    assert len(observations.calls) == 1
    assert browser.calls == []
    assert receipt.reason == "focus-binding-mismatch-before"
    assert receipt.applied is False
    assert receipt.safe_to_retry is False


def test_focus_change_after_dispatch_is_uncertain_and_never_retryable() -> None:
    expected = _binding()
    changed = SemanticWindowBinding(
        window_token=expected.window_token,
        hwnd=99,
        pid=expected.pid,
        process_token=expected.process_token,
        title_sha256=expected.title_sha256,
        context_id=expected.context_id,
        frame=expected.frame,
    )
    binding = FakeBinding(
        SemanticFocusSnapshot(expected, True),
        SemanticFocusSnapshot(changed, True),
    )
    core, *_ = _core(binding=binding)

    receipt = core.activate(_request())

    assert receipt.applied is None
    assert receipt.verified is False and receipt.uncertain is True
    assert receipt.safe_to_retry is False
    assert core.state.phase is PointerPhase.UNCERTAIN


def test_missing_post_state_is_uncertain_even_after_adapter_reports_applied() -> None:
    browser = FakeAdapter(PointerDispatchKind.BROWSER_SEMANTIC, verified=False)
    core, *_ = _core(browser=browser)

    receipt = core.activate(_request())

    assert browser.calls == ["resolve", "activate", "verify"]
    assert receipt.applied is None
    assert receipt.reason == "post-verification-failed"
    assert receipt.uncertain is True and receipt.safe_to_retry is False


def test_target_or_observation_change_fails_before_dispatch() -> None:
    browser = FakeAdapter(
        PointerDispatchKind.BROWSER_SEMANTIC,
        pre_hash=POST_HASH,
    )
    core, *_ = _core(browser=browser)

    receipt = core.activate(_request())

    assert browser.calls == ["resolve"]
    assert receipt.reason == "resolved-target-state-changed"
    assert receipt.applied is False


def test_sequence_replay_and_paused_state_do_not_claim_observation() -> None:
    paused = _state(phase=PointerPhase.PAUSED)
    core, _, observations, *_ = _core(state=paused)
    paused_receipt = core.activate(_request())
    assert paused_receipt.reason == "pointer-not-active"
    assert observations.calls == []

    core, _, observations, *_ = _core()
    out_of_order = core.activate(_request(sequence=2))
    assert out_of_order.reason == "sequence-out-of-order"
    assert observations.calls == []


def test_observation_window_pair_is_one_shot_even_for_a_new_action_id() -> None:
    core, _, observations, _, browser, _, _ = _core()
    first = core.activate(_request())
    second = core.activate(
        _request(sequence=2, provenance=_provenance(action_id="action-2"))
    )

    assert first.applied is True
    assert second.reason == "observation-window-token-replayed"
    assert second.applied is False
    assert len(observations.calls) == 1
    assert browser.calls == ["resolve", "activate", "verify"]


def test_registry_adapter_claims_observation_window_and_frame_once() -> None:
    registry = ObservationRegistry()
    window = {"hwnd": 42, "pid": 7001, "title": "Example"}
    frame = _frame()
    record = registry.record(
        kind="dom-tree",
        payload={"target": "stable-node-7"},
        window=window,
        frame=pointer_frame_record(frame),
    )
    request = _request(
        provenance=PointerProvenance(
            actor="agent-a",
            origin=PointerOrigin.AGENT,
            channel="headless-semantic",
            instruction_id="instruction-1",
            action_id="action-1",
            dispatch_kind=PointerDispatchKind.BROWSER_SEMANTIC,
            observation_id=record["observation_id"],
            window_token=describe_window(window)["window_token"],
            target_id="target-1",
        ),
        binding=SemanticWindowBinding(
            window_token=describe_window(window)["window_token"],
            hwnd=42,
            pid=7001,
            process_token="process-start-1",
            title_sha256=TITLE_HASH,
            context_id="context-1",
            frame=frame,
        ),
    )
    observation = SemanticObservation(
        observation_id=record["observation_id"],
        window_token=describe_window(window)["window_token"],
        context_id="context-1",
        frame=frame,
        target_id="target-1",
        target_state_signature=PRE_HASH,
    )
    port = RegistrySemanticObservationPort(
        registry,
        lambda _request: RegistryObservationSnapshot(
            observation,
            {"target": "stable-node-7"},
            window,
        ),
    )

    assert port.claim_semantic_observation(request) == observation
    with pytest.raises(RuntimeError, match="already consumed"):
        port.claim_semantic_observation(request)


def test_uia_adapter_uses_exact_resolution_and_invoke_target_only() -> None:
    class Target:
        name = "Save"
        role = "Button"
        rect_px = (10, 20, 100, 30)

    class Targeter:
        def __init__(self) -> None:
            self.calls = []

        def resolve_detailed(self, query, **kwargs):
            self.calls.append(("resolve", query, kwargs))
            return Target()

        def invoke_target(self, target, **kwargs):
            self.calls.append(("invoke_target", target, kwargs))
            return True

    states = iter((SemanticTargetState(PRE_HASH), SemanticTargetState(POST_HASH)))
    targeter = Targeter()
    adapter = UiaTargeterSemanticAdapter(
        targeter=targeter,
        state_probe=lambda _target, _request: next(states),
        postcondition=lambda before, after: before != after,
    )
    frame = _frame(kind=PointerFrameKind.WINDOW_CLIENT_PHYSICAL_PX)
    request = _request(binding=_binding(frame))
    observation = _observation(frame)

    resolution = adapter.resolve_exact(observation, request)
    outcome = adapter.activate(resolution.target, request)
    verification = adapter.verify(resolution.target, request)

    assert resolution.status is SemanticResolutionStatus.EXACT
    assert outcome.applied is True and verification.verified is True
    assert targeter.calls[0][2]["exact"] is True
    assert targeter.calls[0][2]["min_score"] == 1.0
    assert [call[0] for call in targeter.calls] == [
        "resolve",
        "invoke_target",
        "resolve",
    ]


def test_semantic_module_has_no_physical_cursor_or_os_input_path() -> None:
    source = inspect.getsource(target_module)
    for forbidden in (
        "GetCursorPos",
        "SetCursorPos",
        "SendInput",
        "mouse_event",
        "LocalExecutor",
        "left_click",
        "os_input_injection",
    ):
        assert forbidden not in source
