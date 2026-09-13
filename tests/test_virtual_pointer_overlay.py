from __future__ import annotations

import ast
import inspect
import io
from datetime import datetime, timezone
from hashlib import sha256

import pytest

import open_compute.virtual_pointer_overlay as overlay_module
from open_compute.virtual_pointer import (
    PointerAuditStamp,
    PointerAuthorization,
    PointerButton,
    PointerCoordinateFrame,
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
from open_compute.virtual_pointer_overlay import (
    CallbackPointerOverlayRenderer,
    PngPointerCaptureProjector,
    PointerOverlayCapabilities,
    PointerOverlayShape,
    PointerOverlayStyle,
    PointerProjectionError,
    PointerRenderSurface,
    RawPointerCapture,
    VirtualPointerOverlayController,
)


NOW = datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc)
ZERO_HASH = "0" * 64


def _frame(
    *,
    frame_id: str = "desktop-frame-1",
    generation: int = 1,
    left: int = -64,
    top: int = -32,
    width: int = 192,
    height: int = 96,
    scale_x: float = 1.25,
    scale_y: float = 1.5,
) -> PointerCoordinateFrame:
    return PointerCoordinateFrame(
        frame_id=frame_id,
        kind=PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX,
        left=left,
        top=top,
        width=width,
        height=height,
        generation=generation,
        scale_x=scale_x,
        scale_y=scale_y,
    )


def _provenance(
    action_id: str,
    *,
    origin: PointerOrigin = PointerOrigin.AGENT,
) -> PointerProvenance:
    return PointerProvenance(
        actor="agent-a",
        origin=origin,
        channel="headless-overlay",
        instruction_id="instruction-1",
        action_id=action_id,
        observation_id="observation-1",
        window_token="window-token-1",
        target_id="target-1",
    )


def _request(
    sequence: int,
    transition: PointerTransition,
    action_id: str,
    *,
    origin: PointerOrigin = PointerOrigin.AGENT,
    frame: PointerCoordinateFrame | None = None,
    position: PointerPosition | None = None,
    button: PointerButton | None = None,
) -> PointerTransitionRequest:
    return PointerTransitionRequest(
        sequence=sequence,
        transition=transition,
        provenance=_provenance(action_id, origin=origin),
        frame=frame,
        position=position,
        button=button,
    )


class AllowSession:
    def __init__(self) -> None:
        self.calls = []

    def authorize_pointer_transition(self, *, state, request):
        self.calls.append(request)
        return PointerAuthorization(True, state.lease_id, "allowed")


class HashAudit:
    def __init__(self) -> None:
        self.receipts = []
        self.last_hash = ZERO_HASH

    def append_pointer_receipt(self, receipt):
        previous = self.last_hash
        self.last_hash = sha256(
            previous.encode("ascii") + repr(receipt.to_dict()).encode("utf-8")
        ).hexdigest()
        self.receipts.append(receipt)
        return PointerAuditStamp(previous, self.last_hash)


def _capabilities(**overrides) -> PointerOverlayCapabilities:
    values = {
        "click_through": True,
        "no_activate": True,
        "per_monitor_v2": True,
        "renders_shape": True,
        "renders_text": True,
        "renders_color": True,
    }
    values.update(overrides)
    return PointerOverlayCapabilities(**values)


class FakeRenderer:
    def __init__(
        self,
        frame: PointerCoordinateFrame,
        *,
        capabilities: PointerOverlayCapabilities | None = None,
        fail_show: bool = False,
        fail_clear: bool = False,
    ) -> None:
        self.surface = PointerRenderSurface(frame, "topology-1")
        self.declared = capabilities or _capabilities()
        self.fail_show = fail_show
        self.fail_clear = fail_clear
        self.shown = []
        self.cleared = []

    def capabilities(self):
        return self.declared

    def current_surface(self):
        return self.surface

    def show_pointer(self, command) -> None:
        if self.fail_show:
            raise RuntimeError("mock show failure")
        self.shown.append(command)

    def clear_pointer(self, *, reason: str) -> None:
        if self.fail_clear:
            raise RuntimeError("mock clear failure")
        self.cleared.append(reason)


