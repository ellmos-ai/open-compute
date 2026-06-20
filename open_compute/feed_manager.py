"""Feed-Manager: dosierte Push-Auto-Injektion der Perception-Feeds.

Architecture
------------
FeedManager collects available feeds, applies change-detection per feed
(State-Feeds: SHA-256 hash; Event-Feeds: rolling window), and dispatches
observations to an InjectorSink.

InjectorSink protocol
---------------------
Two concrete implementations are provided:

1. ``LocalFileInjector`` — **working default**. Writes JSON snapshots/deltas
   to ``_state/inject_queue/`` (gitignored, local). No external dependencies.

2. ``BachInjectorAdapter`` — **non-functional stub; only the file fallback is
   real.** BACH's injection mechanism (``hub/reminder_injector.py`` → prepends
   ``[BACH-REMINDERS]`` blocks to LLM prompts) is tightly coupled to BACH's
   internal SQLite/JSON database and cannot be cleanly imported as a standalone
   transport (would pull in the entire BACH ecosystem as a hard dependency,
   violating the zero-deps-core constraint and publishability requirement).

   **The "real BACH" branch of this adapter does NOT currently work** — it is a
   placeholder, not a verified transport.  Two known gaps (see class docstring):
   (a) ``ReminderInjector.__init__`` requires a ``base_path`` argument, which the
   adapter does not pass; (b) BACH's ``inject(prompt, context) -> str`` is a
   *pure string transform* that prepends matched reminders to ``prompt`` and
   returns the result — it is not a push/store sink, it never reads an
   ``oc_block`` context key, and the adapter discards its return value.  In
   practice every push therefore degrades to the local-file fallback.
   Wiring a real BACH transport requires design work (a genuine sink, not
   ``inject``), tracked as a follow-up.  Evidence:
   ``BACH/system/hub/reminder_injector.py``.

Feed classification
-------------------
- **State-Feeds** (screenshot, uia_tree, ocr, caption): only the current value
  matters. Change-detection via SHA-256 of the serialised observation payload.
  Push fires only when the hash changes.
- **Event-Feeds** (dirwatch, action_chain): accumulate events in a rolling
  window (deque, size OC_SESSION_KEEP). Always included on push (new events).

Dosage modes (per feed, runtime-adjustable)
-------------------------------------------
- ``full``   — push the complete observation (small events, e.g. dirwatch).
- ``delta``  — push only added/removed elements vs. last push (e.g. UIA tree).
- ``notify`` — push a short notification (kind + hash) without payload
               (large feeds: screenshot, UIA when noisy). LLM can then request
               full via ``on_demand_full(feed_name)``.
- ``off``    — no automatic push; pull remains available via ``observe()``.

Sensible defaults:
  screenshot → notify, uia_windows → delta, dirwatch → full, (others) → full.

Zero-dependency guarantee
-------------------------
``import open_compute.feed_manager`` must work without any optional extras.
BACH, mss, uiautomation, watchdog are all lazy/optional.

Pure standard library at module import time.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from open_compute.feeds.base import FeedObservation


# ---------------------------------------------------------------------------
# Dosage mode
# ---------------------------------------------------------------------------

DOSAGE_MODES = frozenset({"full", "delta", "notify", "off"})

#: Default dosage per feed name.  LLM can override at runtime via set_dosage().
_DEFAULTS: dict[str, str] = {
    "screenshot":  "notify",   # large payload; push notification only
    "uia_windows": "delta",    # can be large; push diff
    "dirwatch":    "full",     # small events; always push in full
}


def _default_dosage(feed_name: str) -> str:
    return _DEFAULTS.get(feed_name, "full")


# ---------------------------------------------------------------------------
# InjectorSink protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class InjectorSink(Protocol):
    """Target that receives a prepared push payload.

    Implementations must accept ``push(feed_name, payload, dosage)`` without
    raising.  Errors should be caught internally and surfaced via logging or
    a ``last_error`` attribute.
    """

    def push(self, feed_name: str, payload: dict[str, Any], dosage: str) -> None:
        """Deliver *payload* for *feed_name* with the given *dosage* mode."""
        ...

    def status(self) -> dict[str, Any]:
        """Return a status dict (last push time, error count, …)."""
        ...


# ---------------------------------------------------------------------------
# Concrete sink: local file / queue (working default)
# ---------------------------------------------------------------------------

def _state_dir() -> pathlib.Path:
    """Return the _state directory (module-relative or OC_STATE_DIR env)."""
    env = os.environ.get("OC_STATE_DIR", "")
    if env:
        d = pathlib.Path(env)
    else:
        d = pathlib.Path(__file__).resolve().parent.parent / "_state"
    d.mkdir(parents=True, exist_ok=True)
    return d


class LocalFileInjector:
    """Working default sink: writes JSON payloads to ``_state/inject_queue/``.

    Each push creates/updates a file named ``<feed_name>.json``.  State-Feeds
    overwrite in-place (ring buffer = 1); Event-Feeds append to a rolling
    list (capped at ``max_events``).

    The inject_queue is gitignored and local — never committed.
    """

    def __init__(self, max_events: int = 20) -> None:
        self._max_events = max_events
        self._push_count: int = 0
        self._error_count: int = 0
        self._last_push_ts: float = 0.0
        self._last_error: str = ""

    def _queue_dir(self) -> pathlib.Path:
        d = _state_dir() / "inject_queue"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def push(self, feed_name: str, payload: dict[str, Any], dosage: str) -> None:
        try:
            path = self._queue_dir() / f"{feed_name}.json"
            # For event-feeds: maintain a JSON list, cap at max_events
            if payload.get("_event_feed"):
                existing: list = []
                if path.exists():
                    try:
                        raw = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(raw, list):
                            existing = raw
                        # If previous write was a plain dict, discard it (first-run edge case)
                    except Exception:  # noqa: BLE001
                        existing = []
                events = existing + [payload]
                events = events[-self._max_events:]
                path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
            else:
                # State-Feed: overwrite in-place (ring buffer = 1)
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self._push_count += 1
            self._last_push_ts = time.time()
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            self._last_error = str(exc)

    def status(self) -> dict[str, Any]:
        return {
            "sink": "LocalFileInjector",
            "push_count": self._push_count,
            "error_count": self._error_count,
            "last_push_ts": self._last_push_ts,
            "last_error": self._last_error,
        }


# ---------------------------------------------------------------------------
# Concrete sink: BACH adapter stub (documented, not active without BACH)
# ---------------------------------------------------------------------------

class BachInjectorAdapter:
    """Adapter stub for BACH's injection surface — **file fallback only**.

    **Why a stub?**
    BACH's ``ReminderInjector.inject(prompt, context) -> str`` prepends a
    ``[BACH-REMINDERS]\\n  1. ...\\n[/BACH-REMINDERS]\\n`` block.  The class is
    located in ``BACH/system/hub/reminder_injector.py``.  Importing it requires
    BACH's full runtime on ``sys.path``.  Pulling BACH in as a hard dependency
    would violate open-compute's zero-deps-core constraint and publishability
    requirement.

    **IMPORTANT — the "BACH available" branch is non-functional as written.**
    It does NOT mirror a usable BACH transport.  Two concrete mismatches with
    the real surface (verified against ``BACH/system/hub/reminder_injector.py``):

    1. **Constructor:** the real signature is
       ``ReminderInjector(base_path: Path, db=None)`` — ``base_path`` is
       required.  ``push()`` below instantiates it with no arguments
       (``bach_mod.ReminderInjector()``), which raises ``TypeError`` and is
       swallowed by the broad ``except`` → file fallback.  So this branch can
       never succeed today.
    2. **Semantics:** ``inject(prompt, context)`` is a *pure string transform*.
       It prepends matched reminders to ``prompt`` and returns the new string.
       It does NOT push or store anything, it only reads matching keys from
       ``context`` (``active_task`` / ``user_input``) and never an ``oc_block``
       key, and this adapter discards the return value.  ``inject`` is therefore
       not a sink at all.

    A real BACH transport needs design work (a genuine push/store sink, not
    ``inject``); that is deferred follow-up.  **In every current code path,
    ``push()`` writes the rendered block to a local file** — that fallback is
    the only behaviour that actually works.  All tests mock the BACH call.
    """

    def __init__(self) -> None:
        self._push_count: int = 0
        self._error_count: int = 0
        self._last_push_ts: float = 0.0
        self._last_error: str = ""
        self._bach_available: bool | None = None  # lazy check

    def _try_import_bach(self):  # type: ignore[return]
        """Lazily attempt to import BACH's reminder_injector.

        Returns the module or None if unavailable.
        """
        if self._bach_available is False:
            return None
        try:
            import importlib
            mod = importlib.import_module("hub.reminder_injector")
            self._bach_available = True
            return mod
        except ImportError:
            self._bach_available = False
            return None

    def _render_block(self, feed_name: str, payload: dict[str, Any], dosage: str) -> str:
        """Render payload as an [OC-FEEDS] block mirroring BACH's format."""
        lines = [f"feed={feed_name}", f"dosage={dosage}"]
        if dosage == "notify":
            lines.append(f"hash={payload.get('hash', '')}")
            lines.append("(full payload on demand)")
        elif dosage == "delta":
            added = payload.get("added", [])
            removed = payload.get("removed", [])
            lines.append(f"added={len(added)} elements, removed={len(removed)} elements")
        else:
            elements = payload.get("elements", [])
            text = payload.get("text", "")
            lines.append(f"elements={len(elements)}")
            if text:
                lines.append(f"text_preview={text[:120]}")
        items = "\n  ".join(lines)
        return f"[OC-FEEDS]\n  {items}\n[/OC-FEEDS]\n"

    def push(self, feed_name: str, payload: dict[str, Any], dosage: str) -> None:
        block = self._render_block(feed_name, payload, dosage)
        bach_mod = self._try_import_bach()
        if bach_mod is not None:
            try:
                # NOTE: non-functional against real BACH (see class docstring).
                # Real ctor needs base_path, and inject() is a pure transform
                # whose return is discarded — this call raises/no-ops and the
                # adapter falls through to the local-file fallback below.
                injector = bach_mod.ReminderInjector()  # type: ignore[attr-defined,call-arg]
                injector.inject(prompt="", context={"oc_block": block})
                self._push_count += 1
                self._last_push_ts = time.time()
                return
            except Exception as exc:  # noqa: BLE001
                self._error_count += 1
                self._last_error = f"BACH inject failed: {exc}"
        # Fallback: write block to local file
        try:
            fallback_dir = _state_dir() / "bach_fallback"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            (fallback_dir / f"{feed_name}.txt").write_text(block, encoding="utf-8")
            self._push_count += 1
            self._last_push_ts = time.time()
        except Exception as exc:  # noqa: BLE001
            self._error_count += 1
            self._last_error = str(exc)

    def status(self) -> dict[str, Any]:
        return {
            "sink": "BachInjectorAdapter",
            "bach_available": self._bach_available,
            "push_count": self._push_count,
            "error_count": self._error_count,
            "last_push_ts": self._last_push_ts,
            "last_error": self._last_error,
        }


