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

Coordinates are **normalized 0..1**, but coordinate mouse actions additionally
require the physical source frame plus a robust expected top-level window
identity. Window-local captures must never be treated as virtual-desktop
coordinates. Windows-only for real capture / input (LocalExecutor + UIA need
the interactive desktop session).

Import-light: only ``mcp``, :mod:`open_compute.actions` and
:mod:`open_compute.safety` (both stdlib-only) are imported at module load.
``LocalExecutor`` (mss), UIA and dirwatch are imported lazily inside the tools,
so the server imports and lists its tools on any platform without extras.

Run:  ``open-compute-mcp``  or  ``python -m open_compute.mcp_server``
"""

from __future__ import annotations

import atexit
import json
import math
import os
import pathlib
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import CallToolResult, TextContent

if TYPE_CHECKING:  # pragma: no cover - static-analysis only, zero runtime cost
    # Keeps this module's "import-light" property (indicator.py is otherwise
    # only ever imported lazily, inside the functions that need it) while
    # still giving type checkers/linters a real name to resolve for the
    # string-quoted annotations below (Ticket T-20260825-540085216).
    from .indicator import SignalConfig

from . import mcp_i18n
from .actions import Action, ActionType
from .interaction import (
    InteractionContractError,
    ObservationRegistry,
    describe_window,
    resolve_window_reference,
)
from .preclick import (
    PreClickVerificationError,
    WINDOW_GUARDED_ACTIONS,
    coordinate_frame_from_executor,
    execute_with_preclick,
)
from .safety import Decision, SafetyPolicy

_LANG = mcp_i18n.current_language()
_OBSERVATION_BOUND_ACTIONS = WINDOW_GUARDED_ACTIONS | {
    ActionType.MOUSE_MOVE,
    ActionType.SCROLL,
}

mcp = FastMCP("open-compute", instructions=mcp_i18n.instructions(_LANG))


# ---------------------------------------------------------------------------
# Server state — one warm executor for the whole process (persistence win)
# ---------------------------------------------------------------------------

class _ServerState:
    """Holds the resident executor and cross-call dirwatch baselines."""

    def __init__(self) -> None:
        self._executor: Any = None
        self.preclick_probe: Any = None
        self.window_tokens: dict[str, dict[str, Any]] = {}
        self.observations = ObservationRegistry()
        self.dirwatch_baselines: dict[str, dict] = {}
        self._feed_manager: Any = None
        # Human-in-the-loop signal state (one persistent overlay per server).
        # Touched from the tool thread *and* from the idle-hide timer thread,
        # so every mutation goes through `signal_lock` (reentrant: the arm
        # helpers are called from inside already-locked sections).
        self.signal_lock = threading.RLock()
        self.signal_indicator: Any = None
        self.signal_mode: str = ""
        self.pending_abort_message: str | None = None
        # True only while the *visible* overlay was put up by auto-signal.
        # A manual `signal_show` clears it, so the idle timer never sweeps
        # away an overlay a human asked for.
        self.signal_auto_shown: bool = False
        self.signal_idle_timer: threading.Timer | None = None
        self.signal_lease_timer: threading.Timer | None = None
        self.signal_owner: str = ""
        self.signal_session: str = ""
        self.signal_expires_at: float | None = None
        # --- Not-Aus / kill switch (Ticket T-20260818-895473048) -----------
        # Latched by the overlay's abort button or hotkey. While True, every
        # gate-relevant tool (do/click_name/invoke/rec_replay/capture) denies
        # outright — even under OC_SAFETY_MODE=allow_all — until a fresh
        # `signal_show` re-arms the session (see `_show_signal_indicator`).
        self.abort_triggered: bool = False
        self.abort_reason: str | None = None
        # Pre-action grace countdown: set to a monotonic deadline whenever
        # the overlay (re-)appears; the first gate-relevant tool call after
        # that blocks until the deadline passes or the kill switch fires.
        self.grace_deadline: float | None = None
        # Ticket T-20260825-540085216 (bypass hardening): monotonic timestamp
        # of the last time a gate-relevant tool call passed a satisfied grace
        # window (waited it out, or found it within the activity cooldown).
        # ``None`` means "never satisfied in this process" -- the very next
        # gate-relevant call must always arm+wait. Read/written only via
        # ``_await_grace_period``/``_ensure_mandatory_grace_armed``.
        self.grace_last_satisfied_at: float | None = None
        # Human-activity watch (opt-in, OC_HUMAN_ACTIVITY_WATCH): lazily
        # built so platforms without ctypes/win32 never touch it.
        self.activity_classifier: Any = None
        self.activity_adapter: Any = None
        # Model->human notes window (Ticket T-20260825-767105130, work-
        # together mode). Independent lifecycle from the action-signal
        # overlay above -- it is a session-long "diary", not something that
        # should flicker open/closed on every do()/click_name() call the
        # way signal_hide() does for the border overlay.
        self.observation_overlay: Any = None

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
        self.observations.clear()

    def set_preclick_probe(self, probe: Any) -> None:
        """Inject a WindowFromPoint probe (tests never touch real windows)."""
        self.preclick_probe = probe

    def feed_manager(self) -> Any:
        if self._feed_manager is None:
            from .feed_manager import FeedManager, LocalFileInjector
            self._feed_manager = FeedManager(sink=LocalFileInjector())
        return self._feed_manager


_STATE = _ServerState()


def _current_windows(*, issue_tokens: bool = False) -> list[dict[str, Any]]:
    """Read live windows and normalize the public identity contract."""

    from .drivers.local import list_windows as _driver_list_windows

    windows: list[dict[str, Any]] = []
    for raw in _driver_list_windows():
        try:
            window = describe_window(raw)
        except InteractionContractError:
            continue
        windows.append(window)
        if issue_tokens:
            _STATE.window_tokens[window["window_token"]] = window
    return windows


def _select_window(window: str | None) -> dict[str, Any]:
    """Resolve a foreground or title-selected window exact-first/fail-closed."""

    windows = _current_windows(issue_tokens=True)
    if window is None:
        matches = [item for item in windows if item.get("foreground")]
        if len(matches) != 1:
            raise InteractionContractError(
                "ambiguous_target",
                "exactly one foreground window is required",
                candidates=matches or windows,
            )
        return matches[0]

    query = " ".join(window.split()).casefold()
    exact = [
        item for item in windows
        if " ".join(str(item["title"]).split()).casefold() == query
    ]
    candidates = exact or [
        item for item in windows
        if query in " ".join(str(item["title"]).split()).casefold()
    ]
    if not candidates:
        raise InteractionContractError(
            "target_not_found",
            "no top-level window matches the supplied title",
            query=window,
            candidates=windows,
        )
    if len(candidates) != 1:
        raise InteractionContractError(
            "ambiguous_target",
            "window title matches more than one top-level window",
            query=window,
            candidates=candidates,
        )
    return candidates[0]


def _resolve_bound_window(reference: str | dict) -> dict[str, Any]:
    return resolve_window_reference(
        reference,
        _current_windows(issue_tokens=False),
        _STATE.window_tokens,
    )


def _foreground_window() -> dict[str, Any]:
    windows = _current_windows(issue_tokens=False)
    foreground = [window for window in windows if window.get("foreground")]
    if len(foreground) != 1:
        raise InteractionContractError(
            "foreground_window_unresolvable",
            "exactly one foreground window must be observable before input",
            candidates=foreground or windows,
        )
    return foreground[0]


def _verify_foreground(expected: dict[str, Any]) -> dict[str, Any]:
    """Re-read and compare foreground identity immediately before input."""

    bound = _resolve_bound_window(expected)
    actual = _foreground_window()
    if bound["window_token"] != actual["window_token"]:
        raise InteractionContractError(
            "foreground_window_mismatch",
            "foreground window does not match the bound target",
            expected_window=bound,
            actual_window=actual,
        )
    return actual


def _activate_bound_window(executor: Any, expected: dict[str, Any]) -> dict[str, Any]:
    """Activate one exact identity and verify the postcondition."""

    bound = _resolve_bound_window(expected)
    if hasattr(executor, "activate_window_identity"):
        executor.activate_window_identity(bound)
    else:
        from .window_control import Win32WindowAdapter

        Win32WindowAdapter().show(int(bound["hwnd"]), "activate")
    actual = _verify_foreground(bound)
    return {"expected_window": bound, "actual_window": actual}


def _window_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    expected: dict[str, Any] | None,
) -> dict[str, Any]:
    previous = {window["window_token"] for window in before}
    new_windows = [window for window in after if window["window_token"] not in previous]
    owner_id = expected["window_id"] if expected is not None else None
    owned = [
        window for window in new_windows
        if owner_id is not None and window.get("owner_window_id") == owner_id
    ]
    return {"new_windows": new_windows, "owned_or_modal_candidates": owned}


def _record_screenshot_observation(
    observation: Any,
    *,
    window: dict[str, Any] | None = None,
    frame: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if frame is None:
        frame = coordinate_frame_from_executor(_STATE.executor())
    meta = _STATE.observations.record(
        kind="screenshot",
        payload=bytes(observation.screenshot),
        window=window,
        frame=frame,
    )
    meta["screenshot_id"] = meta["observation_id"]
    meta["width"] = int(observation.width)
    meta["height"] = int(observation.height)
    return meta


def _claim_observation(observation_id: str) -> dict[str, Any]:
    """Re-observe the same source and consume it only if unchanged."""

    meta = _STATE.observations.peek(observation_id)
    if meta["kind"] == "uia_tree":
        feed = _load_uia_feed()
        window = meta.get("window")
        exact_title = window["title"] if window else None
        current = list(feed.observe(window=exact_title).elements)
        return _STATE.observations.claim(
            observation_id,
            payload=current,
            window=window,
            frame=meta.get("coordinate_frame"),
        )

    window = meta.get("window")
    if window is not None:
        png = _capture_window_png(window)
        payload = png
    else:
        payload = bytes(_STATE.executor().screenshot().screenshot)
    return _STATE.observations.claim(
        observation_id,
        payload=payload,
        window=window,
        frame=meta.get("coordinate_frame"),
    )


def _post_action_observation(
    observation: Any,
    *,
    before_windows: list[dict[str, Any]],
    expected_window: dict[str, Any] | None,
) -> dict[str, Any]:
    """Invalidate prior state and return a fresh screenshot postcondition."""

    _STATE.observations.invalidate_all()
    after_windows = _current_windows(issue_tokens=True)
    foreground = next((item for item in after_windows if item.get("foreground")), None)
    meta = _record_screenshot_observation(observation, window=foreground)
    meta["window_changes"] = _window_changes(
        before_windows, after_windows, expected_window
    )
    return meta


# ---------------------------------------------------------------------------
# Not-Aus / kill switch + pre-action grace period
# (Ticket T-20260818-895473048 — the abort button/hotkey in the overlay,
# an auto-pause on real human input, and a countdown before the very first
# state-changing action or screenshot of a session.)
# ---------------------------------------------------------------------------

_DEFAULT_ABORT_REASON = "Vom Nutzer abgebrochen (Grund folgt)"
_GRACE_POLL_SECONDS = 0.25


def _kill_switch_blocked() -> dict | None:
    """``None`` if the kill switch is not latched, else the deny result."""
    with _STATE.signal_lock:
        if not _STATE.abort_triggered:
            return None
        reason = _STATE.abort_reason or _DEFAULT_ABORT_REASON
    return {"result": "aborted", "reason": reason, "abort_reason": reason}


def _trigger_kill_switch(initial_reason: str | None = None) -> None:
    """Latch the kill switch immediately (called off the overlay's UI thread).

    Sets ``abort_triggered`` synchronously so any in-flight grace wait or
    batch loop notices it on its very next poll — before a reason dialog
    even has a chance to open, let alone be answered.
    """
    with _STATE.signal_lock:
        _STATE.abort_triggered = True
        _STATE.abort_reason = initial_reason or _DEFAULT_ABORT_REASON
        _STATE.grace_deadline = None  # nothing left to wait out — it already stopped
        aborted_indicator = _STATE.signal_indicator
    # The abort callback can run on the overlay thread; clear from a distinct
    # daemon thread so WindowsBorderOverlay never tries to join itself.
    if aborted_indicator is not None:
        def _hide_if_still_current() -> None:
            with _STATE.signal_lock:
                if _STATE.signal_indicator is not aborted_indicator:
                    return
            signal_hide()

        cleanup = threading.Timer(0, _hide_if_still_current)
        cleanup.daemon = True
        cleanup.start()


def _reset_kill_switch() -> None:
    """Re-arm: only an explicit fresh `signal_show` calls this (see below)."""
    with _STATE.signal_lock:
        _STATE.abort_triggered = False
        _STATE.abort_reason = None


def _start_grace_period(seconds: float) -> None:
    with _STATE.signal_lock:
        _STATE.grace_deadline = time.monotonic() + seconds if seconds > 0 else None


def _canonical_signal_config() -> "SignalConfig":
    """Load the operator's own signal config from the canonical path only.

    Deliberately takes no ``config_path`` argument and never consults one a
    caller supplied to ``signal_show`` — that per-call ``config_path`` is a
    legitimate operator feature (point at an alternate, still locally
    authored file) but must never become the thing that decides whether the
    *mandatory* grace window applies. Only ``OC_SIGNAL_CONFIG`` (an operator
    environment variable, not an MCP tool argument) or the fixed default
    path can change what this loads. Falls back to built-in defaults
    (4.0s grace, 120s cooldown) when no config file exists yet.
    """
    from .indicator import SignalConfig

    path = _signal_config_path()
    return SignalConfig.load(path) if path is not None else SignalConfig()


def _resolve_grace_seconds(cfg: "SignalConfig") -> float:
    """``pre_action_grace_seconds`` with the ``OC_SIGNAL_GRACE_SECONDS``
    operator override — shared by ``signal_show`` and the mandatory path so
    both always agree on the same number."""
    seconds = cfg.pre_action_grace_seconds
    env = os.environ.get("OC_SIGNAL_GRACE_SECONDS", "").strip()
    if env:
        try:
            candidate = float(env)
            if math.isfinite(candidate):
                seconds = max(0.0, candidate)
        except ValueError:
            pass  # an unusable override must not break the overlay
    return seconds


def _resolve_grace_cooldown_seconds(cfg: "SignalConfig") -> float:
    """``grace_cooldown_seconds`` with the ``OC_SIGNAL_GRACE_COOLDOWN_SECONDS``
    operator override, mirroring ``_resolve_grace_seconds``."""
    seconds = cfg.grace_cooldown_seconds
    env = os.environ.get("OC_SIGNAL_GRACE_COOLDOWN_SECONDS", "").strip()
    if env:
        try:
            candidate = float(env)
            if math.isfinite(candidate):
                seconds = max(0.0, candidate)
        except ValueError:
            pass
    return seconds


def _ensure_mandatory_grace_armed() -> None:
    """Arm the pre-action grace window even when nobody ever called
    ``signal_show`` and ``OC_SIGNAL_AUTO`` is unset (Ticket
    T-20260825-540085216 — bypass hardening).

    Root cause this closes: previously, ``_await_grace_period`` only ever
    blocked if *something else* had already armed ``grace_deadline`` — a
    manual ``signal_show`` call, or the opt-in auto-signal path (off by
    default). A caller that simply never called ``signal_show`` skipped the
    safety window entirely, with no config change required — the window's
    stated purpose (user protection/transparency) must not be something a
    model can silently opt itself out of. This function makes arming the
    default; only the operator's own canonical config can suppress it, via
    ``pre_action_grace_seconds: 0`` (the documented "0 = off" switch,
    already 0 = off before this ticket) — never a model-suppliable
    argument.

    Second bypass this closes: ``signal_show`` accepts a caller-supplied
    ``config_path`` (a legitimate operator feature — point at an alternate,
    still locally authored file). Nothing stopped a model from writing its
    own tiny config (``pre_action_grace_seconds: 0`` or a near-zero value)
    and passing that path to ``signal_show`` to arm a deadline that expires
    almost immediately. This function does not just check "is *something*
    armed" — it enforces a FLOOR: the remaining wait is raised (never
    shortened) to at least the canonical config's grace seconds if it falls
    short. A legitimately *longer* explicit grace (operator's own
    ``config_path`` to a stricter profile) is left untouched; only a
    shorter-than-canonical one gets raised. An explicit-``signal_show``
    grace of exactly 0 clears ``grace_deadline`` to ``None`` in
    ``_start_grace_period`` already, so it is handled the same way as
    "nothing armed yet".

    A no-op (arms nothing / raises nothing) when: the already-armed
    deadline already meets or exceeds the canonical floor, the grace was
    satisfied within the configured activity cooldown (Ticket part 1b —
    continuous use inside the cooldown window does not reopen the window),
    or ``pre_action_grace_seconds <= 0`` in the canonical config (Ticket
    part 1c — the operator's explicit "off").
    """
    # Config load (file I/O) stays outside the lock, matching
    # _show_signal_indicator's existing style — only the state check-and-set
    # below needs the lock.
    cfg = _canonical_signal_config()
    seconds = _resolve_grace_seconds(cfg)
    if seconds <= 0:
        return
    cooldown = _resolve_grace_cooldown_seconds(cfg)
    with _STATE.signal_lock:
        now = time.monotonic()
        floor_deadline = now + seconds
        if _STATE.grace_deadline is not None and _STATE.grace_deadline >= floor_deadline:
            return  # already armed for at least the canonical duration
        last = _STATE.grace_last_satisfied_at
        if last is not None and cooldown > 0 and now - last < cooldown:
            return
        _STATE.grace_deadline = floor_deadline


def _mark_grace_satisfied() -> None:
    with _STATE.signal_lock:
        _STATE.grace_last_satisfied_at = time.monotonic()


def _await_grace_period() -> dict | None:
    """Block out any armed pre-action grace window; the kill switch wins.

    Returns the abort result dict if the kill switch fires (before or
    during the wait), else ``None`` once it is safe to proceed. Since
    Ticket T-20260825-540085216, this ALWAYS attempts to arm a mandatory
    grace window first (see ``_ensure_mandatory_grace_armed``) unless the
    operator's own config or a recent activity cooldown says otherwise —
    it no longer depends on a prior ``signal_show`` call, manual or auto.
    """
    blocked = _kill_switch_blocked()
    if blocked is not None:
        return blocked
    _ensure_mandatory_grace_armed()
    while True:
        with _STATE.signal_lock:
            deadline = _STATE.grace_deadline
        if deadline is None:
            _mark_grace_satisfied()
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            with _STATE.signal_lock:
                if _STATE.grace_deadline == deadline:
                    _STATE.grace_deadline = None
            _mark_grace_satisfied()
            return None
        time.sleep(min(_GRACE_POLL_SECONDS, remaining))
        blocked = _kill_switch_blocked()
        if blocked is not None:
            return blocked


# ---------------------------------------------------------------------------
# User-Aktivitaets-Wache (opt-in, OC_HUMAN_ACTIVITY_WATCH) — pauses on real
# recent mouse/keyboard input instead of executing over it. Built on the
# existing headless `human_activity` module (GetLastInputInfo timestamp only,
# never a key/button/text hook); off by default because a real desktop's
# last-input time reflects whatever the operator is doing *right now*,
# including running this very tool call from a terminal — an unconditional
# default risks false-positive pauses on a workstation the human actively
# shares with the agent. Wired into `do`/`click_name`/`invoke`/`rec_replay`.
# ---------------------------------------------------------------------------

def _activity_watch_enabled() -> bool:
    raw = os.environ.get("OC_HUMAN_ACTIVITY_WATCH", "").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def _activity_watch_active() -> bool:
    """Enabled AND on a platform where the ctypes tick clock exists."""
    if not _activity_watch_enabled():
        return False
    import sys
    return sys.platform == "win32"


def _now_tick_ms() -> int:
    """GetTickCount64, DWORD-wrapped — the same clock human_activity expects."""
    import ctypes
    return int(ctypes.windll.kernel32.GetTickCount64()) % (2**32)


def _human_activity_blocked() -> dict | None:
    """``None`` if clear to proceed, else the same shape as `_kill_switch_blocked`.

    A positive detection also LATCHES the kill switch (not just this one
    call) — "pausiert automatisch und verlangt erneute Freigabe" (Ticket
    T-20260818-895473048): the human is demonstrably at the keyboard, so
    every further action needs a fresh `signal_show`, not just this one.
    """
    if not _activity_watch_enabled():
        return None
    import sys
    if sys.platform != "win32":
        return None
    try:
        from .human_activity import (
            GetLastInputInfoAdapter,
            HumanActivityClassifier,
            InputProvenance,
        )
    except Exception:  # pragma: no cover - defensive, must never break the action
        return None
    with _STATE.signal_lock:
        if _STATE.activity_classifier is None:
            _STATE.activity_classifier = HumanActivityClassifier()
        if _STATE.activity_adapter is None:
            _STATE.activity_adapter = GetLastInputInfoAdapter()
        classifier = _STATE.activity_classifier
        adapter = _STATE.activity_adapter
    try:
        assessment = classifier.assess(adapter.sample())
    except Exception:  # pragma: no cover - a probe failure must not block acting
        return None
    if assessment.recent and assessment.provenance is InputProvenance.HUMAN:
        _trigger_kill_switch(
            "Echte Nutzer-Eingabe erkannt (Maus/Tastatur) waehrend einer "
            "Agent-Aktion — automatisch pausiert, erneute Freigabe per "
            "signal_show noetig."
        )
        return _kill_switch_blocked()
    return None


def _record_agent_action(action_id: str, started_tick_ms: int) -> None:
    """Tell the classifier "that recent input was us", not the human.

    Only meaningful while the watch is enabled; a no-op otherwise so it never
    touches ctypes on a platform/process where the classifier was never built.
    """
    if not _activity_watch_enabled() or _STATE.activity_classifier is None:
        return
    try:
        ended = _now_tick_ms()
        with _STATE.signal_lock:
            _STATE.activity_classifier.record_agent_input(
                action_id, started_tick_ms, ended
            )
    except Exception:  # pragma: no cover - bookkeeping must never break the action
        pass


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

# WGC only pushes a frame when the target redraws, so a window that is idle or
# not capturable at all must fail *fast*: a tool call blocks the whole client.
# Worst case here is retries * timeout + backoff ≈ 7s, against the ~30s the
# monitor-grab defaults would cost.
_WGC_SKIP = 2
_WGC_TIMEOUT = 3.0
_WGC_RETRIES = 2


def _wgc_forced(title: str) -> bool:
    """True if OC_WGC_WINDOWS marks this window title as WGC-only.

    The blank-frame fallback below is automatic, so this is only for windows
    that must skip the GDI attempt outright (a costly or visibly flickering
    first grab). Comma-separated, case-insensitive title substrings.
    """
    raw = os.environ.get("OC_WGC_WINDOWS", "").strip()
    if not raw:
        return False
    lowered = title.lower()
    return any(tok.strip().lower() in lowered for tok in raw.split(",") if tok.strip())


def _capture_budget() -> tuple[float, int, bool]:
    """Read the capture-size knobs: (scale, max_dim, grayscale).

    A vision model is billed per pixel, so a full-HD grab is the single most
    expensive thing this server returns. Shrinking it costs nothing in control
    accuracy because every coordinate here is normalized 0..1 — only legibility
    goes down, so the caller picks the trade-off:

    ``OC_CAPTURE_SCALE``      0.05..1.0 factor (default 1.0 = off)
    ``OC_CAPTURE_MAX_DIM``    cap the longest edge in pixels (default 0 = off)
    ``OC_CAPTURE_GRAYSCALE``  drop colour — shrinks the payload, *not* the token
                              count, which follows pixel count alone
    """
    try:
        scale = float(os.environ.get("OC_CAPTURE_SCALE", "1") or "1")
    except ValueError:
        scale = 1.0
    if not 0.05 <= scale <= 1.0:
        scale = 1.0
    try:
        max_dim = int(os.environ.get("OC_CAPTURE_MAX_DIM", "0") or "0")
    except ValueError:
        max_dim = 0
    grayscale = os.environ.get("OC_CAPTURE_GRAYSCALE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return scale, max(0, max_dim), grayscale


def _shrink_png(png: bytes) -> bytes:
    """Apply the capture budget to a PNG, returning it unchanged when off.

    Never raises: a cosmetic size reduction must not be able to fail a capture.
    """
    scale, max_dim, grayscale = _capture_budget()
    if scale >= 1.0 and max_dim <= 0 and not grayscale:
        return png
    try:
        import io

        from PIL import Image as _PILImage
    except ImportError:
        return png
    try:
        with _PILImage.open(io.BytesIO(png)) as img:
            width, height = img.size
            target_w, target_h = width, height
            if scale < 1.0:
                target_w = max(1, round(width * scale))
                target_h = max(1, round(height * scale))
            longest = max(target_w, target_h)
            if max_dim and longest > max_dim:
                shrink = max_dim / longest
                target_w = max(1, round(target_w * shrink))
                target_h = max(1, round(target_h * shrink))
            out = img
            if (target_w, target_h) != (width, height):
                out = img.resize((target_w, target_h), _PILImage.LANCZOS)
            if grayscale:
                out = out.convert("L")
            buf = io.BytesIO()
            out.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except Exception:
        return png


def _capture_window_png(window: str | dict[str, Any]) -> bytes:
    """Capture a single window, falling back to WGC when GDI yields a black frame.

    A GDI region grab (mss) of a DirectX / hardware-composited window — Roblox
    Studio, Blender, a GPU-accelerated browser — does not fail; it returns an
    all-black rectangle. So a successful grab is not proof of a usable frame:
    we check it, and re-grab through Windows.Graphics.Capture if it is blank.
    """
    import sys
    if sys.platform != "win32":
        raise RuntimeError("capture(window=...) is Windows-only.")
    from . import cli  # stdlib-only at import; reuse the tested Win32 helpers
    try:
        from .drivers.local import _set_dpi_awareness
        _set_dpi_awareness()
    except Exception:  # pragma: no cover - best effort
        pass

    if isinstance(window, dict):
        hwnd = int(window["hwnd"])
        title = str(window["title"])
    else:
        hwnd = cli._find_window_hwnd(window)
        if hwnd is None:
            raise ValueError(f"no window found matching {window!r}")
        title = cli._window_title(hwnd)

    from .drivers import wgc

    png: bytes | None = None

    if not _wgc_forced(title):
        try:
            region = cli._hwnd_to_mss_region(hwnd)
            import mss
            import mss.tools
            with mss.mss() as sct:
                shot = sct.grab(region)
                png = mss.tools.to_png(shot.rgb, shot.size)
        except Exception:
            png = None  # GDI refused outright — try WGC below.
        if png is not None and not wgc.is_blank_png(png):
            return png
        if not wgc.available() and png is not None:
            # Blank, but the extra is not installed: a black frame still beats
            # failing the call. Installing open-compute[wgc] fixes the black.
            return png

    try:
        return wgc.grab_window_png(
            hwnd,
            skip=_WGC_SKIP,
            max_seconds=_WGC_TIMEOUT,
            retries=_WGC_RETRIES,
        )[0]
    except Exception as exc:
        if png is not None:
            return png  # degrade to the black frame rather than fail the call
        raise RuntimeError(f"capture of {title!r} failed: {exc}") from exc


@mcp.tool(description=mcp_i18n.tool_description("capture", _LANG))
def capture(window: str | None = None) -> Any:
    """Return one-shot observation metadata followed by a PNG image.

    Look at the image, then choose exactly one coordinate action. Pass the
    returned `observation_id` plus a descriptor/token from `list_windows` to
    `do`. Prefer `invoke` or `click_name` when UIA can name the target.

    Args:
        window: Optional exact-first window-title query. Ambiguity is rejected.
            If given, captures only that window (Windows), transparently via
            Windows.Graphics.Capture when the window is hardware-composited and
            a plain grab would come back black. Omit for the full virtual
            desktop (recommended; matches `do`'s coordinate frame).

    Raises:
        PermissionError: the kill switch is latched, or a pre-action grace
            countdown is running and the human aborted during it — "vor den
            ersten Screenshots/Bildern" (Ticket T-20260818-895473048): a
            capture is content, not metadata, so it is gated exactly like a
            state-changing action.
    """
    grace_blocked = _await_grace_period()
    if grace_blocked is not None:
        raise PermissionError(grace_blocked["reason"])
    if window is not None:
        identity = _select_window(window)
        png = _capture_window_png(identity)
        frame = dict(identity.get("rect") or {})
        if not frame:
            from . import cli

            frame = dict(cli._hwnd_to_mss_region(int(identity["hwnd"])))
        width, height = int(frame["width"]), int(frame["height"])
        meta = _record_screenshot_observation(
            SimpleNamespace(screenshot=png, width=width, height=height),
            window=identity,
            frame=frame,
        )
        image = Image(data=_shrink_png(png), format="png").to_image_content()
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(meta, ensure_ascii=False)),
                image,
            ],
            structuredContent=meta,
        )

    obs = _STATE.executor().screenshot()
    meta = _record_screenshot_observation(obs)
    image = Image(
        data=_shrink_png(obs.screenshot), format="png"
    ).to_image_content()
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(meta, ensure_ascii=False)),
            image,
        ],
        structuredContent=meta,
    )


@mcp.tool(description=mcp_i18n.tool_description("list_windows", _LANG))
def list_windows() -> list[dict]:
    """List the open top-level windows, foreground first.

    Use this before `capture(window=...)`, `tree(window=...)` or an
    `activate_window` action instead of guessing a title: it returns the exact
    titles, stable window/process IDs, an issued token, pixel rect, and its
    normalized 0..1 center. Pass the complete descriptor/token back to actions.
    """
    return _current_windows(issue_tokens=True)


@mcp.tool(description=mcp_i18n.tool_description("get_screen_size", _LANG))
def get_screen_size() -> dict:
    """Return the virtual-desktop geometry and the per-monitor breakdown.

    All normalized 0..1 coordinates are relative to the `virtual_desktop` rect
    returned here, so this is how a pixel position from elsewhere is translated
    into an `x`/`y` for `do`.
    """
    from .drivers.local import get_screen_size as _get_screen_size

    return _get_screen_size()


@mcp.tool(description=mcp_i18n.tool_description("tree", _LANG))
def tree(window: str | None = None, max_elements: int = 200, depth: int = 8) -> dict:
    """List UI elements of a window via the Windows accessibility tree (UIA).

    Returns observation metadata plus `elements` with `name`, `role`, `value`,
    `rect_px` and `center_norm`. A raw `do` coordinate additionally needs this
    one-shot `observation_id` and the resolved top-level descriptor/token.
    Windows-only; needs open-compute[uia].

    Args:
        window: Exact-first target-window query. Omit for the foreground window.
        max_elements: Maximum number of elements to return.
        depth: Maximum UIA tree depth to walk.
    """
    identity = _select_window(window)
    feed = _load_uia_feed(max_depth=depth, max_elem=max_elements)
    obs = feed.observe(window=identity["title"])
    raw_elements = list(obs.elements)
    try:
        from .feeds.uia_windows import _get_virtual_desktop, _rect_to_center_norm
        virt = _get_virtual_desktop()

        class _R:
            def __init__(self, x, y, w, h):
                self.x, self.y, self.width, self.height = x, y, w, h

        out: list[dict] = []
        for elem in raw_elements:
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
        elements = out
    except Exception:
        elements = raw_elements
    meta = _STATE.observations.record(
        kind="uia_tree",
        payload=raw_elements,
        window=identity,
        frame=coordinate_frame_from_executor(_STATE.executor()),
    )
    return {**meta, "elements": elements}


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
    expected_window: dict | str | None = None,
    coordinate_frame: dict | None = None,
    observation_id: str | None = None,
    keep_signal: bool = False,
) -> dict:
    """Execute one canonical action, or a batch (macro) of them, on the desktop.

    Provide exactly one of `action` (single object) or `actions` (array, run in
    order). Coordinates are normalized 0..1. Coordinate mouse actions also
    require a robust `expected_window` and a one-shot `observation_id` returned
    by `capture` or `tree`. Each action passes the safety gate:
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
        expected_window: Full descriptor or server-issued `window_token` from
            `list_windows`; required for coordinates, typing, keys, and exact
            activation.
        coordinate_frame: Deprecated assertion. If supplied, it must equal the
            frame bound to `observation_id`; callers cannot invent a new frame.
        observation_id: One-shot ID returned by `capture`/`tree`, required for
            every coordinate action and consumed by exactly one action.
        keep_signal: Keep a visible signal after this call. False by default so
            abort/error/turn-end cleanup cannot leave an orphan overlay.

    Returns: a status dict; for a batch, `count` of executed actions. On a gated
    action the batch stops and reports which index blocked.
    """
    if (action is None) == (actions is None):
        if not keep_signal:
            signal_hide()
        raise ValueError("provide exactly one of `action` or `actions`")

    items = [action] if action is not None else list(actions or [])
    if not items:
        if not keep_signal:
            signal_hide()
        raise ValueError("`actions` must be a non-empty list")
    try:
        parsed = [_parse_action(a) for a in items]
    except Exception:
        if not keep_signal:
            signal_hide()
        raise

    observation_bound = [
        act for act in parsed if act.type in _OBSERVATION_BOUND_ACTIONS
    ]
    if len(parsed) > 1 and observation_bound:
        result = InteractionContractError(
            "one_action_per_observation_required",
            "coordinate actions must be issued one per tool call and observation",
            action_count=len(parsed),
        ).to_result()
        if not keep_signal:
            signal_hide()
        return result

    grace_blocked = _await_grace_period()
    if grace_blocked is not None:
        if not keep_signal:
            signal_hide()
        return grace_blocked

    try:
        policy = _make_policy(mode)
        executor = _STATE.executor()
    except Exception:
        if not keep_signal:
            signal_hide()
        raise
    executed = 0
    final_obs = None
    post_observation: dict[str, Any] | None = None
    text_transfer: dict[str, Any] | None = None
    is_batch = actions is not None
    auto_err: dict | None = None

    try:
        for i, act in enumerate(parsed):
            aborted = _kill_switch_blocked()
            if aborted is not None:
                aborted["executed_before"] = executed
                if is_batch:
                    aborted["action_index"] = i
                return aborted
            blocked = _gate(act, policy)
            if blocked is not None:
                blocked["executed_before"] = executed
                if is_batch:
                    blocked["action_index"] = i
                return blocked
            paused = _human_activity_blocked()
            if paused is not None:
                paused["executed_before"] = executed
                if is_batch:
                    paused["action_index"] = i
                return paused

            reference = (act.meta or {}).get("expected_window") or expected_window
            needs_bound_window = act.type in (
                _OBSERVATION_BOUND_ACTIONS
                | {
                    ActionType.TYPE,
                    ActionType.KEY,
                    ActionType.KEY_DOWN,
                    ActionType.KEY_UP,
                    ActionType.ACTIVATE_WINDOW,
                }
            )
            if needs_bound_window and reference is None:
                failed = InteractionContractError(
                    "expected_window_required",
                    f"{act.type.value} requires a descriptor or token from list_windows",
                ).to_result()
                failed["executed_before"] = executed
                return failed

            try:
                bound = _resolve_bound_window(reference) if reference is not None else None
                action_observation_id = (
                    (act.meta or {}).get("observation_id") or observation_id
                )
                source_frame = coordinate_frame
                if act.type in _OBSERVATION_BOUND_ACTIONS:
                    if not action_observation_id:
                        raise InteractionContractError(
                            "observation_required",
                            "coordinate actions require a fresh observation_id from capture/tree",
                        )
                    claimed = _claim_observation(str(action_observation_id))
                    source_frame = claimed.get("coordinate_frame")
                    if source_frame is None:
                        raise InteractionContractError(
                            "observation_frame_missing",
                            "the observation has no physical coordinate frame",
                            observation_id=action_observation_id,
                        )
                    if coordinate_frame is not None and dict(coordinate_frame) != dict(source_frame):
                        raise InteractionContractError(
                            "coordinate_frame_mismatch",
                            "coordinate_frame does not match the bound observation",
                            observation_id=action_observation_id,
                            expected_frame=source_frame,
                            actual_frame=coordinate_frame,
                        )
                    assert bound is not None
                    observed_window = claimed.get("window")
                    if (
                        observed_window is not None
                        and observed_window["window_token"] != bound["window_token"]
                    ):
                        raise InteractionContractError(
                            "observation_window_mismatch",
                            "the observation belongs to a different bound window",
                            observation_id=action_observation_id,
                            expected_window=bound,
                            observed_window=observed_window,
                        )
                    _verify_foreground(bound)
                elif act.type in {
                    ActionType.TYPE,
                    ActionType.KEY,
                    ActionType.KEY_DOWN,
                    ActionType.KEY_UP,
                }:
                    assert bound is not None
                    _verify_foreground(bound)

                before_windows = _current_windows(issue_tokens=False)
                if auto_err is None:
                    auto_err = _ensure_auto_signal()
                started_tick = _now_tick_ms() if _activity_watch_active() else None

                if act.type is ActionType.ACTIVATE_WINDOW:
                    assert bound is not None
                    _activate_bound_window(executor, bound)
                    final_obs = executor.screenshot()
                elif act.type is ActionType.TYPE:
                    assert bound is not None
                    text_transfer = executor.type_text_verified(
                        act.text or "",
                        check_focus=lambda: _verify_foreground(bound),
                        expected_window=bound,
                    )
                    final_obs = executor.screenshot()
                else:
                    final_obs = execute_with_preclick(
                        executor,
                        act,
                        expected_window=bound,
                        coordinate_frame=source_frame,
                        probe=_STATE.preclick_probe,
                    )
                if started_tick is not None:
                    _record_agent_action(f"do:{i}:{act.type.value}", started_tick)
                executed += 1
                post_observation = _post_action_observation(
                    final_obs,
                    before_windows=before_windows,
                    expected_window=bound,
                )
            except InteractionContractError as exc:
                failed = exc.to_result()
                failed["executed_before"] = executed
                if is_batch:
                    failed["action_index"] = i
                return failed
            except PreClickVerificationError as exc:
                failed = exc.to_result()
                failed["executed_before"] = executed
                if is_batch:
                    failed["action_index"] = i
                return failed

        if is_batch:
            result = {
                "result": "batch",
                "count": executed,
                "width": final_obs.width if final_obs else 0,
                "height": final_obs.height if final_obs else 0,
            }
        else:
            result = {
                "result": "executed",
                "action": parsed[0].type.value,
                "width": final_obs.width if final_obs else 0,
                "height": final_obs.height if final_obs else 0,
            }
        if text_transfer is not None:
            result.update(text_transfer)
            if not text_transfer.get("complete"):
                result["result"] = "text_partial" if text_transfer.get("partial") else "text_rejected"
        if post_observation is not None:
            result["post_action_observation"] = post_observation
        if auto_err:
            result.update(auto_err)
        return result
    finally:
        if not keep_signal:
            signal_hide()


@mcp.tool(description=mcp_i18n.tool_description("click_name", _LANG))
def click_name(
    query: str,
    window: dict | str | None = None,
    mode: str | None = None,
    exact: bool = False,
    min_score: float = 0.8,
    keep_signal: bool = False,
) -> dict:
    """Resolve a UI element by name (Windows UIA) and left-click its center.

    Say "click Insert" instead of guessing pixels. Safety-gated like `do`.

    Args:
        query: Element name to resolve (case-insensitive).
        window: Required descriptor or token previously returned by this server.
        mode: Override safety mode (confirm|allow_all|read_only).
        exact: Require an exact element-name match.
        min_score: Minimum deterministic target score (default 0.8).
        keep_signal: Keep a visible screen signal after this call.
    """
    grace_blocked = _await_grace_period()
    if grace_blocked is not None:
        return grace_blocked

    try:
        if window is None:
            raise InteractionContractError(
                "expected_window_required",
                "click_name requires a descriptor or token from list_windows",
            )
        identity = _resolve_bound_window(window)
        feed = _load_uia_feed()
        try:
            if hasattr(feed, "resolve_detailed"):
                target = feed.resolve_detailed(
                    query,
                    window=identity["title"],
                    exact=exact,
                    min_score=min_score,
                )
            else:  # compatibility for third-party feeds and test doubles
                target = feed.resolve(query, window=identity["title"])
                if target is None:
                    raise InteractionContractError(
                        "target_not_found",
                        "no UI element matches the requested name",
                        query=query,
                    )
        except InteractionContractError as exc:
            return exc.to_result()
        nx, ny = target.center_norm
        act = Action(type=ActionType.LEFT_CLICK, x=nx, y=ny)
        blocked = _gate(act, _make_policy(mode))
        if blocked is not None:
            blocked["target"] = target.name
            blocked["center_norm"] = list(target.center_norm)
            return blocked
        paused = _human_activity_blocked()
        if paused is not None:
            paused["target"] = target.name
            paused["center_norm"] = list(target.center_norm)
            return paused

        executor = _STATE.executor()
        _verify_foreground(identity)
        if hasattr(feed, "resolve_detailed"):
            current_target = feed.resolve_detailed(
                f"{target.name}:{target.role}",
                window=identity["title"],
                exact=True,
                min_score=1.0,
            )
            if (
                current_target.name != target.name
                or current_target.role != target.role
                or tuple(current_target.rect_px) != tuple(target.rect_px)
            ):
                raise InteractionContractError(
                    "target_changed",
                    "the resolved UI element moved or changed before the click",
                    expected_target={
                        "name": target.name,
                        "role": target.role,
                        "rect_px": list(target.rect_px),
                    },
                    actual_target={
                        "name": current_target.name,
                        "role": current_target.role,
                        "rect_px": list(current_target.rect_px),
                    },
                )
        before_windows = _current_windows(issue_tokens=False)
        auto_err = _ensure_auto_signal()
        started_tick = _now_tick_ms() if _activity_watch_active() else None
        try:
            obs = execute_with_preclick(
                executor,
                act,
                expected_window=identity,
                coordinate_frame=coordinate_frame_from_executor(executor),
                probe=_STATE.preclick_probe,
            )
        except PreClickVerificationError as exc:
            failed = exc.to_result()
            failed["target"] = target.name
            failed["center_norm"] = list(target.center_norm)
            return failed
        if started_tick is not None:
            _record_agent_action(f"click_name:{query}", started_tick)
        result = {
            "result": "executed",
            "action": "left_click",
            "target": target.name,
            "role": target.role,
            "match_type": getattr(target, "match_type", "exact") or "exact",
            "score": float(getattr(target, "score", 1.0) or 1.0),
            "alternatives": list(getattr(target, "alternatives", ()) or ()),
            "center_norm": list(target.center_norm),
            "rect_px": list(target.rect_px),
            "width": obs.width,
            "height": obs.height,
            "post_action_observation": _post_action_observation(
                obs,
                before_windows=before_windows,
                expected_window=identity,
            ),
        }
        if auto_err:
            result.update(auto_err)
        return result
    except InteractionContractError as exc:
        return exc.to_result()
    finally:
        if not keep_signal:
            signal_hide()


@mcp.tool(description=mcp_i18n.tool_description("invoke", _LANG))
def invoke(
    query: str,
    window: dict | str | None = None,
    mode: str | None = None,
    exact: bool = False,
    min_score: float = 0.8,
    keep_signal: bool = False,
) -> dict:
    """Click-free invoke of a UI element via UIA patterns (no mouse movement).

    Uses InvokePattern/Toggle/SelectionItem/LegacyIAccessible fallbacks; works even
    when the window is not fully foreground for most native apps. Safety-gated.

    Args:
        query: Element name to invoke (case-insensitive).
        window: Required descriptor or token previously returned by this server.
        mode: Override safety mode (confirm|allow_all|read_only).
        exact: Require an exact element-name match.
        min_score: Minimum deterministic target score (default 0.8).
        keep_signal: Keep a visible screen signal after this call.
    """
    grace_blocked = _await_grace_period()
    if grace_blocked is not None:
        return grace_blocked

    try:
        if window is None:
            raise InteractionContractError(
                "expected_window_required",
                "invoke requires a descriptor or token from list_windows",
            )
        identity = _resolve_bound_window(window)
        feed = _load_uia_feed()
        if hasattr(feed, "resolve_detailed"):
            target = feed.resolve_detailed(
                query,
                window=identity["title"],
                exact=exact,
                min_score=min_score,
            )
        else:
            target = feed.resolve(query, window=identity["title"])
            if target is None:
                raise InteractionContractError(
                    "target_not_found",
                    "no UI element matches the requested name",
                    query=query,
                )

        act = Action(
            type=ActionType.LEFT_CLICK,
            x=target.center_norm[0],
            y=target.center_norm[1],
        )
        blocked = _gate(act, _make_policy(mode))
        if blocked is not None:
            blocked["target"] = target.name
            blocked["center_norm"] = list(target.center_norm)
            return blocked
        paused = _human_activity_blocked()
        if paused is not None:
            paused["target"] = target.name
            paused["center_norm"] = list(target.center_norm)
            return paused

        _verify_foreground(identity)
        before_windows = _current_windows(issue_tokens=False)
        auto_err = _ensure_auto_signal()
        started_tick = _now_tick_ms() if _activity_watch_active() else None
        if hasattr(feed, "invoke_target"):
            ok = feed.invoke_target(target, window=identity["title"])
        else:
            ok = feed.invoke(query, window=identity["title"])
        if started_tick is not None:
            _record_agent_action(f"invoke:{query}", started_tick)
        obs = _STATE.executor().screenshot()
        result = {
            "result": "invoked" if ok else "invoke_failed",
            "target": target.name,
            "role": target.role,
            "match_type": getattr(target, "match_type", "exact") or "exact",
            "score": float(getattr(target, "score", 1.0) or 1.0),
            "alternatives": list(getattr(target, "alternatives", ()) or ()),
            "center_norm": list(target.center_norm),
            "rect_px": list(target.rect_px),
            "post_action_observation": _post_action_observation(
                obs,
                before_windows=before_windows,
                expected_window=identity,
            ),
        }
        if auto_err:
            result.update(auto_err)
        return result
    except InteractionContractError as exc:
        return exc.to_result()
    finally:
        if not keep_signal:
            signal_hide()


@mcp.tool(description=mcp_i18n.tool_description("rec_replay", _LANG))
def rec_replay(
    path: str,
    params: dict | None = None,
    mode: str | None = None,
    keep_signal: bool = False,
) -> dict:
    """Replay a recorded .clirec macro against the desktop (optional clirec pkg).

    Every replayed action passes the safety gate (default confirm). Requires the
    external `clirec` package (`pip install open-compute[clirec]`).

    Args:
        path: Path to a .clirec file.
        params: Optional parameter substitutions for the recording.
        mode: Safety mode (confirm|allow_all|read_only). Default confirm.
        keep_signal: Keep a visible screen signal after this call.
    """
    grace_blocked = _await_grace_period()
    if grace_blocked is not None:
        if not keep_signal:
            signal_hide()
        return grace_blocked

    try:
        policy = _make_policy(mode)  # OC_SAFETY_MODE tighten-only ceiling
        try:
            from . import cli
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"cli helpers unavailable: {exc}") from exc

        def _abort_check() -> str | None:
            blocked = _kill_switch_blocked()
            return blocked["abort_reason"] if blocked is not None else None

        auto_err = _ensure_auto_signal()
        try:
            result = cli._run_replay(
                path,
                params or {},
                _STATE.executor(),
                policy=policy,
                abort_check=_abort_check,
            )
        except PermissionError as exc:
            out = {"result": "deny", "reason": str(exc)}
            aborted = _kill_switch_blocked()
            if aborted is not None:
                out["abort_reason"] = aborted["abort_reason"]
            if auto_err:
                out.update(auto_err)
            return out
        out = {"result": "replayed", "path": path, "detail": _jsonable(result)}
        if auto_err:
            out.update(auto_err)
        return out
    finally:
        if not keep_signal:
            signal_hide()


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
# Human-in-the-loop signal / chat / talk tools
# ---------------------------------------------------------------------------

def _module_state_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "_state"


def _module_session_dir() -> pathlib.Path:
    path = pathlib.Path(__file__).resolve().parent.parent / "_session"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _signal_config_path() -> pathlib.Path | None:
    env = os.environ.get("OC_SIGNAL_CONFIG", "").strip()
    if env:
        return pathlib.Path(env)
    default = _module_state_dir() / "signal-config.json"
    return default if default.exists() else None


def _prompt_channel(channel: str, context: str) -> str | None:
    channels = {
        "console": "ConsoleAbortChannel",
        "tk": "TkAbortChannel",
        "none": "NullAbortChannel",
    }
    if channel not in channels:
        raise ValueError(f"channel must be one of {sorted(channels)}")
    from . import indicator as _indicator_mod

    channel_cls = globals().get(channels[channel]) or getattr(
        _indicator_mod, channels[channel]
    )
    return channel_cls().prompt_reason(context=context)


_SIGNAL_TTL_DEFAULT_SECONDS = 120.0
_SIGNAL_TTL_MAX_SECONDS = 3_600.0


def _signal_ttl_seconds(value: float | None) -> float:
    """Resolve a bounded signal lease duration."""

    raw: str | float | None = value
    if raw is None:
        raw = os.environ.get("OC_SIGNAL_TTL", "").strip() or _SIGNAL_TTL_DEFAULT_SECONDS
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("signal ttl_seconds/OC_SIGNAL_TTL must be numeric") from exc
    if seconds <= 0:
        raise ValueError("signal ttl_seconds must be greater than zero")
    return min(seconds, _SIGNAL_TTL_MAX_SECONDS)


def _cancel_signal_lease() -> None:
    """Cancel the current overlay lease timer, if any."""

    with _STATE.signal_lock:
        timer = _STATE.signal_lease_timer
        _STATE.signal_lease_timer = None
    if timer is not None:
        timer.cancel()


def _signal_lease_fire() -> None:
    """Expire an overlay lease without leaving renderer state behind."""

    with _STATE.signal_lock:
        _STATE.signal_lease_timer = None
    signal_hide()


def _arm_signal_lease(
    *, owner: str, session_id: str, ttl_seconds: float | None
) -> dict[str, Any]:
    _cancel_signal_lease()
    seconds = _signal_ttl_seconds(ttl_seconds)
    expires_at = time.time() + seconds
    with _STATE.signal_lock:
        _STATE.signal_owner = str(owner)
        _STATE.signal_session = str(session_id)
        _STATE.signal_expires_at = expires_at
        timer = threading.Timer(seconds, _signal_lease_fire)
        timer.daemon = True
        _STATE.signal_lease_timer = timer
        timer.start()
    return {
        "owner": str(owner),
        "session": str(session_id),
        "ttl_seconds": seconds,
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
    }


def _show_signal_indicator(
    *,
    mode: str,
    agent: str = "agent",
    scope: str = "screen",
    config_path: str | None = None,
    no_border: bool = False,
    no_cursor: bool = False,
    abort_hotkey: str | None = None,
    arm_grace: bool = True,
    owner: str | None = None,
    session_id: str | None = None,
    ttl_seconds: float | None = None,
) -> dict:
    """Core of ``signal_show`` — shared by the tool itself and auto-signal.

    Raises ``ValueError`` for an unknown ``mode`` (via ``SessionMode``), same
    as the public tool always did; callers that must not crash validate the
    mode themselves before calling this (see ``_ensure_auto_signal``).

    ``arm_grace`` gates the pre-action countdown (Ticket T-20260818-895473048):
    True for the explicit ``signal_show`` tool, which is the deliberate
    take-over gesture and therefore gets the grace window.
    ``_ensure_auto_signal`` passes False because the action tool already owns
    the approved turn; auto-signaling must not arm a second configured wait.
    """

    from .indicator import ScreenSignalIndicator, SignalConfig, signal_presentation
    from .session import SessionMode

    # Validate the lease before mutating any currently visible overlay.
    resolved_ttl = _signal_ttl_seconds(ttl_seconds)

    renderer_cls = globals().get("WindowsBorderOverlay")
    if renderer_cls is None:
        from .indicator import WindowsBorderOverlay as renderer_cls

    cfg = SignalConfig()
    path = pathlib.Path(config_path) if config_path else _signal_config_path()
    if path is not None:
        cfg = SignalConfig.load(path)

    hotkey = abort_hotkey or cfg.abort_hotkey
    # OC_SIGNAL_GRACE_SECONDS is the highest-precedence override, same escape
    # hatch as OC_SIGNAL_IDLE_HIDE — operators (and tests) can dial the
    # countdown to 0 without hand-writing a signal-config.json. Shared with
    # the mandatory-arm path (_ensure_mandatory_grace_armed) so both always
    # agree on the same number.
    grace_seconds = _resolve_grace_seconds(cfg)
    abort_reasons = tuple(cfg.abort_reasons)
    session_mode = SessionMode(mode)
    mode_cfg = cfg.for_mode(session_mode)

    _cancel_signal_lease()
    if _STATE.signal_indicator is not None:
        _STATE.signal_indicator.clear()
        _STATE.signal_indicator = None
    if not mode_cfg.enabled:
        with _STATE.signal_lock:
            _STATE.grace_deadline = None
            _STATE.signal_mode = ""
            _STATE.signal_owner = ""
            _STATE.signal_session = ""
            _STATE.signal_expires_at = None
        return {
            "visible": False,
            "mode": session_mode.value,
            "phase": "hidden",
            "reason": "signal_mode_disabled",
            "label": "",
            "accessible_label": "",
            "color": None,
            "active_color": list(mode_cfg.color),
            "grace_color": list(cfg.pre_action_grace_color),
            "countdown_seconds": None,
            "pre_action_grace_seconds": grace_seconds,
        }

    # Clearing a latched kill switch here is safe unconditionally (not just
    # when arm_grace=True): a state-changing tool can only reach the
    # `_ensure_auto_signal` call site (which passes arm_grace=False) once it
    # already cleared the kill switch's own gate to execute in the first
    # place — so this can never silently wave through an action mid-abort.
    _reset_kill_switch()
    def _on_abort() -> None:
        # SOFORT: latch the kill switch before anything else — a batch loop
        # or grace wait polling in another thread must see this the instant
        # the button/hotkey fires, independent of how long the reason dialog
        # below takes to resolve.
        indicator = _STATE.signal_indicator
        abort_context = indicator.last_label if indicator else ""
        _trigger_kill_switch()
        # Same globals()-first lookup as `_prompt_channel` below, so tests
        # can fake the dialog the same way they already do for signal_abort.
        channel_cls = globals().get("TkAbortChannel")
        if channel_cls is None:
            from .indicator import TkAbortChannel as channel_cls

        message = channel_cls(reasons=abort_reasons).prompt_reason(
            context=abort_context
        )
        with _STATE.signal_lock:
            # No stdout here (stdio transport): hold it for signal_status.
            _STATE.pending_abort_message = message
            if message:
                _STATE.abort_reason = message

    indicator = ScreenSignalIndicator(
        renderer=renderer_cls(
            thickness=cfg.thickness,
            border=not no_border,
            cursor_ring=not no_cursor,
            # Always wired (not just when a hotkey is set): the on-screen
            # abort button needs no hotkey to work.
            on_abort=_on_abort,
            abort_hotkey=hotkey,
            grace_seconds=grace_seconds,
            grace_color=cfg.pre_action_grace_color,
            grace_label_template=cfg.pre_action_grace_label,
        ),
        config=cfg,
    )
    try:
        indicator.show(agent=agent, scope=scope, mode=session_mode)
    except BaseException:
        # A failed/restarted overlay must never leave a hidden countdown armed.
        with _STATE.signal_lock:
            _STATE.grace_deadline = None
            _STATE.signal_indicator = None
            _STATE.signal_mode = ""
        raise
    _STATE.signal_indicator = indicator
    _STATE.signal_mode = session_mode.value
    if arm_grace:
        _start_grace_period(grace_seconds)
    lease = _arm_signal_lease(
        owner=owner or agent,
        session_id=session_id or "default",
        ttl_seconds=resolved_ttl,
    )
    presentation = signal_presentation(
        base_label=indicator.last_label,
        active_color=mode_cfg.color,
        grace_color=cfg.pre_action_grace_color,
        grace_label_template=cfg.pre_action_grace_label,
        remaining_seconds=grace_seconds if arm_grace else 0.0,
    )
    return {
        "visible": True,
        "mode": session_mode.value,
        "phase": presentation.phase,
        "label": presentation.visual_label,
        "accessible_label": presentation.accessible_label,
        "color": list(presentation.color),
        "active_color": list(mode_cfg.color),
        "grace_color": list(cfg.pre_action_grace_color),
        "countdown_seconds": presentation.seconds_remaining,
        "pre_action_grace_seconds": grace_seconds,
        **lease,
    }


@mcp.tool(description=mcp_i18n.tool_description("signal_show", _LANG))
def signal_show(
    mode: str,
    agent: str = "agent",
    scope: str = "screen",
    config_path: str | None = None,
    no_border: bool = False,
    no_cursor: bool = False,
    abort_hotkey: str | None = None,
    owner: str | None = None,
    session_id: str | None = None,
    ttl_seconds: float | None = None,
) -> dict:
    """Show the screen-usage signal overlay (border + cursor ring, per mode).

    The overlay has an owner/session lease and bounded TTL. Action tools hide it
    at turn end unless ``keep_signal=true``; ``signal_hide`` is idempotent.
    The configured grace duration, static grace color and text template are
    shown before the mode color; ``signal_status`` exposes both visual and
    accessibility state. Colors and border/cursor toggles come from the signal
    config (``OC_SIGNAL_CONFIG`` or ``_state/signal-config.json``). An abort
    hotkey opens a reason box and the message is held for ``signal_status``.
    """

    with _STATE.signal_lock:
        # A manually requested overlay is the human's, not auto-signal's: drop
        # any pending idle-hide so it cannot sweep this one away.
        _cancel_idle_hide()
        result = _show_signal_indicator(
            mode=mode,
            agent=agent,
            scope=scope,
            config_path=config_path,
            no_border=no_border,
            no_cursor=no_cursor,
            abort_hotkey=abort_hotkey,
            owner=owner,
            session_id=session_id,
            ttl_seconds=ttl_seconds,
        )
        _STATE.signal_auto_shown = False
        return result


# ---------------------------------------------------------------------------
# Idle auto-hide — take the auto-shown overlay down when steering stops
# ---------------------------------------------------------------------------

_IDLE_HIDE_DEFAULT_SECONDS = 60.0


def _idle_hide_seconds() -> tuple[float | None, str | None]:
    """Resolve ``OC_SIGNAL_IDLE_HIDE`` into ``(seconds, error)``.

    Read fresh on every call, like ``OC_SIGNAL_AUTO``. ``seconds is None``
    means "no auto-hide": explicitly empty, ``0``, ``off``, or an unusable
    value (which also yields an error string the caller surfaces). *Unset* is
    not the same as empty — it takes the 60 s default, so auto-shown overlays
    clean themselves up unless the operator opts out.
    """

    raw = os.environ.get("OC_SIGNAL_IDLE_HIDE")
    if raw is None:
        return _IDLE_HIDE_DEFAULT_SECONDS, None
    raw = raw.strip()
    if not raw or raw.casefold() == "off":
        return None, None
    try:
        seconds = float(raw)
    except ValueError:
        return None, (
            f"OC_SIGNAL_IDLE_HIDE={raw!r} is not a number of seconds "
            "(expected e.g. 60, or 0/off to disable)"
        )
    if seconds <= 0:
        return None, None
    return seconds, None


def _cancel_idle_hide() -> None:
    """Stop a pending idle-hide. Safe to call when none is armed."""

    with _STATE.signal_lock:
        timer = _STATE.signal_idle_timer
        _STATE.signal_idle_timer = None
    if timer is not None:
        timer.cancel()


def _idle_hide_fire() -> None:
    """Timer callback (own thread): hide the overlay auto-signal put up.

    Re-checks ownership under the lock — between the timer firing and this
    running, a tool call may have hidden the overlay or replaced it with a
    manual one, and neither is ours to touch.
    """

    with _STATE.signal_lock:
        _STATE.signal_idle_timer = None
        if not _STATE.signal_auto_shown:
            return
        indicator = _STATE.signal_indicator
        _STATE.signal_indicator = None
        _STATE.signal_mode = ""
        _STATE.signal_auto_shown = False
        _STATE.signal_owner = ""
        _STATE.signal_session = ""
        _STATE.signal_expires_at = None
    _cancel_signal_lease()
    if indicator is not None:
        try:
            indicator.clear()
        except Exception:  # pragma: no cover - a stuck renderer must not raise here
            pass


def _arm_idle_hide() -> str | None:
    """(Re-)arm the idle-hide countdown for an auto-shown overlay.

    Called after every state-changing tool call that reached the overlay, so
    the window slides forward while the model keeps steering and only expires
    once it stops. Returns an error string for an unusable env value, else
    ``None``.
    """

    _cancel_idle_hide()
    with _STATE.signal_lock:
        if not _STATE.signal_auto_shown or _STATE.signal_indicator is None:
            return None
        seconds, error = _idle_hide_seconds()
        if seconds is None:
            return error
        timer = threading.Timer(seconds, _idle_hide_fire)
        timer.daemon = True  # never hold the server open on shutdown
        _STATE.signal_idle_timer = timer
        timer.start()
    return None


def _auto_signal_mode() -> str | None:
    """Read ``OC_SIGNAL_AUTO`` fresh on every call (no caching — tests and

    operators can flip it without a server restart). Empty / unset / ``off``
    (any case) means the feature is disabled, which is the default.
    """

    raw = os.environ.get("OC_SIGNAL_AUTO", "").strip()
    if not raw or raw.casefold() == "off":
        return None
    return raw


def _ensure_auto_signal() -> dict | None:
    """Ensure the auto-signal is visible for an approved action turn.

    Called from ``do``/``click_name``/``invoke`` after the safety gate and
    immediately before actuation, and from ``rec_replay`` immediately before
    replay. Never called from read-only tools. A no-op when the feature is off
    (``OC_SIGNAL_AUTO``
    unset) or when a signal is already visible (manual ``signal_show`` in any
    mode is never overridden). Returns ``None`` on no-op/success, or an
    ``{"auto_signal_error": ...}`` dict the caller merges into its own tool
    result — an invalid/failing auto-signal must never fail or mask the
    action it is attached to.

    Doubles as the heartbeat of the idle auto-hide: every call re-arms the
    countdown, so an auto-shown overlay survives a run of actions and only
    disappears once they stop (see ``_arm_idle_hide``).
    """

    with _STATE.signal_lock:
        auto_mode = _auto_signal_mode()
        if auto_mode is None:
            return None

        if _STATE.signal_indicator is not None:
            try:
                visible = bool(_STATE.signal_indicator.renderer.is_visible())
            except Exception:
                visible = False
            if visible:
                # Already visible — keep it, but push the idle window forward.
                return _idle_error_result(_arm_idle_hide())
            # Stale server state must not make the next action appear signaled.
            signal_hide()

        from .session import SessionMode

        try:
            SessionMode(auto_mode)
        except ValueError:
            valid = ", ".join(m.value for m in SessionMode)
            return {
                "auto_signal_error": (
                    f"OC_SIGNAL_AUTO={auto_mode!r} is not a valid session mode "
                    f"(expected one of: {valid})"
                )
            }

        try:
            shown = _show_signal_indicator(
                mode=auto_mode, agent="auto", scope="screen", arm_grace=False,
            )
        except Exception as exc:  # pragma: no cover - defensive, never break the action
            return {"auto_signal_error": f"{type(exc).__name__}: {exc}"}
        if not shown.get("visible"):
            _STATE.signal_auto_shown = False
            return None
        _STATE.signal_auto_shown = True
        return _idle_error_result(_arm_idle_hide())


def _idle_error_result(error: str | None) -> dict | None:
    """Wrap an idle-hide config error like auto-signal wraps its own."""

    return {"signal_idle_hide_error": error} if error else None


@mcp.tool(description=mcp_i18n.tool_description("signal_hide", _LANG))
def signal_hide() -> dict:
    """Idempotently hide the overlay and cancel all cleanup timers."""

    _cancel_idle_hide()
    _cancel_signal_lease()
    with _STATE.signal_lock:
        indicator = _STATE.signal_indicator
        _STATE.signal_indicator = None
        _STATE.signal_mode = ""
        _STATE.signal_auto_shown = False
        _STATE.signal_owner = ""
        _STATE.signal_session = ""
        _STATE.signal_expires_at = None
        _STATE.grace_deadline = None
    if indicator is not None:
        try:
            indicator.clear()
        except Exception:  # pragma: no cover - cleanup remains idempotent
            pass
    return {"visible": False}


@mcp.tool(description=mcp_i18n.tool_description("signal_status", _LANG))
def signal_status() -> dict:
    """Report phase/countdown/accessibility state and consume an abort message."""

    from .indicator import SignalConfig, signal_presentation

    indicator = _STATE.signal_indicator
    message = _STATE.pending_abort_message
    _STATE.pending_abort_message = None
    visible = False
    if indicator is not None:
        try:
            visible = bool(indicator.renderer.is_visible())
        except Exception:  # renderer state must not break the status call
            visible = False
    now = time.monotonic()
    with _STATE.signal_lock:
        aborted = _STATE.abort_triggered
        abort_reason = _STATE.abort_reason
        deadline = _STATE.grace_deadline
        if deadline is not None and deadline <= now:
            # Status is a valid readback even when no action arrived to consume
            # the grace window. Retire an elapsed deadline deterministically.
            _STATE.grace_deadline = None
            deadline = None
        owner = _STATE.signal_owner
        session = _STATE.signal_session
        expires_at = _STATE.signal_expires_at
        signal_mode = _STATE.signal_mode
        auto_shown = _STATE.signal_auto_shown
        idle_hide_armed = _STATE.signal_idle_timer is not None
    grace_remaining = max(0.0, deadline - now) if deadline else 0.0
    presentation = None
    if indicator is not None and signal_mode:
        cfg = indicator.config or SignalConfig()
        mode_cfg = cfg.for_mode(signal_mode)
        presentation = signal_presentation(
            base_label=indicator.last_label,
            active_color=mode_cfg.color,
            grace_color=cfg.pre_action_grace_color,
            grace_label_template=cfg.pre_action_grace_label,
            remaining_seconds=grace_remaining,
        )
    return {
        "visible": visible,
        "mode": signal_mode,
        "phase": presentation.phase if visible and presentation else "hidden",
        "owner": owner,
        "session": session,
        "expires_at": (
            datetime.fromtimestamp(expires_at, timezone.utc).isoformat()
            if expires_at is not None
            else None
        ),
        "label": presentation.visual_label if presentation else "",
        "accessible_label": presentation.accessible_label if presentation else "",
        "color": list(presentation.color) if presentation else None,
        "countdown_seconds": (
            presentation.seconds_remaining if presentation else None
        ),
        "pending_abort_message": message,
        # Who owns the overlay, and whether it is on an idle-hide countdown —
        # answers "why did the overlay disappear / why does it linger".
        "auto_shown": auto_shown,
        "idle_hide_armed": idle_hide_armed,
        # Not-Aus: NOT consumed (unlike pending_abort_message) — stays true
        # until a fresh signal_show re-arms the session, so a poll always
        # sees why every tool call keeps getting denied.
        "aborted": aborted,
        "abort_reason": abort_reason,
        "grace_remaining_seconds": round(grace_remaining, 1),
    }


@mcp.tool(description=mcp_i18n.tool_description("signal_abort", _LANG))
def signal_abort(context: str = "", channel: str = "tk") -> dict:
    """Ask the human for a short abort reason; the message is for the model."""

    if not context and _STATE.signal_indicator is not None:
        context = _STATE.signal_indicator.last_label
    return {"abort_message": _prompt_channel(channel, context)}


@mcp.tool(description=mcp_i18n.tool_description("note_observation", _LANG))
def note_observation(text: str = "", close: bool = False) -> dict:
    """Model-to-human short observation, written to a non-modal notes window.

    The mirror image of `chat` (human-to-model): this is model-to-human,
    fire-and-forget, no reply expected, never blocks. Meant for the
    work-together mode's "what does the machine see, in words" channel
    (Ticket T-20260825-767105130) -- a narrow, glanceable window instead of
    a noisy console log. Never touches the desktop/input, so it is not
    gated by the safety policy or the pre-action grace window (see `do`) --
    it is not a state-changing action.

    Independent lifecycle from the action-signal overlay (`signal_show`/
    `signal_hide`): it opens lazily on first use and stays open across many
    calls -- a session-long log, not something meant to flicker with every
    `do`/`click_name` turn. Call again with `close=true` to hide it
    explicitly; it is also torn down on server shutdown.

    Args:
        text: One short observation line. Ignored when `close=true`.
        close: Hide the window instead of writing a line.
    """
    if close:
        overlay = _STATE.observation_overlay
        if overlay is not None:
            overlay.hide()
        return {"visible": False}

    text = str(text).strip()
    if not text:
        raise ValueError("text must not be empty (pass close=true to hide instead)")

    overlay = _STATE.observation_overlay
    if overlay is None:
        from .indicator import ObservationOverlay

        overlay = ObservationOverlay()
        _STATE.observation_overlay = overlay
    overlay.show()
    overlay.note(text)
    return {"visible": overlay.is_visible(), "noted": text}


def _hide_observation_overlay() -> None:
    """atexit cleanup — mirrors `_release_held_input`'s registration below."""
    overlay = _STATE.observation_overlay
    if overlay is not None:
        try:
            overlay.hide()
        except Exception:  # pragma: no cover - shutdown must never raise
            pass


@mcp.tool(description=mcp_i18n.tool_description("chat", _LANG))
def chat(channel: str = "tk", context: str = "", shot: bool = False) -> dict:
    """Human-to-model message about screen content (+ optional screenshot).

    The message comes back as the tool result; this server never calls a
    model itself — the client (the reasoner) answers in its own channel.
    """

    message = _prompt_channel(channel, context or "Nachricht ans Modell")
    shot_path = None
    if shot:
        obs = _STATE.executor().screenshot()
        data = bytes(obs.screenshot)
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        shot_file = _module_session_dir() / f"chat_{stamp}.png"
        shot_file.write_bytes(data)
        shot_path = str(shot_file)
    return {"chat_message": message, "screenshot": shot_path}


@mcp.tool(description=mcp_i18n.tool_description("talk", _LANG))
def talk(
    key: str = "F9",
    max_seconds: float = 60.0,
    wait_timeout: float | None = None,
) -> dict:
    """Push-to-talk voice note: hold ``key``, speak, release -> WAV path.

    Zero-dependency capture via winmm MCI (Windows). STT/TTS stay model-side;
    the WAV path is returned so the client can transcribe it. This tool blocks
    while waiting for / recording the key hold.
    """

    from .indicator import parse_hotkey

    mods, vk = parse_hotkey(key)
    if mods:
        raise ValueError("key must be a single key without modifiers (e.g. F9)")

    rpt = globals().get("record_push_to_talk")
    mci_factory = globals().get("winmm_mci")
    key_probe = globals().get("async_key_down")
    if rpt is None or mci_factory is None or key_probe is None:
        from .talk import async_key_down, record_push_to_talk, winmm_mci

        rpt = rpt or record_push_to_talk
        mci_factory = mci_factory or winmm_mci
        key_probe = key_probe or async_key_down

    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = _module_session_dir() / f"ptt_{stamp}.wav"
    result = rpt(
        vk=vk,
        out_path=str(out),
        mci=mci_factory(),
        key_down=key_probe(),
        max_seconds=max_seconds,
        wait_timeout=wait_timeout,
    )
    return {
        "recorded": result.recorded,
        "wav": result.path,
        "seconds": result.seconds,
        "reason": result.reason,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _release_held_input() -> None:
    """Release anything the resident executor still holds down.

    The hold primitives (``mouse_down`` / ``key_down``) survive the tool call
    that pressed them — that is their purpose — but they must not survive the
    server. A stranded button or modifier leaves the human at the keyboard with
    an unusable desktop, so a client that dies mid-drag must not be able to
    strand one. Only touches an executor that already exists (never spins one up
    at shutdown) and never raises out of the exit path.
    """
    executor = _STATE._executor
    if executor is None:
        return
    try:
        executor.release_all()
    except Exception:  # pragma: no cover - best effort on the way out
        pass


def main() -> None:
    """Run the open-compute MCP server over stdio."""
    # Register once, at the single entry point: an atexit hook registered at
    # import (or per call) would stack up on repeated imports.
    atexit.register(_release_held_input)
    atexit.register(_hide_observation_overlay)
    try:
        mcp.run(transport="stdio")
    finally:
        signal_hide()
        _hide_observation_overlay()
        _release_held_input()


if __name__ == "__main__":
    main()
