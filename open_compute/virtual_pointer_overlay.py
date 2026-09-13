"""Virtual-pointer overlay and capture projection contracts.

This module is separate from :mod:`open_compute.indicator`.  The existing
``ScreenSignalIndicator`` remains an ownership signal around the physical
cursor; it is neither imported nor reused here.

The overlay controller consumes Phase-1 ``PointerSourceState`` updates through
the ownership port.  A host renderer must explicitly prove that its surface is
click-through, non-activating, text/form capable, color capable, and
Per-Monitor-v2 aware.  A changed frame generation or topology fails closed.

The capture projector copies PNG bytes into a model-only frame and draws the
virtual pointer there.  The raw capture is never changed.  Audit data contains
hashes and projection metadata, never image bytes.  Pillow is imported lazily
only when projection is requested.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Callable, Protocol, runtime_checkable

from .virtual_pointer import (
    PointerCoordinateFrame,
    PointerFrameKind,
    PointerPhase,
    PointerPosition,
    PointerSourceState,
    PointerTransitionRequest,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PHYSICAL_FRAME_KINDS = frozenset(
    {
        PointerFrameKind.VIRTUAL_DESKTOP_PHYSICAL_PX,
        PointerFrameKind.WINDOW_CLIENT_PHYSICAL_PX,
    }
)
_VISIBLE_PHASES = frozenset(
    {PointerPhase.PREVIEW, PointerPhase.ARMED, PointerPhase.PRESSED}
)


def _identifier(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be a stable opaque identifier")
    return normalized


def _color(name: str, value: tuple[int, int, int]) -> tuple[int, int, int]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError(f"{name} must be an RGB tuple")
    normalized = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, int):
            raise ValueError(f"{name} components must be integers")
        if not 0 <= component <= 255:
            raise ValueError(f"{name} components must be in 0..255")
        normalized.append(component)
    return tuple(normalized)


def _relative_luminance(color: tuple[int, int, int]) -> float:
    channels = []
    for component in color:
        value = component / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    high, low = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _physical_frame(frame: PointerCoordinateFrame) -> None:
    if frame.kind not in _PHYSICAL_FRAME_KINDS:
        raise ValueError("overlay projection requires a physical-pixel frame")
    for name in ("left", "top", "width", "height"):
        value = getattr(frame, name)
        if not math.isfinite(value) or int(value) != value:
            raise ValueError("physical-pixel frame bounds must be finite integers")


class PointerOverlayShape(str, Enum):
    """A distinct form; the pointer is never communicated by color alone."""

    CROSSHAIR_DIAMOND = "crosshair_diamond"


@dataclass(frozen=True)
class PointerOverlayStyle:
    shape: PointerOverlayShape = PointerOverlayShape.CROSSHAIR_DIAMOND
    primary_color: tuple[int, int, int] = (0, 220, 255)
    outline_color: tuple[int, int, int] = (10, 12, 20)
    label_text_color: tuple[int, int, int] = (255, 255, 255)
    label_background: tuple[int, int, int] = (20, 24, 38)
    label_prefix: str = "LLM"
    radius_px: int = 14
    stroke_px: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "shape", PointerOverlayShape(self.shape))
        for name in (
            "primary_color",
            "outline_color",
            "label_text_color",
            "label_background",
        ):
            object.__setattr__(self, name, _color(name, getattr(self, name)))
        object.__setattr__(
            self,
            "label_prefix",
            _identifier("label_prefix", self.label_prefix),
        )
        if isinstance(self.radius_px, bool) or not 8 <= self.radius_px <= 48:
            raise ValueError("radius_px must be in 8..48")
        if isinstance(self.stroke_px, bool) or not 2 <= self.stroke_px <= 8:
            raise ValueError("stroke_px must be in 2..8")
        if _contrast(self.primary_color, self.outline_color) < 3:
            raise ValueError("pointer primary and outline colors need contrast >= 3:1")
        if _contrast(self.label_text_color, self.label_background) < 4.5:
            raise ValueError("label text and background need contrast >= 4.5:1")


@dataclass(frozen=True)
class PointerOverlayCapabilities:
    click_through: bool
    no_activate: bool
    per_monitor_v2: bool
    renders_shape: bool
    renders_text: bool
    renders_color: bool

    def __post_init__(self) -> None:
        for name in (
            "click_through",
            "no_activate",
            "per_monitor_v2",
            "renders_shape",
            "renders_text",
            "renders_color",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")

    @property
    def satisfies_contract(self) -> bool:
        return all(
            (
                self.click_through,
                self.no_activate,
                self.per_monitor_v2,
                self.renders_shape,
                self.renders_text,
                self.renders_color,
            )
        )


@dataclass(frozen=True)
class PointerRenderSurface:
    frame: PointerCoordinateFrame
    topology_id: str

    def __post_init__(self) -> None:
        _physical_frame(self.frame)
        object.__setattr__(
            self,
            "topology_id",
            _identifier("topology_id", self.topology_id),
        )


@dataclass(frozen=True)
class PointerOverlayCommand:
    source_id: str
    pointer_id: str
    session_id: str
    lease_id: str
    sequence: int
    phase: PointerPhase
    position: PointerPosition
    frame: PointerCoordinateFrame
    topology_id: str
    actor: str
    label: str
    style: PointerOverlayStyle
    click_through_required: bool = field(default=True, init=False)
    no_activate_required: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "pointer_id",
            "session_id",
            "lease_id",
            "topology_id",
            "actor",
        ):
            object.__setattr__(self, name, _identifier(name, getattr(self, name)))
        object.__setattr__(self, "phase", PointerPhase(self.phase))
        if isinstance(self.sequence, bool) or int(self.sequence) != self.sequence:
            raise ValueError("overlay sequence must be a positive integer")
        if self.sequence <= 0:
            raise ValueError("overlay sequence must be a positive integer")
        object.__setattr__(self, "sequence", int(self.sequence))
        if self.phase not in _VISIBLE_PHASES:
            raise ValueError("only visible pointer phases can be rendered")
        _physical_frame(self.frame)
        if not self.frame.contains(self.position):
            raise ValueError("overlay position is outside the render frame")
        expected_label = f"{self.style.label_prefix} | {self.actor}"
        if self.label != expected_label:
            raise ValueError("overlay label must identify LLM prefix and actor")


@runtime_checkable
class PointerOverlayRenderer(Protocol):
    """Host seam for a visible but non-interactive pointer window."""

    def capabilities(self) -> PointerOverlayCapabilities: ...

    def current_surface(self) -> PointerRenderSurface: ...

    def show_pointer(self, command: PointerOverlayCommand) -> None: ...

    def clear_pointer(self, *, reason: str) -> None: ...


@dataclass
class CallbackPointerOverlayRenderer:
    """System-agnostic renderer adapter around host-supplied callbacks."""

    declared_capabilities: PointerOverlayCapabilities
    surface_provider: Callable[[], PointerRenderSurface]
    show_callback: Callable[[PointerOverlayCommand], None]
    clear_callback: Callable[[str], None]

    def capabilities(self) -> PointerOverlayCapabilities:
        return self.declared_capabilities

    def current_surface(self) -> PointerRenderSurface:
        return self.surface_provider()

    def show_pointer(self, command: PointerOverlayCommand) -> None:
        self.show_callback(command)

    def clear_pointer(self, *, reason: str) -> None:
        self.clear_callback(_identifier("reason", reason))


class PointerOverlayContractError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = _identifier("code", code)
        super().__init__(self.code)


@dataclass
class VirtualPointerOverlayController:
    """Phase-1 ownership-port implementation for a dedicated LLM pointer."""

    renderer: PointerOverlayRenderer
    style: PointerOverlayStyle = field(default_factory=PointerOverlayStyle)
    last_command: PointerOverlayCommand | None = None

    def publish_pointer(
        self,
        *,
        state: PointerSourceState,
        request: PointerTransitionRequest,
    ) -> None:
        if (
            state.phase not in _VISIBLE_PHASES
            or state.position is None
            or state.frame is None
        ):
            self._clear("state-not-visible")
            return
        try:
            capabilities = self.renderer.capabilities()
            if (
                not isinstance(capabilities, PointerOverlayCapabilities)
                or not capabilities.satisfies_contract
            ):
                raise PointerOverlayContractError("renderer-capability-mismatch")
            surface = self.renderer.current_surface()
            if not isinstance(surface, PointerRenderSurface):
                raise PointerOverlayContractError("renderer-surface-invalid")
            if surface.frame != state.frame:
                raise PointerOverlayContractError("render-frame-stale")
            command = PointerOverlayCommand(
                source_id=state.source_id,
                pointer_id=state.pointer_id,
                session_id=state.session_id,
                lease_id=state.lease_id,
                sequence=state.sequence,
                phase=state.phase,
                position=state.position,
                frame=state.frame,
                topology_id=surface.topology_id,
                actor=request.provenance.actor,
                label=f"{self.style.label_prefix} | {request.provenance.actor}",
                style=self.style,
            )
            self.renderer.show_pointer(command)
            self.last_command = command
        except Exception as exc:  # noqa: BLE001 - renderer failures clear then fail closed
            self._clear("renderer-error")
            if isinstance(exc, PointerOverlayContractError):
                raise
            raise PointerOverlayContractError("renderer-show-failed") from exc

    def clear_pointer(
        self,
        *,
        state: PointerSourceState,
        request: PointerTransitionRequest,
    ) -> None:
        self._clear(f"transition-{request.transition.value}")

    def clear_for_lease_end(self) -> None:
        self._clear("lease-ended")

    def clear_for_error(self) -> None:
        self._clear("integration-error")

    def _clear(self, reason: str) -> None:
        self.last_command = None
        try:
            self.renderer.clear_pointer(reason=_identifier("reason", reason))
        except Exception as exc:  # noqa: BLE001 - a failed clear is a hard contract error
            raise PointerOverlayContractError("renderer-clear-failed") from exc


@dataclass(frozen=True)
class RawPointerCapture:
    capture_id: str
    scope_id: str
    topology_id: str
    frame: PointerCoordinateFrame
    png_bytes: bytes
    ownership_overlay_contained: bool = False
    llm_pointer_overlay_contained: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "capture_id", _identifier("capture_id", self.capture_id))
        object.__setattr__(self, "scope_id", _identifier("scope_id", self.scope_id))
        object.__setattr__(
            self,
            "topology_id",
            _identifier("topology_id", self.topology_id),
        )
        _physical_frame(self.frame)
        if not isinstance(self.png_bytes, bytes) or not self.png_bytes:
            raise ValueError("raw capture must contain encoded PNG bytes")
        if not isinstance(self.ownership_overlay_contained, bool):
            raise ValueError("ownership_overlay_contained must be boolean")
        if not isinstance(self.llm_pointer_overlay_contained, bool):
            raise ValueError("llm_pointer_overlay_contained must be boolean")


@dataclass(frozen=True)
class ModelPointerFrame:
    capture_id: str
    topology_id: str
    frame: PointerCoordinateFrame
    png_bytes: bytes
    pointer_projection: bool
    source_id: str | None
    pointer_sequence: int | None


@dataclass(frozen=True)
class PointerCaptureAudit:
    capture_id: str
    scope_id: str
    topology_id: str
    frame_id: str
    frame_generation: int
    raw_sha256: str
    model_sha256: str
    ownership_overlay_contained: bool
    llm_pointer_overlay_contained: bool
    pointer_projection: bool
    source_id: str | None
    pointer_sequence: int | None
    pointer_position: tuple[float, float] | None


@dataclass(frozen=True)
class PointerCaptureBundle:
    raw_frame: RawPointerCapture
    model_frame: ModelPointerFrame
    audit_frame: PointerCaptureAudit


class PointerProjectionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = _identifier("code", code)
        super().__init__(self.code)


@dataclass
class PngPointerCaptureProjector:
    """Draw an LLM pointer into a copy for the model, never into raw capture."""

    style: PointerOverlayStyle = field(default_factory=PointerOverlayStyle)

    def project(
        self,
        raw_frame: RawPointerCapture,
        *,
        command: PointerOverlayCommand | None,
        include_pointer: bool,
    ) -> PointerCaptureBundle:
        model_png = raw_frame.png_bytes
        projected = False
        source_id = None
        pointer_sequence = None
        pointer_position = None

        if include_pointer:
            if command is None:
                raise PointerProjectionError("pointer-command-required")
            if raw_frame.llm_pointer_overlay_contained:
                raise PointerProjectionError("pointer-already-in-raw-frame")
            if command.frame != raw_frame.frame:
                raise PointerProjectionError("capture-frame-stale")
            if command.topology_id != raw_frame.topology_id:
                raise PointerProjectionError("capture-topology-stale")
            if command.style != self.style:
                raise PointerProjectionError("pointer-style-mismatch")
            model_png = self._draw(raw_frame, command)
            projected = True
            source_id = command.source_id
            pointer_sequence = command.sequence
            pointer_position = (command.position.x, command.position.y)

        raw_hash = sha256(raw_frame.png_bytes).hexdigest()
        model_hash = sha256(model_png).hexdigest()
        model_frame = ModelPointerFrame(
            capture_id=raw_frame.capture_id,
            topology_id=raw_frame.topology_id,
            frame=raw_frame.frame,
            png_bytes=model_png,
            pointer_projection=projected,
            source_id=source_id,
            pointer_sequence=pointer_sequence,
        )
        audit_frame = PointerCaptureAudit(
            capture_id=raw_frame.capture_id,
            scope_id=raw_frame.scope_id,
            topology_id=raw_frame.topology_id,
            frame_id=raw_frame.frame.frame_id,
            frame_generation=raw_frame.frame.generation,
            raw_sha256=raw_hash,
            model_sha256=model_hash,
            ownership_overlay_contained=raw_frame.ownership_overlay_contained,
            llm_pointer_overlay_contained=raw_frame.llm_pointer_overlay_contained,
            pointer_projection=projected,
            source_id=source_id,
            pointer_sequence=pointer_sequence,
            pointer_position=pointer_position,
        )
        return PointerCaptureBundle(raw_frame, model_frame, audit_frame)

    def _draw(
        self,
        raw_frame: RawPointerCapture,
        command: PointerOverlayCommand,
    ) -> bytes:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise PointerProjectionError("pillow-compose-extra-required") from exc

        try:
            with Image.open(io.BytesIO(raw_frame.png_bytes)) as source:
                source.load()
                image = source.convert("RGBA")
        except Exception as exc:  # noqa: BLE001 - malformed capture fails closed
            raise PointerProjectionError("raw-png-invalid") from exc

        expected_size = (int(raw_frame.frame.width), int(raw_frame.frame.height))
        if image.size != expected_size:
            raise PointerProjectionError("capture-dimensions-mismatch")

        x = int(round(command.position.x - raw_frame.frame.left))
        y = int(round(command.position.y - raw_frame.frame.top))
        if not 0 <= x < image.width or not 0 <= y < image.height:
            raise PointerProjectionError("pointer-outside-capture")

        draw = ImageDraw.Draw(image)
        style = command.style
        radius = style.radius_px
        stroke = style.stroke_px
        outline_width = stroke + 2
        primary = style.primary_color + (255,)
        outline = style.outline_color + (245,)

        draw.line((x - radius, y, x + radius, y), fill=outline, width=outline_width)
        draw.line((x, y - radius, x, y + radius), fill=outline, width=outline_width)
        draw.line((x - radius, y, x + radius, y), fill=primary, width=stroke)
        draw.line((x, y - radius, x, y + radius), fill=primary, width=stroke)
        diamond = ((x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y))
        draw.line(diamond + (diamond[0],), fill=outline, width=outline_width)
        draw.line(diamond + (diamond[0],), fill=primary, width=stroke)

        font = ImageFont.load_default()
        left, top, right, bottom = draw.textbbox((0, 0), command.label, font=font)
        text_width = right - left
        text_height = bottom - top
        padding = 3
        label_x = min(max(x + radius + 5, 0), max(0, image.width - text_width - padding * 2))
        label_y = min(max(y - text_height // 2 - padding, 0), max(0, image.height - text_height - padding * 2))
        draw.rectangle(
            (
                label_x,
                label_y,
                label_x + text_width + padding * 2,
                label_y + text_height + padding * 2,
            ),
            fill=style.label_background + (230,),
            outline=style.primary_color + (255,),
            width=1,
        )
        draw.text(
            (label_x + padding, label_y + padding - top),
            command.label,
            font=font,
            fill=style.label_text_color + (255,),
        )

        output = io.BytesIO()
        image.save(output, format="PNG", compress_level=6)
        return output.getvalue()


__all__ = [
    "CallbackPointerOverlayRenderer",
    "ModelPointerFrame",
    "PngPointerCaptureProjector",
    "PointerCaptureAudit",
    "PointerCaptureBundle",
    "PointerOverlayCapabilities",
    "PointerOverlayCommand",
    "PointerOverlayContractError",
    "PointerOverlayRenderer",
    "PointerOverlayShape",
    "PointerOverlayStyle",
    "PointerProjectionError",
    "PointerRenderSurface",
    "RawPointerCapture",
    "VirtualPointerOverlayController",
]