# ---------------------------------------------------------------------------
# Feed classification helpers
# ---------------------------------------------------------------------------

#: Feed names that are State-Feeds (overwrite, hash-based change-detection).
STATE_FEED_NAMES = frozenset({"screenshot", "uia_windows", "uia_tree", "ocr", "caption"})
#: Feed names that are Event-Feeds (rolling window).
EVENT_FEED_NAMES = frozenset({"dirwatch", "action_chain"})


def _is_state_feed(feed_name: str) -> bool:
    """Return True when feed_name is a State-Feed (single current value)."""
    return feed_name in STATE_FEED_NAMES


def _is_event_feed(feed_name: str) -> bool:
    """Return True when feed_name is an Event-Feed (accumulates events)."""
    return feed_name in EVENT_FEED_NAMES


# ---------------------------------------------------------------------------
# Change-detection helpers
# ---------------------------------------------------------------------------

def _hash_observation(obs: FeedObservation) -> str:
    """Return a SHA-256 hex digest of the observation's canonical payload.

    For screenshot feeds the PNG bytes are hashed directly.
    For other feeds the element list is serialised to JSON (sorted keys).
    """
    data: bytes
    if obs.kind == "screenshot" and obs.elements:
        png = obs.elements[0].get("png_bytes", b"")
        data = png if isinstance(png, bytes) else json.dumps(obs.elements, sort_keys=True).encode()
    else:
        data = json.dumps(obs.elements, sort_keys=True, default=str).encode()
    return hashlib.sha256(data).hexdigest()[:16]  # first 16 hex chars (64 bits) is enough


