"""Command-line interface for open-compute.

Entry points (registered in ``pyproject.toml`` under ``[project.scripts]``):

    oc capture [--out PATH] [--monitor N] [--window SUBSTR]
        Capture a screenshot via the LocalExecutor and write it to a PNG file.
        Defaults to ``_session/<seq>_<timestamp>.png`` inside the module tree
        (never loose on the Desktop or CWD).  Old files rotate automatically
        (keep last 20 by default, configurable via ``OC_SESSION_KEEP``).
        Prints a JSON line ``{"width": W, "height": H, "path": "..."}`` to
        stdout so the caller knows the dimensions.
        --window SUBSTR: capture only the bounding rect of the named window
        (Win32 GetWindowRect via HWND; case-insensitive substring match).

    oc do '<json-action-or-array>' [--mode allow_all|confirm|read_only] [--yes]
                                   [--label NAME] [--shots each]
                                   [--ensure-foreground SUBSTR] [--fullres]
        Execute one canonical action **or a batch/macro (JSON array)** through
        the SafetyPolicy + LocalExecutor.

        Single action (backwards-compatible):
            oc do '{"type":"mouse_move","x":0.5,"y":0.5}'
            → {"result":"executed","action":"mouse_move","width":W,"height":H}

        Labeled single action — auto Before|After composite:
            oc do '{"type":"left_click","x":0.5,"y":0.3}' --label "click_save"
            → {"result":"executed","action":"left_click","width":W,"height":H,
               "composite":"..._click_save.png"}   # or "before"/"after" keys
               if Pillow is not installed (graceful degrade).

        Batch/macro — JSON array of actions:
            oc do '[{"type":"mouse_move","x":0.5,"y":0.5},
                    {"type":"left_click","x":0.5,"y":0.3}]'
            → {"result":"batch","count":2,"width":W,"height":H}

            Combine with --label for a final composite:
            oc do '[...]' --label "macro_foo"
            → {"result":"batch","count":2,"composite":"..."}

            Use --shots each for a per-step composite (one file per action):
            oc do '[...]' --shots each --label "macro_foo"

        --ensure-foreground SUBSTR:
            Before executing, check whether the foreground window title contains
            SUBSTR.  If not (or if ``OC_ALWAYS_FOREGROUND=1``), call
            activate_window(SUBSTR) first.

        --fullres:
            Save an additional full-resolution after-shot alongside the
            composite. Annotated with a click-coordinate marker when Pillow
            is available (circle/crosshair at click position).  Path returned
            as ``"fullres"`` in JSON output (``"fullres_annotated"`` when the
            marker was drawn).

        Exit codes: 0 = all executed, 1 = denied / confirm-needed without --yes
        (for batch: first denied action stops the run), 2 = error.

    oc run "<goal>" --backend claude|openai [--max-steps N] [--model ID]
                    [--ensure-foreground SUBSTR]
        Run the autonomous AgentLoop with a real API backend and LocalExecutor.
        Requires ANTHROPIC_API_KEY or OPENAI_API_KEY in the environment.
        --ensure-foreground performs a single pre-loop activation check.

    oc push --status
        Print FeedManager status (feeds, dosage modes, push counts, sink) as JSON.

    oc push --once [--window SUBSTR]
        Run one inject cycle (no daemon, no live interaction in tests).
        Prints the cycle summary dict as JSON.

    oc watch-dir <path> [<path>...] [--for SECS] [--once]
        Monitor one or more directories for file-system changes and print events
        as a JSON array.
        --for SECS: collect events for this many seconds (float), then exit.
        --once:     one-time snapshot diff against the last known state
                    (no background observer; compares current vs. previous scan).

Usage notes
-----------
- The ``_session/`` directory lives at the module root (never CWD or Desktop).
  Override with the ``OC_SESSION_DIR`` environment variable.
- Rotation keeps the last ``OC_SESSION_KEEP`` files (default: 20).
- Composite stitching requires Pillow (``pip install open-compute[compose]``).
  Without Pillow, separate before/after PNG files are saved and their paths
  returned in the JSON output under ``"before"`` and ``"after"`` keys.
- Dirwatch requires watchdog for native events (``pip install open-compute[watch]``).
  Without watchdog, stdlib polling is used automatically.

Pure standard library at import time. LocalExecutor (mss) and Pillow are
imported lazily.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import textwrap
import time as _time


# ---------------------------------------------------------------------------
# _session helpers
# ---------------------------------------------------------------------------

def _session_dir() -> pathlib.Path:
    """Return the _session directory (module-relative or OC_SESSION_DIR)."""
    env = os.environ.get("OC_SESSION_DIR", "")
    if env:
        d = pathlib.Path(env)
    else:
        # <package_root>/_session  (two levels up from this file: cli.py → open_compute → root)
        d = pathlib.Path(__file__).resolve().parent.parent / "_session"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _next_session_path(label: str | None = None, suffix: str = ".png") -> pathlib.Path:
    """Return a unique sequenced/timestamped path inside _session/.

    Format: ``<seq04d>_<timestamp>_<label><suffix>``
    or      ``<seq04d>_<timestamp><suffix>`` when label is None.
    """
    d = _session_dir()
    ts = _time.strftime("%Y%m%d_%H%M%S")
    # Derive next sequence number from existing files
    existing = sorted(d.glob("*.png"))
    seq = len(existing) + 1
    stem = f"{seq:04d}_{ts}"
    if label:
        # Sanitize label: keep alphanumerics, replace rest with _
        safe = "".join(c if c.isalnum() else "_" for c in label)
        stem = f"{stem}_{safe}"
    return d / f"{stem}{suffix}"


def _rotate_session(keep: int | None = None) -> None:
    """Delete oldest session files, keeping at most *keep* files."""
    if keep is None:
        try:
            keep = int(os.environ.get("OC_SESSION_KEEP", "20"))
        except ValueError:
            keep = 20
    d = _session_dir()
    files = sorted(d.glob("*.png"), key=lambda p: p.stat().st_mtime)
    to_delete = files[: max(0, len(files) - keep)]
    for f in to_delete:
        try:
            f.unlink()
        except OSError:
            pass  # best-effort


# ---------------------------------------------------------------------------
# Composite / Pillow helper
# ---------------------------------------------------------------------------

def _compose_before_after(
    before_bytes: bytes,
    after_bytes: bytes,
    label: str,
    out_path: pathlib.Path,
) -> dict:
    """Stitch before|after into one labeled PNG using Pillow (lazy import).

    Returns a dict with either:
    - ``{"composite": str(out_path)}`` on success, or
    - ``{"before": str(before_path), "after": str(after_path)}`` when Pillow
      is not installed (graceful degrade).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401 — lazy
    except ImportError:
        # Graceful degrade: save two separate files
        before_path = out_path.with_name(out_path.stem + "_before" + out_path.suffix)
        after_path = out_path.with_name(out_path.stem + "_after" + out_path.suffix)
        before_path.write_bytes(before_bytes)
        after_path.write_bytes(after_bytes)
        return {"before": str(before_path), "after": str(after_path)}

    import io
    img_before = Image.open(io.BytesIO(before_bytes)).convert("RGB")
    img_after = Image.open(io.BytesIO(after_bytes)).convert("RGB")

    # Resize to the same height (use the taller one as reference)
    h = max(img_before.height, img_after.height)
    def _resize_h(img: "Image.Image", target_h: int) -> "Image.Image":  # type: ignore[name-defined]
        if img.height == target_h:
            return img
        ratio = target_h / img.height
        return img.resize((int(img.width * ratio), target_h), Image.LANCZOS)

    img_before = _resize_h(img_before, h)
    img_after = _resize_h(img_after, h)

    # Add a 4px separator
    gap = 4
    bar_h = 24  # label bar height
    total_w = img_before.width + gap + img_after.width
    total_h = bar_h + h

    composite = Image.new("RGB", (total_w, total_h), (30, 30, 30))

    # Label bar
    draw = ImageDraw.Draw(composite)
    try:
        font = ImageFont.load_default(size=14)  # Pillow >= 10
    except TypeError:
        font = ImageFont.load_default()
    safe_label = label[:60]
    draw.text((6, 4), f"BEFORE  |  AFTER  — {safe_label}", fill=(220, 220, 220), font=font)

    # Paste images
    composite.paste(img_before, (0, bar_h))
    composite.paste(img_after, (img_before.width + gap, bar_h))

    composite.save(str(out_path), format="PNG")
    return {"composite": str(out_path)}


