"""Windows.Graphics.Capture (WGC) screenshot backend.

Grabs the composited monitor - including DirectX / hardware-rendered / occluded
windows (e.g. Roblox Studio, Blender, games) - where GDI ``BitBlt`` (used by
``mss``) fails with "Zugriff verweigert" / "Access denied".

Optional extra: ``pip install windows-capture`` (pulls a Rust-backed WGC wrapper).
PNG encoding goes through Pillow; the ``windows-capture`` package itself pulls
numpy and OpenCV as transitive dependencies.

The WGC frame pool needs a few frames to warm up (the first frames are often
black), so we skip the first ``skip`` frames before grabbing one. A watchdog
stops the capture after ``max_seconds`` so a static screen can't hang the call.
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
    import time
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return _grab_once(monitor_index, skip, max_seconds)
        except RuntimeError as exc:
            last = exc
            time.sleep(0.4 * (attempt + 1))
    assert last is not None
    raise last


def _grab_once(monitor_index: int, skip: int, max_seconds: float) -> tuple[bytes, int, int]:
    from windows_capture import WindowsCapture
    from PIL import Image

    result: dict = {"png": None, "size": None, "count": 0, "err": None, "ctrl": None}
    done = threading.Event()

    cap = WindowsCapture(
        cursor_capture=False,
        draw_border=False,
        monitor_index=monitor_index,
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

    # WGC's WinRT capture must run on the calling (main) thread - starting it on
    # a worker thread fails with "Failed to convert item to GraphicsCaptureItem".
    # ``start()`` blocks until the frame callback calls ``stop()``. A watchdog
    # thread stops it via the captured control if frames are too slow, so the
    # call returns within ``max_seconds`` instead of hanging.
    def _watchdog() -> None:
        if not done.wait(timeout=max_seconds):
            ctrl = result.get("ctrl")
            if ctrl is not None:
                try:
                    ctrl.stop()
                except Exception:
                    pass
            done.set()

    threading.Thread(target=_watchdog, daemon=True).start()
    try:
        cap.start()
    except Exception as exc:
        raise RuntimeError(f"WGC start failed: {exc}") from exc
    got = result["png"] is not None

    if result["err"] is not None:
        raise RuntimeError(f"WGC frame decode failed: {result['err']}")
    if not got or result["png"] is None:
        raise RuntimeError(
            f"WGC: no usable frame within {max_seconds}s (frames seen: {result['count']})"
        )
    w, h = result["size"]
    return result["png"], w, h