# ---------------------------------------------------------------------------
# UIA delta helper (added/removed elements)
# ---------------------------------------------------------------------------

def _diff_uia_elements(
    prev: list[dict[str, Any]],
    curr: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute added / removed elements between two UIA element snapshots.

    Identity key: ``(name, role)``.  Elements whose key appears in ``curr``
    but not ``prev`` are "added"; those in ``prev`` but not ``curr`` are
    "removed".

    Args:
        prev: Previous element list.
        curr: Current element list.

    Returns:
        ``(added, removed)`` — each a list of element dicts.
    """
    def _key(e: dict) -> tuple:
        return (e.get("name", ""), e.get("role", ""))

    prev_keys = {_key(e) for e in prev}
    curr_keys = {_key(e) for e in curr}

    added = [e for e in curr if _key(e) not in prev_keys]
    removed = [e for e in prev if _key(e) not in curr_keys]
    return added, removed


# ---------------------------------------------------------------------------
# FeedManager
# ---------------------------------------------------------------------------

@dataclass
class FeedStatus:
    """Status snapshot for a single feed inside FeedManager.status()."""

    feed_name: str
    dosage: str
    push_count: int = 0
    last_push_ts: float = 0.0
    last_hash: str = ""
    skipped_unchanged: int = 0


class FeedManager:
    """Collects available feeds, applies change-detection, dispatches to a sink.

    Usage
    -----
    >>> from open_compute.feed_manager import FeedManager, LocalFileInjector
    >>> mgr = FeedManager(sink=LocalFileInjector())
    >>> mgr.cycle()           # one inject cycle (all feeds)
    >>> mgr.set_dosage("screenshot", "full")  # override at runtime

    Dependencies are injected for testability:
    - ``feeds``: list of PerceptionFeed instances. Defaults to
      ``available_feeds()`` when None.
    - ``sink``: InjectorSink.  Defaults to LocalFileInjector.
    """

    def __init__(
        self,
        feeds: list | None = None,
        sink: InjectorSink | None = None,
        *,
        dosage_overrides: dict[str, str] | None = None,
        event_window: int = 20,
    ) -> None:
        """Initialise FeedManager.

        Args:
            feeds: List of PerceptionFeed instances. ``None`` → lazy-load
                   ``available_feeds()`` on first access.
            sink:  InjectorSink to deliver payloads to.  Defaults to
                   ``LocalFileInjector()``.
            dosage_overrides: Initial dosage map ``{feed_name: mode}``.
                   Missing entries fall back to ``_default_dosage()``.
            event_window: Rolling-window size for Event-Feeds.
        """
        self._feeds_override = feeds  # None = lazy
        self._sink: InjectorSink = sink or LocalFileInjector()
        self._dosage: dict[str, str] = {}
        if dosage_overrides:
            for k, v in dosage_overrides.items():
                if v in DOSAGE_MODES:
                    self._dosage[k] = v

        self._event_window = event_window

        # Per-feed state
        self._last_hash: dict[str, str] = {}          # State-Feed change-detection
        self._last_elements: dict[str, list] = {}     # State-Feed previous elements for delta
        self._event_queues: dict[str, deque] = {}     # Event-Feed rolling windows
        self._status: dict[str, FeedStatus] = {}

    # ------------------------------------------------------------------
    # Feed access (lazy default)
    # ------------------------------------------------------------------

    @property
    def _feeds(self) -> list:
        if self._feeds_override is None:
            from open_compute.feeds.registry import available_feeds
            self._feeds_override = available_feeds()
        return self._feeds_override

    # ------------------------------------------------------------------
    # Dosage API (LLM-adjustable at runtime)
    # ------------------------------------------------------------------

    def set_dosage(self, feed_name: str, mode: str) -> None:
        """Set dosage mode for a feed at runtime.

        Args:
            feed_name: Name of the feed (e.g. ``"screenshot"``).
            mode: One of ``"full"``, ``"delta"``, ``"notify"``, ``"off"``.

        Raises:
            ValueError: If *mode* is not a valid dosage mode.
        """
        if mode not in DOSAGE_MODES:
            raise ValueError(f"Invalid dosage mode {mode!r}. Valid: {sorted(DOSAGE_MODES)}")
        self._dosage[feed_name] = mode

    def get_dosage(self, feed_name: str) -> str:
        """Return current dosage mode for *feed_name*."""
        return self._dosage.get(feed_name, _default_dosage(feed_name))

    # ------------------------------------------------------------------
    # On-demand full pull (LLM bypass of dosage)
    # ------------------------------------------------------------------

    def on_demand_full(self, feed_name: str, window: str | None = None) -> FeedObservation | None:
        """Return a full FeedObservation for feed_name, bypassing dosage.

        The LLM can call this when ``notify`` was pushed and it wants the
        full payload.  Returns None if the feed is not available.

        Args:
            feed_name: Name of the feed to pull from.
            window:    Optional window hint forwarded to feed.observe().
        """
        for feed in self._feeds:
            if feed.name == feed_name:
                try:
                    return feed.observe(window=window)
                except Exception:  # noqa: BLE001
                    return None
        return None

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    def cycle(self, window: str | None = None) -> dict[str, Any]:
        """Run one inject cycle over all available feeds.

        For each feed:
        1. Check dosage — skip if ``off``.
        2. Observe the feed.
        3. Apply change-detection (State-Feeds: hash; Event-Feeds: new events).
        4. Build dosage-appropriate payload.
        5. Dispatch to sink.

        Args:
            window: Optional window hint forwarded to all feeds.

        Returns:
            Summary dict ``{feed_name: "pushed" | "skipped_unchanged" | "skipped_off" | "error"}``.
        """
        summary: dict[str, str] = {}

        for feed in self._feeds:
            name = feed.name
            dosage = self.get_dosage(name)

            if dosage == "off":
                summary[name] = "skipped_off"
                continue

            try:
                obs: FeedObservation = feed.observe(window=window)
            except Exception as exc:  # noqa: BLE001
                summary[name] = f"error:{exc}"
                continue

            if _is_state_feed(name):
                result = self._handle_state_feed(name, obs, dosage)
            else:
                # Event-Feed: all observations are new events by definition
                result = self._handle_event_feed(name, obs, dosage)

            summary[name] = result

        return summary

    # ------------------------------------------------------------------
    # State-Feed handling
    # ------------------------------------------------------------------

    def _handle_state_feed(self, name: str, obs: FeedObservation, dosage: str) -> str:
        new_hash = _hash_observation(obs)
        prev_hash = self._last_hash.get(name, "")

        if new_hash == prev_hash and prev_hash:
            status = self._get_or_create_status(name)
            status.skipped_unchanged += 1
            return "skipped_unchanged"

        self._last_hash[name] = new_hash

        payload = self._build_state_payload(name, obs, dosage, new_hash)
        self._sink.push(name, payload, dosage)

        # Update previous elements for future delta computation
        self._last_elements[name] = list(obs.elements)

        status = self._get_or_create_status(name)
        status.push_count += 1
        status.last_push_ts = obs.ts
        status.last_hash = new_hash
        return "pushed"

    def _build_state_payload(
        self,
        name: str,
        obs: FeedObservation,
        dosage: str,
        obs_hash: str,
    ) -> dict[str, Any]:
        """Build the payload dict for a State-Feed observation."""
        if dosage == "notify":
            return {
                "kind": obs.kind,
                "hash": obs_hash,
                "ts": obs.ts,
                "dosage": "notify",
                "message": f"{name} changed (hash={obs_hash}); request full via on_demand_full('{name}')",
            }
        if dosage == "delta":
            prev = self._last_elements.get(name, [])
            added, removed = _diff_uia_elements(prev, obs.elements)
            return {
                "kind": obs.kind,
                "hash": obs_hash,
                "ts": obs.ts,
                "dosage": "delta",
                "added": added,
                "removed": removed,
                "total_current": len(obs.elements),
            }
        # dosage == "full"
        # Strip large binary blobs from screenshot payload (keep metadata)
        elements = obs.elements
        if obs.kind == "screenshot":
            elements = [
                {k: v for k, v in e.items() if k != "png_bytes"}
                for e in elements
            ]
        return {
            "kind": obs.kind,
            "hash": obs_hash,
            "ts": obs.ts,
            "dosage": "full",
            "elements": elements,
            "text": obs.text,
        }

    # ------------------------------------------------------------------
    # Event-Feed handling
    # ------------------------------------------------------------------

    def _handle_event_feed(self, name: str, obs: FeedObservation, dosage: str) -> str:
        if name not in self._event_queues:
            self._event_queues[name] = deque(maxlen=self._event_window)

        q = self._event_queues[name]
        new_events = obs.elements  # dirwatch elements are events
        if not new_events:
            return "skipped_unchanged"

        for ev in new_events:
            q.append(ev)

        payload = {
            "kind": obs.kind,
            "ts": obs.ts,
            "dosage": dosage,
            "_event_feed": True,
            "events": list(new_events),  # only new events, not entire window
            "window_size": len(q),
        }
        self._sink.push(name, payload, dosage)

        status = self._get_or_create_status(name)
        status.push_count += 1
        status.last_push_ts = obs.ts
        return "pushed"

    # ------------------------------------------------------------------
    # Status API
    # ------------------------------------------------------------------

    def _get_or_create_status(self, name: str) -> FeedStatus:
        if name not in self._status:
            self._status[name] = FeedStatus(
                feed_name=name,
                dosage=self.get_dosage(name),
            )
        return self._status[name]

    def status(self) -> dict[str, Any]:
        """Return status dict: feeds, dosages, push counts, sink status.

        Returns:
            Dict with keys:
            - ``"feeds"``: list of feed names (from registry).
            - ``"dosages"``: ``{feed_name: dosage_mode}`` for all feeds.
            - ``"feed_status"``: per-feed ``FeedStatus`` as dicts.
            - ``"sink"``: sink.status() result.
        """
        feed_names = [f.name for f in self._feeds]
        dosages = {n: self.get_dosage(n) for n in feed_names}
        feed_status = {n: vars(s) for n, s in self._status.items()}
        return {
            "feeds": feed_names,
            "dosages": dosages,
            "feed_status": feed_status,
            "sink": self._sink.status(),
        }
