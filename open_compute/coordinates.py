"""Coordinate normalization utilities.

The agent loop keeps all coordinates as normalized floats in ``0.0 .. 1.0`` so
that a single screenshot-and-reason cycle is resolution- and DPI-independent.
Each backend adapter denormalizes to its own pixel space at dispatch time. This
centralizes the DPI/scaling problem in one tested place.

Pure standard library; no third-party imports.
"""

from __future__ import annotations


def normalize(px: float, py: float, width: int, height: int) -> tuple[float, float]:
    """Convert pixel coordinates to normalized ``0..1`` floats.

    Args:
        px, py: Pixel coordinates (may be floats).
        width, height: Display dimensions in pixels. Must be positive.

    Returns:
        ``(nx, ny)`` clamped to ``[0.0, 1.0]``.

    Raises:
        ValueError: If ``width`` or ``height`` is not positive.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width and height must be positive, got {width}x{height}")
    nx = px / width
    ny = py / height
    return _clamp01(nx), _clamp01(ny)


def denormalize(nx: float, ny: float, width: int, height: int) -> tuple[int, int]:
    """Convert normalized ``0..1`` coordinates to integer pixels.

    Args:
        nx, ny: Normalized coordinates. Values outside ``[0, 1]`` are clamped.
        width, height: Target display dimensions in pixels. Must be positive.

    Returns:
        ``(px, py)`` integer pixel coordinates, each within
        ``[0, width-1]`` / ``[0, height-1]``.

    Raises:
        ValueError: If ``width`` or ``height`` is not positive.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width and height must be positive, got {width}x{height}")
    px = int(round(_clamp01(nx) * width))
    py = int(round(_clamp01(ny) * height))
    # Keep the result a valid addressable pixel index.
    px = min(px, width - 1)
    py = min(py, height - 1)
    return px, py


def rescale(
    px: float,
    py: float,
    src: tuple[int, int],
    dst: tuple[int, int],
) -> tuple[int, int]:
    """Rescale a pixel coordinate from one resolution to another.

    Convenience wrapper around :func:`normalize` + :func:`denormalize`, useful
    when a screenshot was captured at one resolution but actions execute against
    a display at a different resolution/DPI.

    Args:
        px, py: Source pixel coordinate.
        src: ``(width, height)`` of the source space.
        dst: ``(width, height)`` of the destination space.

    Returns:
        ``(px, py)`` in the destination pixel space.
    """
    nx, ny = normalize(px, py, src[0], src[1])
    return denormalize(nx, ny, dst[0], dst[1])


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