# ---------------------------------------------------------------------------
# Full-res after-shot + annotation helper
# ---------------------------------------------------------------------------

def _save_fullres_shot(
    after_bytes: bytes,
    out_path: pathlib.Path,
    click_nx: float | None = None,
    click_ny: float | None = None,
    img_width: int | None = None,
    img_height: int | None = None,
) -> dict:
    """Save a full-resolution after-shot, optionally annotating a click marker.

    The marker (circle/crosshair) is drawn at ``(click_nx * img_width,
    click_ny * img_height)`` when Pillow is available and coordinates are given.
    If Pillow is absent, the full-res PNG is still saved without annotation.

    Args:
        after_bytes:  PNG bytes of the after-shot (full virtual desktop).
        out_path:     Destination path for the full-res PNG.
        click_nx:     Normalized X of click position (0..1). None = no marker.
        click_ny:     Normalized Y of click position (0..1). None = no marker.
        img_width:    Screenshot width in pixels (needed to project click_nx).
        img_height:   Screenshot height in pixels.

    Returns:
        Dict with either ``{"fullres_annotated": str(path)}`` when the marker
        was drawn, or ``{"fullres": str(path)}`` when saved without annotation.
    """
    annotated = False
    data = after_bytes

    if click_nx is not None and click_ny is not None and img_width and img_height:
        try:
            from PIL import Image, ImageDraw  # noqa: F401 — lazy
            import io
            img = Image.open(io.BytesIO(after_bytes)).convert("RGB")
            draw = ImageDraw.Draw(img)
            px = int(round(click_nx * img.width))
            py = int(round(click_ny * img.height))
            r = 12  # circle radius
            # Red circle
            draw.ellipse([(px - r, py - r), (px + r, py + r)],
                         outline=(255, 0, 0), width=3)
            # Crosshair lines
            line_len = 20
            draw.line([(px - line_len, py), (px + line_len, py)],
                      fill=(255, 0, 0), width=2)
            draw.line([(px, py - line_len), (px, py + line_len)],
                      fill=(255, 0, 0), width=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            annotated = True
        except ImportError:
            pass  # Pillow absent — save without annotation

    out_path.write_bytes(data)
    key = "fullres_annotated" if annotated else "fullres"
    return {key: str(out_path)}


# ---------------------------------------------------------------------------
# Win32 window-rect helper (for capture --window)
# ---------------------------------------------------------------------------

def _find_window_hwnd(substr: str) -> int | None:
    """Find the HWND of a top-level window matching *substr* (case-insensitive).

    Uses the same whitespace-normalized substring convention as UiaWindowsFeed:
    both the query and the candidate title are collapsed to single spaces
    before comparison.

    Args:
        substr: Case-insensitive title substring to search for.

    Returns:
        HWND (int) of the first match, or ``None`` if nothing was found.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    import re as _re

    user32 = ctypes.windll.user32
    q_norm = _re.sub(r"\s+", " ", substr).strip().lower()
    found: list[int] = []

    _EnumCB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _enum_impl(hwnd: int, _lp: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title_norm = _re.sub(r"\s+", " ", buf.value).strip().lower()
        if q_norm in title_norm:
            found.append(hwnd)
        return True

    _cb = _EnumCB(_enum_impl)
    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def _hwnd_to_mss_region(hwnd: int) -> dict:
    """Convert an HWND bounding rect to an mss region dict.

    Calls ``GetWindowRect(hwnd)`` which returns physical pixel coordinates in
    the same DPI-aware frame as mss (provided DPI awareness was set at process
    start by LocalExecutor.__post_init__ or _set_dpi_awareness).

    Args:
        hwnd: Win32 window handle.

    Returns:
        Dict with keys ``left``, ``top``, ``width``, ``height`` suitable for
        ``mss.grab(region)``.

    Raises:
        OSError: if ``GetWindowRect`` fails (hwnd no longer valid, etc.).
    """
    import ctypes
    import ctypes.wintypes  # ensure wintypes is loaded (not auto-imported with ctypes)
    rect = ctypes.wintypes.RECT()
    ok = ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if not ok:
        raise OSError(f"GetWindowRect failed for hwnd={hwnd}")
    left = rect.left
    top = rect.top
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    return {"left": left, "top": top, "width": max(1, width), "height": max(1, height)}


# ---------------------------------------------------------------------------
# Foreground-window helper
# ---------------------------------------------------------------------------

def _get_foreground_title() -> str:
    """Return the title of the current foreground window (Windows-only)."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:  # noqa: BLE001
        return ""


def _should_activate(current_title: str, target: str, always: bool) -> bool:
    """Return True if activate_window should be called before the action.

    Args:
        current_title: Current foreground window title.
        target: Substring the target window title must contain.
        always: If True, always activate regardless of current state.
    """
    if always:
        return True
    return target.lower() not in current_title.lower()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _load_local_executor(monitor: int = 0):  # type: ignore[return]
    """Import and instantiate LocalExecutor (lazy)."""
    if sys.platform != "win32":
        _die("LocalExecutor is Windows-only; run on Windows.")
    try:
        from open_compute.drivers.local import LocalExecutor
    except ImportError as exc:
        _die(f"LocalExecutor not available: {exc}")
    return LocalExecutor(monitor_index=monitor)


def _parse_action(raw: str):  # type: ignore[return]
    """Parse a JSON string into a canonical Action."""
    from open_compute.actions import Action
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die(f"invalid action JSON: {exc}")
    # Accept both "type" and "action" as the key (the canonical schema uses "type",
    # but users writing Claude-style dicts may use "action").
    if "action" in data and "type" not in data:
        data["type"] = data.pop("action")
    try:
        return Action(**data)
    except (TypeError, ValueError) as exc:
        _die(f"invalid action: {exc}")


def _parse_actions(raw: str) -> list:
    """Parse a JSON string into a list of canonical Actions.

    Accepts either a single action object OR a JSON array of action objects.
    Always returns a list.
    """
    from open_compute.actions import Action
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die(f"invalid action JSON: {exc}")

    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
        _die("action JSON must be an object or an array of objects")

    actions = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            _die(f"action at index {i} is not an object")
        if "action" in item and "type" not in item:
            item = dict(item)
            item["type"] = item.pop("action")
        try:
            actions.append(Action(**item))
        except (TypeError, ValueError) as exc:
            _die(f"invalid action at index {i}: {exc}")
    return actions


# ---------------------------------------------------------------------------
# sub-commands
# ---------------------------------------------------------------------------

def cmd_capture(args: list[str]) -> None:
    """oc capture [--out PATH] [--monitor N] [--window SUBSTR]

    IMPORTANT — coordinate consistency:
    ``oc do`` always dispatches into the **virtual desktop** coordinate space
    (monitor_index=0).  Capture must therefore also use monitor_index=0 so
    that the agent's (nx, ny) fraction maps to the same physical pixel.
    Using ``--monitor 1`` or higher gives the agent a fraction relative to
    *that* monitor, but ``oc do`` will still project it over the full virtual
    desktop — clicks will land in the wrong place on multi-monitor setups.
    Default (monitor 0 = virtual desktop) is always safe.  Non-zero is
    intentionally kept for diagnostic use; a warning is printed.

    --window SUBSTR captures only the bounding rect of the named window
    (Win32 GetWindowRect; case-insensitive substring, whitespace-normalized).
    Mutually exclusive with --monitor on Windows.
    """
    import argparse
    p = argparse.ArgumentParser(prog="oc capture", description="Capture a screenshot.")
    p.add_argument(
        "--out", "-o", default=None,
        help=(
            "Output PNG path. Default: auto-sequenced file in _session/ "
            "(module root, gitignored). Override the directory with OC_SESSION_DIR."
        ),
    )
    p.add_argument("--monitor", "-m", type=int, default=0,
                   help=(
                       "Monitor index: 0=virtual desktop (default, recommended). "
                       "Non-zero values are for diagnostics only — coordinates "
                       "computed from a single-monitor capture are NOT compatible "
                       "with 'oc do' on a multi-monitor setup."
                   ))
    p.add_argument(
        "--window", "-w", default=None, metavar="SUBSTR",
        help=(
            "Capture only the bounding rect of the window whose title contains "
            "SUBSTR (case-insensitive, whitespace-normalized). Windows-only. "
            "Error if no matching window is found."
        ),
    )
    ns = p.parse_args(args)

    if ns.window is not None:
        # Window-rect capture path (Windows only)
        if sys.platform != "win32":
            _die("--window is Windows-only.")
        # Ensure DPI awareness so GetWindowRect and mss share the same coordinate
        # frame (LocalExecutor sets this in __post_init__, but cmd_capture calls
        # Win32 helpers directly, so we set it explicitly here).
        try:
            from open_compute.drivers.local import _set_dpi_awareness
            _set_dpi_awareness()
        except Exception:
            pass
        hwnd = _find_window_hwnd(ns.window)
        if hwnd is None:
            _die(
                f"No window found matching {ns.window!r}. "
                "Check the title substring or omit --window for a full desktop capture."
            )
        region = _hwnd_to_mss_region(hwnd)
        # Use mss directly to capture the window rect
        try:
            import mss
            import mss.tools
        except ImportError as exc:
            _die(f"Screenshot requires the 'mss' package: {exc}")

        with mss.mss() as sct:
            shot = sct.grab(region)
            png_bytes: bytes = mss.tools.to_png(shot.rgb, shot.size)
            w, h = shot.width, shot.height

        if ns.out is not None:
            out_path = pathlib.Path(ns.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_path = _next_session_path(label="window")
        out_path.write_bytes(png_bytes)
        if ns.out is None:
            _rotate_session()
        result = {
            "path": str(out_path.resolve()),
            "width": w,
            "height": h,
            "window": ns.window,
            "region": region,
        }
        print(json.dumps(result))
        return

    # Standard full-desktop / monitor capture
    if ns.monitor != 0:
        print(
            f"WARNING: --monitor {ns.monitor} captures only one monitor. "
            "'oc do' always targets the virtual desktop (monitor 0). "
            "Coordinates from this capture will be misaligned on multi-monitor setups.",
            file=sys.stderr,
        )

    executor = _load_local_executor(monitor=ns.monitor)
    obs = executor.screenshot()

    if ns.out is not None:
        out_path = pathlib.Path(ns.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_path = _next_session_path()

    out_path.write_bytes(obs.screenshot)

    if ns.out is None:
        # Rotate AFTER writing the new file so the on-disk count settles at
        # exactly OC_SESSION_KEEP (matching cmd_do's write-then-rotate order).
        _rotate_session()

    result = {"path": str(out_path.resolve()), "width": obs.width, "height": obs.height}
    print(json.dumps(result))


def cmd_do(args: list[str]) -> None:
    """oc do '<json-action-or-array>' [--mode MODE] [--yes] [--label NAME]
             [--shots each] [--ensure-foreground SUBSTR]
    """
    import argparse
    p = argparse.ArgumentParser(
        prog="oc do",
        description="Execute one canonical action (or a batch/array) through the safety gate.",
    )
    p.add_argument(
        "action_json",
        help=(
            "JSON action object, e.g. '{\"type\":\"mouse_move\",\"x\":0.5,\"y\":0.5}', "
            "or a JSON array of action objects for batch execution."
        ),
    )
    p.add_argument(
        "--mode",
        default=os.environ.get("OC_SAFETY_MODE", "confirm"),
        choices=("allow_all", "confirm", "read_only"),
        help="Safety mode (default: confirm or $OC_SAFETY_MODE)",
    )
    p.add_argument(
        "--yes", "-y", action="store_true",
        help="Pre-approve the action (agent has already decided; no TTY prompt).",
    )
    p.add_argument(
        "--label", "-l", default=None,
        help=(
            "Action label: triggers auto Before|After screenshot composite saved "
            "to _session/<seq>_<label>.png. Without Pillow, saves two separate "
            "files and returns both paths."
        ),
    )
    p.add_argument(
        "--shots", default=None, choices=("each",),
        help=(
            "Screenshot strategy for batch: 'each' = one composite per action. "
            "Default (omitted): one composite at end of batch (requires --label)."
        ),
    )
    p.add_argument(
        "--ensure-foreground", default=None, metavar="SUBSTR",
        help=(
            "Before executing, check that the foreground window title contains SUBSTR. "
            "If not (or if OC_ALWAYS_FOREGROUND=1), call activate_window(SUBSTR) first."
        ),
    )
    p.add_argument(
        "--fullres", action="store_true",
        help=(
            "Save an additional full-resolution after-shot alongside the composite. "
            "Annotated with a click-coordinate marker (circle/crosshair) when Pillow "
            "is available. Path returned as 'fullres' or 'fullres_annotated' in JSON."
        ),
    )
    ns = p.parse_args(args)

    from open_compute.actions import Action
    from open_compute.safety import Decision, SafetyPolicy

    actions = _parse_actions(ns.action_json)
    is_batch = len(actions) > 1 or (
        # Original input was an array (even with 1 element) — detect via JSON parse
        ns.action_json.strip().startswith("[")
    )

    # Build safety policy
    if ns.yes:
        policy = SafetyPolicy(mode="allow_all")
    else:
        policy = SafetyPolicy(mode=ns.mode)

    # Foreground config
    always_fg = bool(os.environ.get("OC_ALWAYS_FOREGROUND", ""))
    ensure_fg: str | None = ns.ensure_foreground

    # -----------------------------------------------------------------------
    # Single-action path (backwards-compatible: no --label, single object JSON)
    # -----------------------------------------------------------------------
    if not is_batch and ns.label is None:
        action = actions[0]
        result = policy.evaluate(action)

        if result.decision is Decision.DENY:
            print(json.dumps({"result": "deny", "reason": result.reason}))
            sys.exit(1)

        if result.decision is Decision.CONFIRM:
            print(json.dumps({
                "result": "confirm",
                "reason": result.reason,
                "action": action.type.value,
                "hint": "Re-run with --yes to approve, or ask the user.",
            }))
            sys.exit(1)

        # ALLOW: optionally activate foreground, then execute
        executor = _load_local_executor()
        if ensure_fg:
            current = _get_foreground_title()
            if _should_activate(current, ensure_fg, always_fg):
                executor.activate_window(ensure_fg)
        obs = executor.execute(action)

        resp: dict = {
            "result": "executed",
            "action": action.type.value,
            "width": obs.width,
            "height": obs.height,
        }

        # --fullres: save full-res after-shot (+ annotate click position if applicable)
        if ns.fullres:
            from open_compute.actions import ActionType
            click_nx: float | None = action.x if action.type in (
                ActionType.LEFT_CLICK, ActionType.RIGHT_CLICK,
                ActionType.MIDDLE_CLICK, ActionType.DOUBLE_CLICK,
                ActionType.TRIPLE_CLICK,
            ) else None
            click_ny: float | None = action.y if click_nx is not None else None
            fr_path = _next_session_path(label="fullres")
            fr_info = _save_fullres_shot(
                obs.screenshot, fr_path,
                click_nx=click_nx, click_ny=click_ny,
                img_width=obs.width, img_height=obs.height,
            )
            resp.update(fr_info)
            _rotate_session()

        print(json.dumps(resp))
        return

    # -----------------------------------------------------------------------
    # Single-action WITH --label  OR  batch path
    # -----------------------------------------------------------------------
    executor = _load_local_executor()

    # Foreground check once, before first action
    if ensure_fg:
        current = _get_foreground_title()
        if _should_activate(current, ensure_fg, always_fg):
            executor.activate_window(ensure_fg)

    executed_count = 0
    per_step_composites: list[str] = []
    final_obs = None

    # Capture pre-batch screenshot once when a final composite is requested.
    # Must happen BEFORE the loop so the "before" half is genuinely pre-batch.
    pre_batch_bytes: bytes | None = None
    if is_batch and ns.label is not None and ns.shots != "each":
        pre_batch_bytes = executor.screenshot().screenshot

    for i, action in enumerate(actions):
        result = policy.evaluate(action)

        if result.decision is Decision.DENY:
            print(json.dumps({
                "result": "deny",
                "reason": result.reason,
                "action_index": i,
                "action": action.type.value,
                "executed_before": executed_count,
            }))
            sys.exit(1)

        if result.decision is Decision.CONFIRM:
            print(json.dumps({
                "result": "confirm",
                "reason": result.reason,
                "action_index": i,
                "action": action.type.value,
                "hint": "Re-run with --yes to approve, or ask the user.",
                "executed_before": executed_count,
            }))
            sys.exit(1)

        # ALLOW — optionally capture before (single-action or per-step batch)
        if ns.label is not None and (ns.shots == "each" or not is_batch):
            obs_before = executor.screenshot()
            before_bytes = obs_before.screenshot
        else:
            before_bytes = None

        obs_after = executor.execute(action)
        final_obs = obs_after
        executed_count += 1

        # Per-step composite (--shots each)
        if ns.shots == "each" and ns.label is not None and before_bytes is not None:
            step_label = f"{ns.label}_step{i + 1:02d}"
            out_path = _next_session_path(step_label)
            info = _compose_before_after(before_bytes, obs_after.screenshot, step_label, out_path)
            per_step_composites.append(
                info.get("composite") or info.get("before", "")
            )
            _rotate_session()

    # Final composite (for batch without --shots each, or labeled single action)
    final_composite_info: dict = {}
    if ns.label is not None and ns.shots != "each":
        if not is_batch:
            # Single-action: before_bytes was captured inside the loop above
            if before_bytes is not None and final_obs is not None:
                out_path = _next_session_path(ns.label)
                final_composite_info = _compose_before_after(
                    before_bytes, final_obs.screenshot, ns.label, out_path
                )
                _rotate_session()
        else:
            # Batch with --label: pre_batch_bytes (captured before loop) vs.
            # final_obs (after last action) — a genuine before|after composite.
            if pre_batch_bytes is not None and final_obs is not None:
                out_path = _next_session_path(ns.label)
                final_composite_info = _compose_before_after(
                    pre_batch_bytes, final_obs.screenshot, ns.label, out_path
                )
                _rotate_session()

    # Build response
    if not is_batch:
        # Single action with --label
        action = actions[0]
        resp_batch: dict = {
            "result": "executed",
            "action": action.type.value,
            "width": final_obs.width if final_obs else 0,
            "height": final_obs.height if final_obs else 0,
        }
        resp_batch.update(final_composite_info)
    else:
        resp_batch = {
            "result": "batch",
            "count": executed_count,
            "width": final_obs.width if final_obs else 0,
            "height": final_obs.height if final_obs else 0,
        }
        if per_step_composites:
            resp_batch["composites"] = per_step_composites
        resp_batch.update(final_composite_info)

    # --fullres: save full-res after-shot of last action
    if ns.fullres and final_obs is not None:
        from open_compute.actions import ActionType
        last_action = actions[-1] if actions else None
        click_nx_b: float | None = None
        click_ny_b: float | None = None
        if last_action is not None and last_action.type in (
            ActionType.LEFT_CLICK, ActionType.RIGHT_CLICK,
            ActionType.MIDDLE_CLICK, ActionType.DOUBLE_CLICK,
            ActionType.TRIPLE_CLICK,
        ):
            click_nx_b = last_action.x
            click_ny_b = last_action.y
        fr_label = (ns.label or "fullres") + "_fullres"
        fr_path = _next_session_path(label=fr_label)
        fr_info = _save_fullres_shot(
            final_obs.screenshot, fr_path,
            click_nx=click_nx_b, click_ny=click_ny_b,
            img_width=final_obs.width, img_height=final_obs.height,
        )
        resp_batch.update(fr_info)
        _rotate_session()

    print(json.dumps(resp_batch))


def cmd_run(args: list[str]) -> None:
    """oc run "<goal>" --backend claude|openai [--max-steps N] [--model ID]
              [--ensure-foreground SUBSTR]
    """
    import argparse
    p = argparse.ArgumentParser(
        prog="oc run",
        description="Run the autonomous AgentLoop with a real API backend.",
    )
    p.add_argument("goal", help="Goal for the agent.")
    p.add_argument(
        "--backend", "-b", required=True, choices=("claude", "openai"),
        help="Backend: claude (needs ANTHROPIC_API_KEY) or openai [UNSICHER] (needs OPENAI_API_KEY).",
    )
    p.add_argument("--max-steps", "-n", type=int, default=20, help="Max agent-loop steps.")
    p.add_argument("--model", "-m", default=None, help="Override model ID.")
    p.add_argument(
        "--safety", default="confirm", choices=("allow_all", "confirm", "read_only"),
        help="Safety mode (default: confirm — prompts before risky actions).",
    )
    p.add_argument(
        "--ensure-foreground", default=None, metavar="SUBSTR",
        help=(
            "Before starting the loop, check that the foreground window title "
            "contains SUBSTR. If not (or if OC_ALWAYS_FOREGROUND=1), call "
            "activate_window(SUBSTR) first."
        ),
    )
    ns = p.parse_args(args)

    if ns.backend == "openai":
        print(
            "WARNING: The OpenAI backend is marked [UNSICHER] -- the model name and "
            "Responses-API request shape are not fully verified. Use at your own risk.",
            file=sys.stderr,
        )

    from open_compute.backends.factory import get_backend
    from open_compute.config import Config
    from open_compute.loop import AgentLoop
    from open_compute.safety import SafetyPolicy

    executor = _load_local_executor()

    # Foreground check (once, pre-loop)
    ensure_fg: str | None = ns.ensure_foreground
    always_fg = bool(os.environ.get("OC_ALWAYS_FOREGROUND", ""))
    if ensure_fg:
        current = _get_foreground_title()
        if _should_activate(current, ensure_fg, always_fg):
            executor.activate_window(ensure_fg)

    config = Config(
        backend=ns.backend,
        scope="os",
        model=ns.model,
        display_width=executor.width,
        display_height=executor.height,
        safety_mode=ns.safety,
        max_steps=ns.max_steps,
    )

    kwargs: dict = {}
    if ns.model:
        kwargs["model"] = ns.model

    backend = get_backend(ns.backend, executor.width, executor.height, **kwargs)

    # In confirm mode, ask the user interactively (TTY assumed for `oc run`).
    if ns.safety == "confirm":
        def _confirm(action) -> bool:  # type: ignore[override]
            answer = input(f"\nSafety: run {action.type.value!r}? [y/N] ").strip().lower()
            return answer in ("y", "yes")
        policy = SafetyPolicy(mode="confirm", confirm_callback=_confirm)
    else:
        policy = SafetyPolicy(mode=ns.safety)

    loop = AgentLoop(config, backend=backend, executor=executor, policy=policy)

    print(f"Running: {ns.goal!r} via {ns.backend!r} backend, max_steps={ns.max_steps}")
    result = loop.run(ns.goal)

    print(json.dumps({
        "done": result.done,
        "steps": result.steps,
        "traces": len(result.traces),
    }))


# ---------------------------------------------------------------------------
# UIA sub-commands (Phase 2a)
# ---------------------------------------------------------------------------

def _load_uia_feed(window: str | None = None, max_depth: int | None = None, max_elem: int | None = None):
    """Import and return a UiaWindowsFeed, with a clear error when unavailable."""
    if sys.platform != "win32":
        _die("UIA feed is Windows-only.")
    try:
        from open_compute.feeds.uia_windows import UiaWindowsFeed
    except ImportError as exc:
        _die(f"UIA feed not available: {exc}")
    kwargs: dict = {}
    if max_depth is not None:
        kwargs["max_depth"] = max_depth
    if max_elem is not None:
        kwargs["max_elem"] = max_elem
    feed = UiaWindowsFeed(**kwargs)
    if not feed.available():
        _die(
            "UIA feed not available — install uiautomation with:\n"
            "  pip install open-compute[uia]"
        )
    return feed


def cmd_tree(args: list[str]) -> None:
    """oc tree [--window SUBSTR] [--max N]

    Walk the UIA accessibility tree of the target window and print a JSON
    array of elements (name / role / center_norm / invokable).

    All coordinates in center_norm are 0..1 relative to the virtual desktop,
    consistent with 'oc do'.
    """
    import argparse
    p = argparse.ArgumentParser(
        prog="oc tree",
        description=(
            "Walk the UIA element tree of a window and print JSON elements."
        ),
    )
    p.add_argument(
        "--window", "-w", default=None, metavar="SUBSTR",
        help="Target window title substring. Default: foreground window.",
    )
    p.add_argument(
        "--max", "-n", type=int, default=None, dest="max_elem",
        help="Maximum number of elements to return (default: 200 / OC_UIA_MAX_ELEM).",
    )
    p.add_argument(
        "--depth", type=int, default=None,
        help="Maximum UIA tree depth (default: 8 / OC_UIA_MAX_DEPTH).",
    )
    ns = p.parse_args(args)

    feed = _load_uia_feed(window=ns.window, max_depth=ns.depth, max_elem=ns.max_elem)
    try:
        obs = feed.observe(window=ns.window)
    except RuntimeError as exc:
        _die(str(exc))

    # Enrich each element with center_norm so callers can click directly
    try:
        from open_compute.feeds.uia_windows import _get_virtual_desktop, _rect_to_center_norm
        virt = _get_virtual_desktop()

        class _R:
            def __init__(self, x, y, w, h):
                self.x, self.y, self.width, self.height = x, y, w, h

        output = []
        for elem in obs.elements:
            rx, ry, rw, rh = elem["rect_px"]
            nx, ny = _rect_to_center_norm(_R(rx, ry, rw, rh), *virt)
            output.append({
                "name": elem["name"],
                "role": elem["role"],
                "value": elem.get("value", ""),
                "rect_px": elem["rect_px"],
                "center_norm": [round(nx, 5), round(ny, 5)],
                "invokable": False,  # cheaply set; use oc invoke to actually invoke
                "visible": elem.get("visible", True),
                "depth": elem.get("depth", 0),
            })
    except RuntimeError:
        raise  # window-not-found already died above; re-raise unexpected RuntimeError
    except Exception:
        # Non-RuntimeError (e.g. COM glitch in coord enrichment): fallback to raw elements
        output = obs.elements

    print(json.dumps(output, ensure_ascii=False))


def cmd_click_name(args: list[str]) -> None:
    """oc click-name "<query>" [--window SUBSTR]

    Resolve a UI element by name via UIA, then click at its center_norm
    coordinate using LocalExecutor + Safety gate.

    All coordinates are 0..1 (consistent with 'oc do').
    """
    import argparse
    p = argparse.ArgumentParser(
        prog="oc click-name",
        description=(
            "Resolve a UI element by name (UIA) and click its center. "
            "Uses Safety gate; pass --yes to pre-approve."
        ),
    )
    p.add_argument("query", help="Element name to resolve (case-insensitive).")
    p.add_argument(
        "--window", "-w", default=None, metavar="SUBSTR",
        help="Target window title substring.",
    )
    p.add_argument(
        "--mode",
        default=os.environ.get("OC_SAFETY_MODE", "confirm"),
        choices=("allow_all", "confirm", "read_only"),
        help="Safety mode (default: confirm or $OC_SAFETY_MODE).",
    )
    p.add_argument("--yes", "-y", action="store_true", help="Pre-approve the click.")
    p.add_argument(
        "--ensure-foreground", default=None, metavar="SUBSTR",
        help="Activate this window before clicking (same as 'oc do --ensure-foreground').",
    )
    p.add_argument(
        "--fullres", action="store_true",
        help=(
            "Save an additional full-resolution after-shot. Annotated with a "
            "click-coordinate marker when Pillow is available."
        ),
    )
    ns = p.parse_args(args)

    feed = _load_uia_feed()
    try:
        target = feed.resolve(ns.query, window=ns.window)
    except RuntimeError as exc:
        _die(str(exc))
    if target is None:
        _die(f"No element found matching {ns.query!r}")

    nx, ny = target.center_norm

    from open_compute.actions import Action, ActionType
    from open_compute.safety import Decision, SafetyPolicy

    action = Action(type=ActionType.LEFT_CLICK, x=nx, y=ny)
    policy = SafetyPolicy(mode="allow_all" if ns.yes else ns.mode)
    result = policy.evaluate(action)

    if result.decision is Decision.DENY:
        print(json.dumps({"result": "deny", "reason": result.reason}))
        sys.exit(1)
    if result.decision is Decision.CONFIRM:
        print(json.dumps({
            "result": "confirm",
            "reason": result.reason,
            "hint": "Re-run with --yes to approve.",
            "target": target.name,
            "center_norm": list(target.center_norm),
        }))
        sys.exit(1)

    executor = _load_local_executor()
    ensure_fg = ns.ensure_foreground
    always_fg = bool(os.environ.get("OC_ALWAYS_FOREGROUND", ""))
    if ensure_fg:
        current = _get_foreground_title()
        if _should_activate(current, ensure_fg, always_fg):
            executor.activate_window(ensure_fg)

    obs = executor.execute(action)
    cn_resp: dict = {
        "result": "executed",
        "action": "left_click",
        "target": target.name,
        "role": target.role,
        "center_norm": list(target.center_norm),
        "rect_px": list(target.rect_px),
        "width": obs.width,
        "height": obs.height,
    }

    if ns.fullres:
        fr_path = _next_session_path(label="click_name_fullres")
        fr_info = _save_fullres_shot(
            obs.screenshot, fr_path,
            click_nx=nx, click_ny=ny,
            img_width=obs.width, img_height=obs.height,
        )
        cn_resp.update(fr_info)
        _rotate_session()

    print(json.dumps(cn_resp))


def cmd_invoke(args: list[str]) -> None:
    """oc invoke "<query>" [--window SUBSTR]

    Click-free invocation of a UI element via UIA patterns (InvokePattern,
    TogglePattern, SelectionItemPattern, LegacyIAccessible.DoDefaultAction).

    No mouse movement; works even when the window is not fully in the
    foreground (for most native apps). Safety gate applies.
    """
    import argparse
    p = argparse.ArgumentParser(
        prog="oc invoke",
        description=(
            "Click-free invocation of a UI element via UIA patterns. "
            "Safety gate applies; pass --yes to pre-approve."
        ),
    )
    p.add_argument("query", help="Element name to invoke (case-insensitive).")
    p.add_argument(
        "--window", "-w", default=None, metavar="SUBSTR",
        help="Target window title substring.",
    )
    p.add_argument(
        "--mode",
        default=os.environ.get("OC_SAFETY_MODE", "confirm"),
        choices=("allow_all", "confirm", "read_only"),
        help="Safety mode (default: confirm or $OC_SAFETY_MODE).",
    )
    p.add_argument("--yes", "-y", action="store_true", help="Pre-approve the invocation.")
    ns = p.parse_args(args)

    # Safety gate: treat invoke as a left_click for policy evaluation
    from open_compute.actions import Action, ActionType
    from open_compute.safety import Decision, SafetyPolicy

    feed = _load_uia_feed()
    try:
        target = feed.resolve(ns.query, window=ns.window)
    except RuntimeError as exc:
        _die(str(exc))
    if target is None:
        _die(f"No element found matching {ns.query!r}")

    nx, ny = target.center_norm
    action = Action(type=ActionType.LEFT_CLICK, x=nx, y=ny)
    policy = SafetyPolicy(mode="allow_all" if ns.yes else ns.mode)
    result = policy.evaluate(action)

    if result.decision is Decision.DENY:
        print(json.dumps({"result": "deny", "reason": result.reason}))
        sys.exit(1)
    if result.decision is Decision.CONFIRM:
        print(json.dumps({
            "result": "confirm",
            "reason": result.reason,
            "hint": "Re-run with --yes to approve.",
            "target": target.name,
            "center_norm": list(target.center_norm),
        }))
        sys.exit(1)

    # ALLOW: attempt click-free invoke
    ok = feed.invoke(ns.query, window=ns.window)
    print(json.dumps({
        "result": "invoked" if ok else "invoke_failed",
        "target": target.name,
        "role": target.role,
        "center_norm": list(target.center_norm),
        "rect_px": list(target.rect_px),
        "invokable": target.invokable,
    }))
    if not ok:
        sys.exit(2)


# ---------------------------------------------------------------------------
# Push sub-command (Feed-Manager status / one-shot inject cycle)
# ---------------------------------------------------------------------------

def cmd_push(args: list[str]) -> None:
    """oc push --status | --once [--window SUBSTR]

    --status: Print FeedManager status as JSON (read-only, no inject).
    --once:   Run exactly one inject cycle and print the summary as JSON.
              Uses LocalFileInjector (local file sink, no BACH/API).
              No permanent daemon; safe to call in tests.
    """
    import argparse
    p = argparse.ArgumentParser(
        prog="oc push",
        description="Feed-Manager: status check or one-shot inject cycle.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--status", action="store_true",
        help="Print FeedManager status (feeds, dosage modes, push counts, sink).",
    )
    group.add_argument(
        "--once", action="store_true",
        help=(
            "Run exactly one inject cycle. Uses the LocalFileInjector sink. "
            "No daemon, no live interaction in tests."
        ),
    )
    p.add_argument(
        "--window", "-w", default=None, metavar="SUBSTR",
        help="Optional window hint forwarded to feeds during --once cycle.",
    )
    ns = p.parse_args(args)

    from open_compute.feed_manager import FeedManager, LocalFileInjector

    mgr = FeedManager(sink=LocalFileInjector())

    if ns.status:
        print(json.dumps(mgr.status(), ensure_ascii=False, default=str))
        return

    # --once: run exactly one cycle
    summary = mgr.cycle(window=ns.window)
    print(json.dumps(summary, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Directory-watch sub-command
# ---------------------------------------------------------------------------

def cmd_watch_dir(args: list[str]) -> None:
    """oc watch-dir <path> [<path>...] [--for SECS] [--once]

    Monitor one or more directories for file-system changes and print events
    as a JSON array (newest first).

    Modes:
    - ``--for SECS``: collect events for SECS seconds (background observer),
      then print and exit.
    - ``--once``:     one-time snapshot diff; no background observer. Compares
                      current directory state to the previous scan. On first
                      run, emits an empty list (no baseline yet to diff against).

    Without any mode flag, collects events until Ctrl-C and prints on exit.
    """
    import argparse
    import time as _time_mod
    p = argparse.ArgumentParser(
        prog="oc watch-dir",
        description="Monitor directories for file-system changes and emit JSON events.",
    )
    p.add_argument(
        "paths", nargs="+", metavar="PATH",
        help="One or more directories to watch.",
    )
    p.add_argument(
        "--for", dest="duration", type=float, default=None, metavar="SECS",
        help="Collect events for this many seconds, then exit.",
    )
    p.add_argument(
        "--once", action="store_true",
        help=(
            "One-time snapshot diff (no background observer). "
            "Prints events since last scan, or empty list on first run."
        ),
    )
    ns = p.parse_args(args)

    # Validate paths
    for pth in ns.paths:
        if not os.path.isdir(pth):
            _die(f"Not a directory (or does not exist): {pth!r}")

    from open_compute.feeds.dirwatch import DirwatchFeed

    feed = DirwatchFeed()

    if ns.once:
        # Snapshot diff mode: load baseline from _session/dirwatch_snapshot.json.
        # Baselines are keyed by a canonical path-set string so that watching
        # different directories in separate ``--once`` calls never cross-contaminates.
        import json as _json

        abs_paths = sorted(str(pathlib.Path(p).resolve()) for p in ns.paths)
        pathset_key = "|".join(abs_paths)  # e.g. "/tmp/a|/tmp/b"

        session_d = _session_dir()
        snap_file = session_d / "dirwatch_snapshot.json"

        # Load the full store; find baseline for this specific path-set.
        store: dict = {}
        if snap_file.exists():
            try:
                store = _json.loads(snap_file.read_text(encoding="utf-8"))
            except Exception:
                store = {}
        baseline: dict | None = store.get(pathset_key)

        events, new_snap = feed.snapshot_diff(ns.paths, baseline)

        # Persist updated snapshot for this path-set (leave other keys intact).
        try:
            store[pathset_key] = new_snap
            snap_file.write_text(_json.dumps(store), encoding="utf-8")
        except Exception:
            pass

        print(json.dumps(events, ensure_ascii=False))
        return

    # Duration or indefinite mode (background observer)
    feed.start(ns.paths)
    try:
        if ns.duration is not None:
            _time_mod.sleep(ns.duration)
        else:
            # Run until Ctrl-C
            while True:
                _time_mod.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        obs = feed.observe()
        feed.stop()

    print(json.dumps(obs.elements, ensure_ascii=False))


# ---------------------------------------------------------------------------
# clirec sub-commands  (Task 8)
# ---------------------------------------------------------------------------

def _run_replay(path: str, params: dict, executor):
    """Testable seam: load a .clirec and replay it against *executor*.

    Args:
        path:     Path to the ``.clirec`` file to replay.
        params:   Template parameter dict (e.g. ``{"msg": "hello"}``).
        executor: Any object satisfying the Executor protocol
                  (``execute(action) -> Observation``).  Injected by tests
                  to avoid requiring a real Windows driver.

    Returns:
        ``ReplayReport`` from :func:`open_compute.clirec.replay.replay`.
    """
    from .clirec.format import read
    from .clirec.replay import replay
    rec = read(path)
    return replay(rec, executor, params=params or None)


def cmd_rec(args: list[str]) -> None:
    """oc rec validate|list|replay|start|stop|buffer ..."""
    if not args:
        _die("usage: oc rec validate|list|replay|start|stop|buffer ...")
    sub, rest = args[0], args[1:]

    if sub == "validate":
        if not rest:
            _die("usage: oc rec validate <file.clirec>")
        from .clirec.format import validate
        with open(rest[0], "r", encoding="utf-8") as fh:
            problems = validate(fh.read())
        print("OK" if not problems else "\n".join(problems))
        return

    if sub == "list":
        d = "recordings"
        if "--dir" in rest:
            idx = rest.index("--dir")
            if idx + 1 >= len(rest):
                _die("usage: oc rec list [--dir DIR]")
            d = rest[idx + 1]
        if not os.path.isdir(d):
            print(f"(no recordings dir: {d})")
            return
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".clirec"):
                print(fn)
        return

    if sub == "replay":
        if not rest:
            _die("usage: oc rec replay <file.clirec> [--param k=v ...]")
        path = rest[0]
        params: dict[str, str] = {}
        i = 1
        while i < len(rest):
            if rest[i] == "--param" and i + 1 < len(rest):
                k, _, v = rest[i + 1].partition("=")
                params[k] = v
                i += 2
            else:
                i += 1
        from .drivers.local import LocalExecutor  # real Windows executor
        ex = LocalExecutor()
        rep = _run_replay(path, params, ex)
        print(f"replay: total={rep.total} ok={rep.ok} fallbacks={rep.fallbacks} "
              f"failures={len(rep.failures)}")
        for f in rep.failures:
            print("  FAIL", f)
        return

    if sub in ("start", "stop", "buffer"):
        _rec_live(sub, rest)
        return

    _die(f"unknown rec subcommand {sub!r}")


def _rec_live(sub: str, rest: list[str]) -> None:
    """Thin live-recording loop (not unit-tested). Drives Recorder.pump()."""
    import time
    from .config import Config, clirec_recorder_config
    from .clirec.capture.base import get_backend
    from .clirec.recorder import Recorder
    from .clirec.uia_probe import DefaultProbe
    cfg = Config()
    rc = clirec_recorder_config(cfg)
    backend = get_backend()
    rec = Recorder(backend, config=rc, probe=DefaultProbe())
    if sub == "start":
        name = rest[0] if rest else "recording"
        print(f"recording '{name}' — press Ctrl+C to stop")
        rec.start(name)
        try:
            while True:
                rec.pump()
                time.sleep(0.05)
        except KeyboardInterrupt:
            out = rec.stop()
            path = rec.save(out, name)
            print(f"\nsaved: {path} ({len(out.steps)} steps)")
    else:
        print("note: 'stop'/'buffer' require a running daemon session; "
              "use 'oc rec start <name>' (Ctrl+C to stop) for the MVP.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Dispatch to sub-commands: capture | do | run | tree | click-name | invoke | push | watch-dir | rec."""
    if len(sys.argv) < 2:
        print(textwrap.dedent("""\
            Usage:
              oc capture [--out PATH] [--monitor N] [--window SUBSTR]
              oc do '<json-action-or-array>' [--mode allow_all|confirm|read_only] [--yes]
                     [--label NAME] [--shots each] [--ensure-foreground SUBSTR] [--fullres]
              oc run "<goal>" --backend claude|openai [--max-steps N]
                     [--ensure-foreground SUBSTR]
              oc tree [--window SUBSTR] [--max N] [--depth N]
              oc click-name "<query>" [--window SUBSTR] [--mode MODE] [--yes]
                     [--ensure-foreground SUBSTR] [--fullres]
              oc invoke "<query>" [--window SUBSTR] [--mode MODE] [--yes]
              oc push --status | --once [--window SUBSTR]
              oc watch-dir <path> [<path>...] [--for SECS] [--once]
              oc rec validate <file.clirec> | list [--dir DIR]
              oc rec replay <file.clirec> [--param k=v ...]
              oc rec start <name>   (Ctrl+C to stop & save)
            """))
        sys.exit(0)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "capture":
        cmd_capture(rest)
    elif cmd == "do":
        cmd_do(rest)
    elif cmd == "run":
        cmd_run(rest)
    elif cmd == "tree":
        cmd_tree(rest)
    elif cmd == "click-name":
        cmd_click_name(rest)
    elif cmd == "invoke":
        cmd_invoke(rest)
    elif cmd == "push":
        cmd_push(rest)
    elif cmd == "watch-dir":
        cmd_watch_dir(rest)
    elif cmd == "rec":
        cmd_rec(rest)
    else:
        _die(f"unknown command {cmd!r}; expected capture | do | run | tree | click-name | invoke | push | watch-dir | rec")


if __name__ == "__main__":
    main()