def _core(
    *,
    frame: PointerCoordinateFrame | None = None,
    renderer: FakeRenderer | None = None,
    style: PointerOverlayStyle | None = None,
):
    frame = frame or _frame()
    renderer = renderer or FakeRenderer(frame)
    controller = VirtualPointerOverlayController(
        renderer=renderer,
        style=style or PointerOverlayStyle(),
    )
    counter = iter(range(1, 100))
    core = VirtualPointerCore(
        state=PointerSourceState(
            source_id="llm-pointer-1",
            pointer_id="pointer-1",
            session_id="session-1",
            lease_id="lease-1",
        ),
        session=AllowSession(),
        ownership=controller,
        audit=HashAudit(),
        now=lambda: NOW,
        receipt_id=lambda: f"receipt-{next(counter)}",
    )
    return core, controller, renderer, frame


def _move(core, frame, *, sequence=1, action_id="move-1"):
    return core.apply(
        _request(
            sequence,
            PointerTransition.MOVE,
            action_id,
            frame=frame,
            position=PointerPosition(frame.left + 32, frame.top + 32),
        )
    )


def _png(width: int = 192, height: int = 96, color=(40, 42, 48)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def _raw(frame, png, **overrides) -> RawPointerCapture:
    values = {
        "capture_id": "capture-1",
        "scope_id": "window-scope-1",
        "topology_id": "topology-1",
        "frame": frame,
        "png_bytes": png,
        "ownership_overlay_contained": False,
        "llm_pointer_overlay_contained": False,
    }
    values.update(overrides)
    return RawPointerCapture(**values)


def test_overlay_style_requires_form_text_color_and_contrast() -> None:
    style = PointerOverlayStyle()

    assert style.shape is PointerOverlayShape.CROSSHAIR_DIAMOND
    assert style.label_prefix == "LLM"
    assert style.primary_color != style.outline_color
    assert style.label_text_color != style.label_background

    with pytest.raises(ValueError, match="contrast"):
        PointerOverlayStyle(
            primary_color=(10, 10, 10),
            outline_color=(11, 11, 11),
        )


def test_phase1_move_publishes_distinct_noninteractive_overlay_command() -> None:
    core, controller, renderer, frame = _core()

    receipt = _move(core, frame)

    assert receipt.applied is True
    assert len(renderer.shown) == 1
    command = renderer.shown[0]
    assert controller.last_command is command
    assert command.position == PointerPosition(-32, 0)
    assert command.frame == frame
    assert command.frame.scale_x == 1.25
    assert command.frame.scale_y == 1.5
    assert command.label == "LLM | agent-a"
    assert command.style.shape is PointerOverlayShape.CROSSHAIR_DIAMOND
    assert command.click_through_required is True
    assert command.no_activate_required is True


@pytest.mark.parametrize(
    "missing",
    [
        "click_through",
        "no_activate",
        "per_monitor_v2",
        "renders_shape",
        "renders_text",
        "renders_color",
    ],
)
def test_missing_renderer_capability_fails_closed(missing) -> None:
    frame = _frame()
    renderer = FakeRenderer(frame, capabilities=_capabilities(**{missing: False}))
    core, controller, _, _ = _core(frame=frame, renderer=renderer)

    receipt = _move(core, frame)

    assert receipt.applied is None
    assert receipt.reason == "ownership_update_uncertain"
    assert receipt.phase_after is PointerPhase.UNCERTAIN
    assert renderer.shown == []
    assert renderer.cleared == ["renderer-error"]
    assert controller.last_command is None


def test_monitor_generation_or_hotplug_change_fails_closed_without_clamp() -> None:
    frame = _frame(generation=1)
    renderer = FakeRenderer(frame)
    core, controller, _, _ = _core(frame=frame, renderer=renderer)
    assert _move(core, frame).applied is True

    changed = _frame(frame_id="desktop-frame-2", generation=2, left=-128, width=128)
    renderer.surface = PointerRenderSurface(changed, "topology-2")
    second = core.apply(
        _request(
            2,
            PointerTransition.MOVE,
            "move-after-hotplug",
            frame=frame,
            position=PointerPosition(frame.left + 20, frame.top + 20),
        )
    )

    assert second.applied is None
    assert second.phase_after is PointerPhase.UNCERTAIN
    assert renderer.cleared[-1] == "renderer-error"
    assert controller.last_command is None


@pytest.mark.parametrize(
    "transition,origin,expected_reason",
    [
        (PointerTransition.PAUSE, PointerOrigin.USER, "transition-pause"),
        (PointerTransition.ABORT, PointerOrigin.USER, "transition-abort"),
        (PointerTransition.CLEANUP, PointerOrigin.SYSTEM, "transition-cleanup"),
    ],
)
def test_pause_abort_and_cleanup_clear_overlay(transition, origin, expected_reason) -> None:
    core, controller, renderer, frame = _core()
    _move(core, frame)

    receipt = core.apply(_request(2, transition, f"{transition.value}-1", origin=origin))

    assert receipt.applied is True
    assert renderer.cleared == [expected_reason]
    assert controller.last_command is None


def test_lease_end_and_integration_error_have_explicit_clear_paths() -> None:
    core, controller, renderer, frame = _core()
    _move(core, frame)

    controller.clear_for_lease_end()
    assert renderer.cleared[-1] == "lease-ended"
    assert controller.last_command is None

    _move(core, frame, sequence=2, action_id="move-2")
    controller.clear_for_error()
    assert renderer.cleared[-1] == "integration-error"
    assert controller.last_command is None


def test_renderer_show_failure_clears_and_makes_phase1_receipt_uncertain() -> None:
    frame = _frame()
    renderer = FakeRenderer(frame, fail_show=True)
    core, controller, _, _ = _core(frame=frame, renderer=renderer)

    receipt = _move(core, frame)

    assert receipt.applied is None
    assert receipt.phase_after is PointerPhase.UNCERTAIN
    assert renderer.cleared == ["renderer-error"]
    assert controller.last_command is None


def test_callback_renderer_delegates_without_paths_or_platform_state() -> None:
    frame = _frame()
    shown = []
    cleared = []
    adapter = CallbackPointerOverlayRenderer(
        declared_capabilities=_capabilities(),
        surface_provider=lambda: PointerRenderSurface(frame, "topology-1"),
        show_callback=shown.append,
        clear_callback=cleared.append,
    )
    core, _, _, _ = _core(frame=frame, renderer=adapter)

    assert _move(core, frame).applied is True
    assert len(shown) == 1
    adapter.clear_pointer(reason="lease-ended")
    assert cleared == ["lease-ended"]


def test_model_projection_preserves_raw_and_writes_only_model_copy() -> None:
    Image = pytest.importorskip("PIL.Image")
    core, controller, _, frame = _core()
    _move(core, frame)
    raw_png = _png()
    raw = _raw(frame, raw_png, ownership_overlay_contained=True)

    bundle = PngPointerCaptureProjector().project(
        raw,
        command=controller.last_command,
        include_pointer=True,
    )

    assert bundle.raw_frame is raw
    assert bundle.raw_frame.png_bytes == raw_png
    assert bundle.model_frame.png_bytes != raw_png
    assert bundle.model_frame.pointer_projection is True
    assert bundle.model_frame.source_id == "llm-pointer-1"
    assert bundle.audit_frame.raw_sha256 == sha256(raw_png).hexdigest()
    assert bundle.audit_frame.model_sha256 == sha256(
        bundle.model_frame.png_bytes
    ).hexdigest()
    assert bundle.audit_frame.ownership_overlay_contained is True
    assert bundle.audit_frame.llm_pointer_overlay_contained is False
    assert bundle.audit_frame.pointer_position == (-32, 0)
    assert not hasattr(bundle.audit_frame, "png_bytes")

    with Image.open(io.BytesIO(bundle.model_frame.png_bytes)) as image:
        assert image.convert("RGB").getpixel((32, 32)) == (0, 220, 255)


def test_projection_disabled_keeps_model_bytes_identical_and_declared() -> None:
    frame = _frame()
    raw_png = _png()
    raw = _raw(frame, raw_png)

    bundle = PngPointerCaptureProjector().project(
        raw,
        command=None,
        include_pointer=False,
    )

    assert bundle.model_frame.png_bytes == raw_png
    assert bundle.model_frame.pointer_projection is False
    assert bundle.audit_frame.pointer_projection is False
    assert bundle.audit_frame.source_id is None
    assert bundle.audit_frame.pointer_position is None
    assert bundle.audit_frame.raw_sha256 == bundle.audit_frame.model_sha256


def test_existing_llm_overlay_is_never_silently_projected_twice() -> None:
    core, controller, _, frame = _core()
    _move(core, frame)
    raw = _raw(frame, _png(), llm_pointer_overlay_contained=True)

    with pytest.raises(PointerProjectionError, match="pointer-already-in-raw-frame"):
        PngPointerCaptureProjector().project(
            raw,
            command=controller.last_command,
            include_pointer=True,
        )


def test_capture_frame_generation_mismatch_fails_closed() -> None:
    core, controller, _, frame = _core()
    _move(core, frame)
    changed = _frame(frame_id="desktop-frame-2", generation=2)
    raw = _raw(changed, _png())

    with pytest.raises(PointerProjectionError, match="capture-frame-stale"):
        PngPointerCaptureProjector().project(
            raw,
            command=controller.last_command,
            include_pointer=True,
        )


def test_capture_topology_mismatch_fails_closed_even_with_same_frame() -> None:
    core, controller, _, frame = _core()
    _move(core, frame)
    raw = _raw(frame, _png(), topology_id="topology-2")

    with pytest.raises(PointerProjectionError, match="capture-topology-stale"):
        PngPointerCaptureProjector().project(
            raw,
            command=controller.last_command,
            include_pointer=True,
        )


def test_capture_dimensions_must_match_physical_frame_exactly() -> None:
    core, controller, _, frame = _core()
    _move(core, frame)
    raw = _raw(frame, _png(width=32, height=32))

    with pytest.raises(PointerProjectionError, match="capture-dimensions-mismatch"):
        PngPointerCaptureProjector().project(
            raw,
            command=controller.last_command,
            include_pointer=True,
        )


def test_browser_css_frame_cannot_be_used_as_physical_overlay_surface() -> None:
    browser_frame = PointerCoordinateFrame(
        frame_id="browser-frame-1",
        kind=PointerFrameKind.BROWSER_VIEWPORT_CSS_PX,
        left=0,
        top=0,
        width=1280,
        height=720,
        generation=1,
        context_id="browser-context-1",
    )

    with pytest.raises(ValueError, match="physical-pixel"):
        PointerRenderSurface(browser_frame, "topology-1")
    with pytest.raises(ValueError, match="physical-pixel"):
        _raw(browser_frame, _png())


def test_overlay_module_never_reads_or_moves_physical_cursor() -> None:
    tree = ast.parse(inspect.getsource(overlay_module))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert "open_compute.indicator" not in imported_modules
    assert "open_compute.drivers.local" not in imported_modules
    assert "ctypes" not in imported_modules
    source = inspect.getsource(overlay_module)
    assert "GetCursorPos" not in source
    assert "SendInput" not in source
