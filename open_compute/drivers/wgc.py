"""Windows.Graphics.Capture (WGC) screenshot backend.

Grabs the composited monitor or a single window - including DirectX /
hardware-rendered / occluded content (e.g. Roblox Studio, Blender, games) -
where GDI ``BitBlt`` (used by ``mss``) fails. GDI fails in two different ways,
and the second one is the nasty one: it either raises ("Zugriff verweigert" /
"Access denied"), or it *succeeds* and hands back an all-black rectangle. See
:func:`is_blank_png`, which is what lets a caller notice the silent case.

Optional extra: ``pip install windows-capture`` (pulls a Rust-backed WGC wrapper).
PNG encoding goes through Pillow; the ``windows-capture`` package itself pulls
numpy and OpenCV as transitive dependencies.

The WGC frame pool needs a few frames to warm up (the first frames are often
black), so we skip the first ``skip`` frames before grabbing one. Capture runs
free-threaded, which hands us the ``CaptureControl`` up front so the watchdog
can always stop it after ``max_seconds``: WGC delivers frames only when the
target *redraws*, so an idle window (a paused player, a still viewport) is a
normal state that must not hang the call — and a window WGC cannot capture at
all raises immediately instead.
"""
from __future__ import annotations

import io
import threading


def available() -> bool:
    """True if the WGC backend can be used on this host."""
    try:
        import windows_capture  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def grab_monitor_png(monitor_index: int = 1, skip: int = 3,
                     max_seconds: float = 6.0, retries: int = 4) -> tuple[bytes, int, int]:
    """Capture one monitor frame via WGC and return ``(png_bytes, width, height)``.

    The WGC ``GraphicsCaptureItem`` creation is transiently flaky (especially
    right after a failed GDI/mss grab leaves the DWM state churned), so this
    retries a few times with a short backoff before giving up.

    Args:
        monitor_index: 1-based monitor index (1 = primary).
        skip: number of warm-up frames to discard before grabbing.
        max_seconds: watchdog timeout; raises if no usable frame arrives.
        retries: attempts on transient WGC init failures.

    Raises:
        ImportError: windows-capture / Pillow not installed.
        RuntimeError: capture still failing after ``retries`` attempts.
    """
    return _grab_with_retries(
        {"monitor_index": monitor_index}, skip, max_seconds, retries
    )


def grab_window_png(hwnd: int, skip: int = 3, max_seconds: float = 6.0,
                    retries: int = 4) -> tuple[bytes, int, int]:
    """Capture one window's frame via WGC and return ``(png_bytes, width, height)``.

    This is the window-level counterpart to :func:`grab_monitor_png`, and the
    reason it exists: a GDI region grab of a DirectX / hardware-composited
    window (Roblox Studio, Blender, a GPU-accelerated browser) succeeds but
    returns an all-black rectangle. WGC asks the compositor instead and gets the
    real pixels.

    The frame is the window's client-inclusive capture surface, which may differ
    slightly from ``GetWindowRect`` (shadow/border are not composited), so it is
    a view of that window, not a crop of the desktop.

    Args:
        hwnd: Win32 window handle (from ``cli._find_window_hwnd``). Passing the
            HWND rather than a title keeps window resolution in one place —
            WGC's own ``window_name`` matcher would be a second, divergent one.
        skip: number of warm-up frames to discard before grabbing.
        max_seconds: watchdog timeout; raises if no usable frame arrives.
        retries: attempts on transient WGC init failures.

    Raises:
        ImportError: windows-capture / Pillow not installed.
        RuntimeError: capture still failing after ``retries`` attempts (e.g. the
            window is minimized — WGC cannot capture an unmapped window).
    """
    return _grab_with_retries({"window_hwnd": int(hwnd)}, skip, max_seconds, retries)


def is_blank_png(png_bytes: bytes, threshold: int = 8) -> bool:
    """True if the PNG is (near-)uniformly black — the GDI-on-GPU failure mode.

    A GDI ``BitBlt`` of a hardware-composited window does not raise; it quietly
    hands back a black rectangle. Detecting that is what lets the capture path
    fall back to WGC automatically instead of returning a useless screenshot.

    Args:
        png_bytes: Encoded PNG.
        threshold: Maximum grey level (0..255) still counted as black; a small
            tolerance covers the near-black frames WGC/GDI sometimes produce.

    Returns:
        ``False`` when Pillow is unavailable or the image cannot be read — the
        check is an optimization, never a reason to fail a capture.
    """
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            _, brightest = img.convert("L").getextrema()
    except Exception:  # pragma: no cover - defensive: unreadable/odd PNG
        return False
    return brightest <= threshold


def _grab_with_retries(target: dict, skip: int, max_seconds: float,
                       retries: int) -> tuple[bytes, int, int]:
    import time
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return _grab_once(target, skip, max_seconds)
        except RuntimeError as exc:
            last = exc
            time.sleep(0.4 * (attempt + 1))
    assert last is not None
    raise last


def _grab_once(target: dict, skip: int, max_seconds: float) -> tuple[bytes, int, int]:
    from windows_capture import WindowsCapture
    from PIL import Image

    result: dict = {"png": None, "size": None, "count": 0, "err": None, "ctrl": None}
    done = threading.Event()

    cap = WindowsCapture(
        cursor_capture=False,
        draw_border=False,
        **target,  # monitor_index=... or window_hwnd=...
    )

    @cap.event
    def on_frame_arrived(frame, capture_control):  # type: ignore[no-untyped-def]
        result["ctrl"] = capture_control           # keep for the watchdog
        result["count"] += 1
        if result["count"] >= skip and result["png"] is None:
            try:
                buf = frame.frame_buffer            # (h, w, 4) BGRA numpy view
                rgb = buf[:, :, [2, 1, 0]]          # BGR -> RGB, drop alpha
                img = Image.fromarray(rgb, "RGB")
                bio = io.BytesIO()
                img.save(bio, format="PNG")
                result["png"] = bio.getvalue()
                result["size"] = (frame.width, frame.height)
            except Exception as exc:                # pragma: no cover - defensive
                result["err"] = exc
            finally:
                done.set()
                try:
                    capture_control.stop()
                except Exception:
                    pass

    @cap.event
    def on_closed():  # type: ignore[no-untyped-def]
        done.set()

    # ``start_free_threaded()`` hands back the CaptureControl *immediately*, so a
    # stop is always possible. The blocking ``start()`` does not: it only yields
    # a control through the frame callback, so a target that never produces a
    # frame — a window that is not redrawing, e.g. a paused player or an idle
    # 3D viewport — leaves the watchdog with nothing to stop and hangs the call
    # forever (observed on a real desktop). WGC only pushes frames on change, so
    # "no frame" is a normal state, not an error, and it must stay bounded.
    try:
        control = cap.start_free_threaded()
    except Exception as exc:
        raise RuntimeError(f"WGC start failed: {exc}") from exc

    got = done.wait(timeout=max_seconds)
    try:
        control.stop()
    except Exception:  # pragma: no cover - already stopped by the callback
        pass
    got = result["png"] is not None

    if result["err"] is not None:
        raise RuntimeError(f"WGC frame decode failed: {result['err']}")
    if not got or result["png"] is None:
        raise RuntimeError(
            f"WGC {target}: no usable frame within {max_seconds}s "
            f"(frames seen: {result['count']})"
        )
    w, h = result["size"]
    return result["png"], w, h
