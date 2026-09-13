from __future__ import annotations

import inspect

import pytest

import open_compute.virtual_pointer_windows as windows_module
from open_compute.virtual_pointer import (
    PointerCoordinateFrame,
    PointerFrameKind,
    PointerPhase,
    PointerPosition,
)
from open_compute.virtual_pointer_overlay import (
    PointerOverlayCommand,
    PointerOverlayContractError,
    PointerOverlayStyle,
    PointerRenderSurface,
)
from open_compute.virtual_pointer_windows import (
    HTTRANSPARENT,
    SWP_NOACTIVATE,
    SWP_SHOWWINDOW,
    SW_SHOWNA,
    VIRTUAL_POINTER_EXTENDED_STYLE,
    WS_EX_LAYERED,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    WS_EX_TRANSPARENT,
    WS_POPUP,
    WindowsPointerSurfaceTracker,
    WindowsVirtualDesktopSnapshot,
    WindowsVirtualPointerOverlayRenderer,
    pointer_window_spec,
    surface_matches_command,
)


class FakeProbe:
    def __init__(self, snapshot: WindowsVirtualDesktopSnapshot) -> None:
        self.value = snapshot
        self.calls = 0

    def snapshot(self) -> WindowsVirtualDesktopSnapshot:
        self.calls += 1
        return self.value


class FakeWindowHost:
    def __init__(self, surface: PointerRenderSurface) -> None:
        self.surface = surface
        self.shown = []
        self.destroyed = []

    def current_surface(self) -> PointerRenderSurface:
        return self.surface

    def show_pointer_window(self, spec) -> None:
        self.shown.append(spec)

    def destroy_pointer_window(self, *, reason: str) -> None:
        self.destroyed.append(reason)


def _snapshot(
    *,
    monitor_rects=((-1920, 0, 0, 1080), (0, 0, 1920, 1080)),
) -> WindowsVirtualDesktopSnapshot:
    return WindowsVirtualDesktopSnapshot(
        left=-1920,
        top=0,
        width=3840,
        height=1080,
        monitor_rects=monitor_rects,
    )


def _command(
    surface: PointerRenderSurface,
    *,
    position: PointerPosition = PointerPosition(-1811, 127),
) -> PointerOverlayCommand:
    return PointerOverlayCommand(
        source_id="llm-source-1",
        pointer_id="pointer-1",
        session_id="session-1",
        lease_id="lease-1",
        sequence=1,
        phase=PointerPhase.PREVIEW,
        position=position,
        frame=surface.frame,
        topology_id=surface.topology_id,
        actor="agent-a",
        label="LLM | agent-a",
        style=PointerOverlayStyle(),
    )


def test_windows_style_contract_is_click_through_nonactivating_and_topmost() -> None:
    required = (
        WS_EX_TOOLWINDOW
        | WS_EX_TRANSPARENT
        | WS_EX_LAYERED
        | WS_EX_TOPMOST
        | WS_EX_NOACTIVATE
    )
    assert VIRTUAL_POINTER_EXTENDED_STYLE == required
    assert WS_POPUP == 0x80000000
    assert SW_SHOWNA == 8
    assert HTTRANSPARENT == -1


def test_surface_tracker_preserves_negative_physical_pixels_and_stable_generation() -> None:
    probe = FakeProbe(_snapshot())
    tracker = WindowsPointerSurfaceTracker(probe)

    first = tracker.current_surface()
    repeated = tracker.current_surface()

    assert first == repeated
    assert first.frame.kind is PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX
    assert first.frame.left == -1920
    assert first.frame.top == 0
    assert first.frame.width == 3840
    assert first.frame.scale_x == first.frame.scale_y == 1.0
    assert first.frame.generation == 0


def test_monitor_topology_change_advances_generation_even_with_same_outer_bounds() -> None:
    probe = FakeProbe(_snapshot())
    tracker = WindowsPointerSurfaceTracker(probe)
    before = tracker.current_surface()

    probe.value = _snapshot(monitor_rects=((-1920, 0, 1920, 1080),))
    after = tracker.current_surface()

    assert after.frame.generation == before.frame.generation + 1
    assert after.frame.frame_id != before.frame.frame_id
    assert after.topology_id != before.topology_id


