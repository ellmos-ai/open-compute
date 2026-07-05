"""MCP server for open-compute — the keyless Mode-A loop, exposed natively.

This module wraps the existing open-compute core (canonical action schema, safety
gate, LocalExecutor, UIA / dirwatch feeds) as a `Model Context Protocol
<https://modelcontextprotocol.io>`_ server built on **FastMCP**. The **MCP client
is the reasoner** — no API key, model-agnostic — exactly like Mode A in
``SKILL.md``/``cli.py``, but as native tool-calls instead of Bash + a separate
Read of a PNG file.

Why a server (over Mode A CLI):

* **Process persistence.** One warm :class:`LocalExecutor` is kept resident for
  the whole server lifetime (DPI-awareness set once), instead of a fresh Python
  process per ``oc do``. This is the ``TODO.md`` "Prozess-Persistenz" item.
* **Native image return.** ``capture`` returns the screenshot as an MCP image
  block (via :class:`mcp.server.fastmcp.Image`) — no loose ``_session/`` file,
  one round-trip less per step.
* **Safety = the client's tool-approval UX.** State-changing tools run through
  the same :class:`~open_compute.safety.SafetyPolicy`; the default ``confirm``
  mode returns a structured ``needs_confirmation`` result instead of a TTY prompt.

Coordinates are **normalized 0..1** everywhere (the client estimates them from
the returned image), consistent with the CLI. Windows-only for real capture /
input (LocalExecutor + UIA need the interactive desktop session).

Import-light: only ``mcp``, :mod:`open_compute.actions` and
:mod:`open_compute.safety` (both stdlib-only) are imported at module load.
``LocalExecutor`` (mss), UIA and dirwatch are imported lazily inside the tools,
so the server imports and lists its tools on any platform without extras.

Run:  ``open-compute-mcp``  or  ``python -m open_compute.mcp_server``
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from . import mcp_i18n
from .actions import Action, ActionType
from .safety import Decision, SafetyPolicy

_LANG = mcp_i18n.current_language()

mcp = FastMCP("open-compute", instructions=mcp_i18n.instructions(_LANG))


# ---------------------------------------------------------------------------
# Server state — one warm executor for the whole process (persistence win)
# ---------------------------------------------------------------------------

class _ServerState:
    """Holds the resident executor and cross-call dirwatch baselines."""

    def __init__(self) -> None:
        self._executor: Any = None
        self.dirwatch_baselines: dict[str, dict] = {}
        self._feed_manager: Any = None

    def executor(self) -> Any:
        """Return the resident LocalExecutor, creating it lazily (Windows/mss)."""
        if self._executor is None:
            import sys
            if sys.platform != "win32":
                raise RuntimeError(
                    "LocalExecutor is Windows-only; run the MCP server on the Windows host."
                )
            try:
                from .drivers.local import LocalExecutor
            except ImportError as exc:  # pragma: no cover - env-specific
                raise RuntimeError(
                    f"LocalExecutor unavailable (install open-compute[local]): {exc}"
                ) from exc
            self._executor = LocalExecutor()
        return self._executor

    def set_executor(self, executor: Any) -> None:
        """Inject an executor (used by tests with a MockExecutor)."""
        self._executor = executor

    def feed_manager(self) -> Any:
        if self._feed_manager is None:
            from .feed_manager import FeedManager, LocalFileInjector
            self._feed_manager = FeedManager(sink=LocalFileInjector())
        return self._feed_manager


_STATE = _ServerState()


# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------

def _default_mode() -> str:
    mode = os.environ.get("OC_SAFETY_MODE", "confirm")
    return mode if mode in ("confirm", "allow_all", "read_only") else "confirm"


def _denied_actions() -> frozenset[ActionType]:
    """Optional deny list from OC_DENY (comma-separated ActionType values)."""
    raw = os.environ.get("OC_DENY", "").strip()
    if not raw:
        return frozenset()
    out = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(ActionType(tok))
        except ValueError:
            pass  # ignore unknown tokens rather than fail the whole server
    return frozenset(out)


# Restrictiveness rank: read_only (strictest) > confirm > allow_all (loosest).
_MODE_RANK = {"allow_all": 0, "confirm": 1, "read_only": 2}


def _make_policy(mode: str | None) -> SafetyPolicy:
    """Build a policy whose effective mode is the MORE RESTRICTIVE of the operator
    ceiling (``OC_SAFETY_MODE``) and the per-call ``mode``.

    A per-call ``mode`` can only *tighten* the gate, never loosen it below the
    operator-set ceiling — so a misbehaving or prompt-injected agent cannot escape a
    ``read_only``/``confirm`` server by passing ``mode='allow_all'``.
    """
    server = _default_mode()
    if mode in _MODE_RANK and _MODE_RANK[mode] > _MODE_RANK[server]:
        effective = mode
    else:
        effective = server
    return SafetyPolicy(mode=effective, denied_actions=_denied_actions())


def _parse_action(obj: dict) -> Action:
    """Build a canonical Action from a dict, accepting 'action' as a 'type' alias."""
    if not isinstance(obj, dict):
        raise ValueError("each action must be a JSON object")
    data = dict(obj)
    if "action" in data and "type" not in data:
        data["type"] = data.pop("action")
    try:
        return Action(**data)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid action {obj!r}: {exc}") from exc


def _gate(action: Action, policy: SafetyPolicy) -> dict | None:
    """Return a non-executed result dict if the gate blocks, else None (=execute)."""
    result = policy.evaluate(action)
    if result.decision is Decision.DENY:
        return {"result": "deny", "reason": result.reason, "action": action.type.value}
    if result.decision is Decision.CONFIRM:
        return {
            "result": "needs_confirmation",
            "reason": result.reason,
            "action": action.type.value,
            "hint": (
                "The server's safety mode blocks this action without acting (stdio MCP has "
                "no server->client confirm callback). For interactive use, the operator "
                "starts the server with OC_SAFETY_MODE=allow_all (isolated VM) and approves "
                "each action via the MCP client's tool-permission dialog. A per-call mode can "
                "only tighten this ceiling, never loosen it."
            ),
        }
    return None


# ---------------------------------------------------------------------------
# Tools — perception (read-only)
# ---------------------------------------------------------------------------

@mcp.tool(description=mcp_i18n.tool_description("capture", _LANG))
def capture(window: str | None = None) -> Image:
    """Take a screenshot of the local screen and return it as a PNG image.

    Look at the returned image, then choose your next action; give coordinates to
    `do`/click tools as fractions 0..1 of the image width/height.

    Args:
        window: Optional window-title substring (case-insensitive). If given,
            captures only that window's bounding rect (Windows). Omit for the
            full virtual desktop (recommended; matches `do`'s coordinate frame).
    """
    if window is not None:
        import sys
        if sys.platform != "win32":
            raise RuntimeError("capture(window=...) is Windows-only.")
        from . import cli  # stdlib-only at import; reuse the tested Win32 helpers
        try:
            from .drivers.local import _set_dpi_awareness
            _set_dpi_awareness()
        except Exception:  # pragma: no cover - best effort
            pass
        hwnd = cli._find_window_hwnd(window)
        if hwnd is None:
            raise ValueError(f"no window found matching {window!r}")
        region = cli._hwnd_to_mss_region(hwnd)
        import mss
        import mss.tools
        with mss.mss() as sct:
            shot = sct.grab(region)
            png = mss.tools.to_png(shot.rgb, shot.size)
        return Image(data=png, format="png")

    obs = _STATE.executor().screenshot()
    return Image(data=obs.screenshot, format="png")


@mcp.tool(description=mcp_i18n.tool_description("tree", _LANG))
def tree(window: str | None = None, max_elements: int = 200, depth: int = 8) -> list[dict]:
    """List UI elements of a window via the Windows accessibility tree (UIA).

    Returns a JSON array of elements with `name`, `role`, `value`, `rect_px` and
    `center_norm` (0..1) — feed `center_norm` to `do`/`click_name` to click an
    element without pixel-guessing. Windows-only; needs open-compute[uia].

    Args:
        window: Target window-title substring. Omit for the foreground window.
        max_elements: Maximum number of elements to return.
        depth: Maximum UIA tree depth to walk.
    """
    feed = _load_uia_feed(max_depth=depth, max_elem=max_elements)
    obs = feed.observe(window=window)
    try:
        from .feeds.uia_windows import _get_virtual_desktop, _rect_to_center_norm
        virt = _get_virtual_desktop()

        class _R:
            def __init__(self, x, y, w, h):
                self.x, self.y, self.width, self.height = x, y, w, h

        out: list[dict] = []
        for elem in obs.elements:
            rx, ry, rw, rh = elem["rect_px"]
            nx, ny = _rect_to_center_norm(_R(rx, ry, rw, rh), *virt)
            out.append({
                "name": elem["name"],
                "role": elem["role"],
                "value": elem.get("value", ""),
                "rect_px": elem["rect_px"],
                "center_norm": [round(nx, 5), round(ny, 5)],
                "visible": elem.get("visible", True),
                "depth": elem.get("depth", 0),
            })
        return out
    except Exception:
        return list(obs.elements)


@mcp.tool(description=mcp_i18n.tool_description("watch_dir", _LANG))
def watch_dir(paths: list[str], seconds: float | None = None, once: bool = True) -> list[dict]:
    """Watch one or more directories for file-system changes; return JSON events.

    Args:
        paths: Directories to watch.
        seconds: If set, collect events for this many seconds (capped at 30) via a
            background observer, then return. Blocks the server for that duration.
        once: Default. One-shot snapshot diff against the baseline remembered from
            the previous `watch_dir` call for the same path-set (persists across
            calls for the server's lifetime). First call returns an empty list.
    """
    import pathlib
    for p in paths:
        if not os.path.isdir(p):
            raise ValueError(f"not a directory: {p!r}")

    from .feeds.dirwatch import DirwatchFeed
    feed = DirwatchFeed()

    if seconds is not None:
        import time as _t
        feed.start(paths)
        try:
            _t.sleep(min(float(seconds), 30.0))
        finally:
            obs = feed.observe()
            feed.stop()
        return list(obs.elements)

    # once: snapshot diff with in-memory baseline (server persistence)
    key = "|".join(sorted(str(pathlib.Path(p).resolve()) for p in paths))
    baseline = _STATE.dirwatch_baselines.get(key)
    events, new_snap = feed.snapshot_diff(paths, baseline)
    _STATE.dirwatch_baselines[key] = new_snap
    return list(events)


@mcp.tool(description=mcp_i18n.tool_description("push_status", _LANG))
def push_status() -> dict:
    """Return the FeedManager status (available feeds, dosage modes, push counts).

    Read-only introspection of the push/auto-injection layer. No actions taken.
    """
    return _STATE.feed_manager().status()


# ---------------------------------------------------------------------------
# Tools — actions (state-changing, safety-gated)
# ---------------------------------------------------------------------------

@mcp.tool(description=mcp_i18n.tool_description("do", _LANG))
def do(
    action: dict | None = None,
    actions: list[dict] | None = None,
    mode: str | None = None,
) -> dict:
    """Execute one canonical action, or a batch (macro) of them, on the desktop.

    Provide exactly one of `action` (single object) or `actions` (array, run in
    order). Coordinates are normalized 0..1. Each action passes the safety gate:
    in `confirm` mode (default) a risky action returns `needs_confirmation`
    without acting; in `allow_all` it runs; in `read_only` state-changing actions
    are denied.

    Action schema (canonical, `type` is required):
      - point actions `left_click`/`right_click`/`middle_click`/`double_click`/
        `triple_click`/`mouse_move`/`scroll`/`left_click_drag`: need `x`,`y`
        (0..1); drag also `end_x`,`end_y`; scroll also `scroll_direction`
        (up/down/left/right) and `scroll_amount`.
      - `type`: `text`. `key`: `text` = combo like "ctrl+s". `wait`: `duration`.
      - `screenshot`, `cursor_position` (read-only).
      - host-side `launch_app`/`activate_window`: `app_name`.

    Args:
        action: A single action object, e.g. {"type":"left_click","x":0.5,"y":0.3}.
        actions: A list of action objects for one macro call.
        mode: Override safety mode for this call (confirm|allow_all|read_only).

    Returns: a status dict; for a batch, `count` of executed actions. On a gated
    action the batch stops and reports which index blocked.
    """
    if (action is None) == (actions is None):
        raise ValueError("provide exactly one of `action` or `actions`")

    items = [action] if action is not None else list(actions or [])
    if not items:
        raise ValueError("`actions` must be a non-empty list")
    parsed = [_parse_action(a) for a in items]
    policy = _make_policy(mode)

    executor = _STATE.executor()
    executed = 0
    final_obs = None
    is_batch = actions is not None

    for i, act in enumerate(parsed):
        blocked = _gate(act, policy)
        if blocked is not None:
            blocked["executed_before"] = executed
            if is_batch:
                blocked["action_index"] = i
            return blocked
        final_obs = executor.execute(act)
        executed += 1

    if is_batch:
        return {
            "result": "batch",
            "count": executed,
            "width": final_obs.width if final_obs else 0,
            "height": final_obs.height if final_obs else 0,
        }
    return {
        "result": "executed",
        "action": parsed[0].type.value,
        "width": final_obs.width if final_obs else 0,
        "height": final_obs.height if final_obs else 0,
    }


@mcp.tool(description=mcp_i18n.tool_description("click_name", _LANG))
def click_name(query: str, window: str | None = None, mode: str | None = None) -> dict:
    """Resolve a UI element by name (Windows UIA) and left-click its center.

    Say "click Insert" instead of guessing pixels. Safety-gated like `do`.

    Args:
        query: Element name to resolve (case-insensitive).
        window: Target window-title substring. Omit for the foreground window.
        mode: Override safety mode (confirm|allow_all|read_only).
    """
    feed = _load_uia_feed()
    target = feed.resolve(query, window=window)
    if target is None:
        raise ValueError(f"no element found matching {query!r}")
    nx, ny = target.center_norm

    act = Action(type=ActionType.LEFT_CLICK, x=nx, y=ny)
    policy = _make_policy(mode)
    blocked = _gate(act, policy)
    if blocked is not None:
        blocked["target"] = target.name
        blocked["center_norm"] = list(target.center_norm)
        return blocked

    obs = _STATE.executor().execute(act)
    return {
        "result": "executed",
        "action": "left_click",
        "target": target.name,
        "role": target.role,
        "center_norm": list(target.center_norm),
        "rect_px": list(target.rect_px),
        "width": obs.width,
        "height": obs.height,
    }


@mcp.tool(description=mcp_i18n.tool_description("invoke", _LANG))
def invoke(query: str, window: str | None = None, mode: str | None = None) -> dict:
    """Click-free invoke of a UI element via UIA patterns (no mouse movement).

    Uses InvokePattern/Toggle/SelectionItem/LegacyIAccessible fallbacks; works even
    when the window is not fully foreground for most native apps. Safety-gated.

    Args:
        query: Element name to invoke (case-insensitive).
        window: Target window-title substring.
        mode: Override safety mode (confirm|allow_all|read_only).
    """
    feed = _load_uia_feed()
    target = feed.resolve(query, window=window)
    if target is None:
        raise ValueError(f"no element found matching {query!r}")

    # Gate as a left_click equivalent (invoke is a state-changing activation).
    act = Action(type=ActionType.LEFT_CLICK, x=target.center_norm[0], y=target.center_norm[1])
    policy = _make_policy(mode)
    blocked = _gate(act, policy)
    if blocked is not None:
        blocked["target"] = target.name
        blocked["center_norm"] = list(target.center_norm)
        return blocked

    ok = feed.invoke(query, window=window)
    return {
        "result": "invoked" if ok else "invoke_failed",
        "target": target.name,
        "role": target.role,
        "center_norm": list(target.center_norm),
        "rect_px": list(target.rect_px),
    }


@mcp.tool(description=mcp_i18n.tool_description("rec_replay", _LANG))
def rec_replay(path: str, params: dict | None = None, mode: str | None = None) -> dict:
    """Replay a recorded .clirec macro against the desktop (optional clirec pkg).

    Every replayed action passes the safety gate (default confirm). Requires the
    external `clirec` package (`pip install open-compute[clirec]`).

    Args:
        path: Path to a .clirec file.
        params: Optional parameter substitutions for the recording.
        mode: Safety mode (confirm|allow_all|read_only). Default confirm.
    """
    policy = _make_policy(mode)  # respects the OC_SAFETY_MODE ceiling (tighten-only)
    try:
        from . import cli
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"cli helpers unavailable: {exc}") from exc
    try:
        result = cli._run_replay(path, params or {}, _STATE.executor(), policy=policy)
    except PermissionError as exc:
        return {"result": "deny", "reason": str(exc)}
    return {"result": "replayed", "path": path, "detail": _jsonable(result)}


# ---------------------------------------------------------------------------
# Lazy loaders / helpers
# ---------------------------------------------------------------------------

def _load_uia_feed(max_depth: int | None = None, max_elem: int | None = None):
    """Import and return a UiaWindowsFeed, raising a clear error when unavailable."""
    import sys
    if sys.platform != "win32":
        raise RuntimeError("UIA feed is Windows-only.")
    try:
        from .feeds.uia_windows import UiaWindowsFeed
    except ImportError as exc:
        raise RuntimeError(f"UIA feed unavailable (install open-compute[uia]): {exc}") from exc
    kwargs: dict = {}
    if max_depth is not None:
        kwargs["max_depth"] = max_depth
    if max_elem is not None:
        kwargs["max_elem"] = max_elem
    feed = UiaWindowsFeed(**kwargs)
    if not feed.available():
        raise RuntimeError("UIA feed not available — install open-compute[uia].")
    return feed


def _jsonable(value: Any) -> Any:
    """Best-effort convert an arbitrary return value to something JSON-safe."""
    try:
        import json
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the open-compute MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