def test_snapshot_fails_closed_when_monitor_union_disagrees_with_virtual_bounds() -> None:
    with pytest.raises(ValueError, match="topology disagree"):
        WindowsVirtualDesktopSnapshot(
            left=-1920,
            top=0,
            width=3840,
            height=1080,
            monitor_rects=((-1920, 0, 0, 1080),),
        )


def test_pointer_window_placement_is_exact_and_never_clamps_negative_origin() -> None:
    surface = WindowsPointerSurfaceTracker(FakeProbe(_snapshot())).current_surface()
    command = _command(surface)

    spec = pointer_window_spec(command)

    assert spec.left < 0
    assert spec.left + spec.hotspot_x == command.position.x
    assert spec.top + spec.hotspot_y == command.position.y
    assert spec.extended_style == VIRTUAL_POINTER_EXTENDED_STYLE
    assert spec.window_style == WS_POPUP
    assert spec.show_command == SW_SHOWNA
    assert spec.set_position_flags & SWP_NOACTIVATE
    assert spec.set_position_flags & SWP_SHOWWINDOW


def test_fractional_physical_pixel_is_rejected_instead_of_silently_rounded() -> None:
    surface = WindowsPointerSurfaceTracker(FakeProbe(_snapshot())).current_surface()
    command = _command(surface, position=PointerPosition(-1811.5, 127))

    with pytest.raises(
        PointerOverlayContractError,
        match="windows-pointer-pixel-not-integral",
    ):
        pointer_window_spec(command)


def test_concrete_renderer_uses_isolated_host_without_desktop_execution() -> None:
    surface = WindowsPointerSurfaceTracker(FakeProbe(_snapshot())).current_surface()
    host = FakeWindowHost(surface)
    renderer = WindowsVirtualPointerOverlayRenderer(host=host)
    command = _command(surface)

    renderer.show_pointer(command)
    renderer.clear_pointer(reason="paused")

    assert renderer.capabilities().satisfies_contract
    assert renderer.current_surface() == surface
    assert len(host.shown) == 1
    assert host.shown[0].command == command
    assert host.destroyed == ["paused"]


def test_renderer_generation_gate_destroys_existing_window_and_fails_closed() -> None:
    original = WindowsPointerSurfaceTracker(FakeProbe(_snapshot())).current_surface()
    changed_frame = PointerCoordinateFrame(
        frame_id="windows-virtual-desktop-1-changed",
        kind=PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX,
        left=-1920,
        top=0,
        width=3840,
        height=1080,
        generation=1,
    )
    changed = PointerRenderSurface(changed_frame, "windows-topology-1-changed")
    host = FakeWindowHost(changed)
    renderer = WindowsVirtualPointerOverlayRenderer(host=host)

    with pytest.raises(PointerOverlayContractError, match="surface-stale"):
        renderer.show_pointer(_command(original))

    assert host.shown == []
    assert host.destroyed == ["surface-changed-before-show"]


def test_surface_match_binds_frame_generation_and_topology() -> None:
    surface = WindowsPointerSurfaceTracker(FakeProbe(_snapshot())).current_surface()
    command = _command(surface)
    assert surface_matches_command(surface, command)

    changed = PointerRenderSurface(surface.frame, "windows-topology-99-changed")
    assert not surface_matches_command(changed, command)


def test_native_host_contract_contains_visible_window_and_hotplug_cleanup_only() -> None:
    source = inspect.getsource(windows_module)

    for required in (
        "CreateWindowExW",
        "SetLayeredWindowAttributes",
        "SetWindowPos",
        "ShowWindow",
        "DrawTextW",
        "Polyline",
        "SetThreadDpiAwarenessContext",
        "EnumDisplayMonitors",
        "DestroyWindow",
        "surface_matches_command",
    ):
        assert required in source
    for forbidden in ("GetCursorPos", "SetCursorPos", "SendInput", "mouse_event"):
        assert forbidden not in source

