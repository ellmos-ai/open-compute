"""On-screen signaling for active screen functions (compute/companion modes).

Befund 2026-07-31: :class:`open_compute.cooperative.OwnershipIndicator` shipped
only as a Protocol plus ``NullOwnershipIndicator`` — intentionally no renderer.
This module is the renderer side:

* a colored, glowing border overlay around the virtual screen (Windows,
  click-through, topmost) with per-mode colors — red while the model may act
  (CONTROL), blue while it only watches (OBSERVE), plus a neon ring around the
  mouse cursor and a small status strip,
* an abort channel: when the human aborts compute mode, a short reason is
  captured (console or topmost Tk input box) and handed back to the model.

Layering mirrors ``cooperative.py``: every side effect sits behind an injected
protocol (:class:`OverlayRenderer`, :class:`AbortChannel`), so headless tests
run everywhere and the Win32 implementation stays separately gated. The Win32
overlay is constructed lazily and raises on non-Windows platforms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import queue
from string import Formatter
import sys
import tempfile
import threading
import time
from typing import Callable, Protocol

from .session import SessionMode


# Mode -> (label, (r, g, b)).
# Rot = Modell darf handeln (CONTROL). Blau = Modell beobachtet nur (OBSERVE) —
# die andere Farbe fuer "nur zusehen / gemeinsam etwas anschauen".
MODE_SIGNALS: dict[SessionMode, tuple[str, tuple[int, int, int]]] = {
    SessionMode.OBSERVE: ("observe - Modell schaut zu", (0, 150, 255)),
    SessionMode.COMPANION: ("companion - gemeinsam", (0, 200, 120)),
    SessionMode.ASSIST: ("assist", (255, 200, 0)),
    SessionMode.HANDOFF: ("handoff - Übergabe", (255, 130, 0)),
    SessionMode.CONTROL: ("CONTROL - Modell steuert", (255, 40, 60)),
    SessionMode.PAUSED: ("paused", (150, 150, 150)),
}

# The pre-action phase is intentionally distinct from every mode color. It is
# static (no flashing/pulsing), so the safety state remains understandable when
# Windows animations are disabled or reduced-motion preferences are active.
DEFAULT_PRE_ACTION_GRACE_COLOR = (176, 86, 255)
DEFAULT_PRE_ACTION_GRACE_LABEL = "Start in {seconds} Sekunden"


def signal_for_mode(mode: SessionMode | str) -> tuple[str, tuple[int, int, int]]:
    """Return the (label, rgb) signal for a session mode."""

    return MODE_SIGNALS[SessionMode(mode)]


# --- Hotkey parsing (headless-testable) --------------------------------------

_MOD_KEYS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
}
_VK_KEYS = {
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "pause": 0x13,
}
for _fnum in range(1, 13):
    _VK_KEYS[f"f{_fnum}"] = 0x6F + _fnum


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse ``ctrl+alt+esc`` / ``f9`` / ``shift+a`` into (modifiers, vk)."""

    parts = [p.strip().casefold() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("hotkey spec must not be empty")
    mods = 0
    key: int | None = None
    for part in parts:
        if part in _MOD_KEYS:
            mods |= _MOD_KEYS[part]
            continue
        if key is not None:
            raise ValueError(f"hotkey has more than one key: {spec!r}")
        if part in _VK_KEYS:
            key = _VK_KEYS[part]
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        else:
            raise ValueError(f"unknown hotkey key: {part!r}")
    if key is None:
        raise ValueError(f"hotkey needs a non-modifier key: {spec!r}")
    return mods, key


# --- Signal configuration (JSON file, defaults = built-in palette) -----------

def _validate_color(value: object) -> tuple[int, int, int]:
    parts = tuple(int(c) for c in value)  # type: ignore[union-attr]
    if len(parts) != 3 or any(not 0 <= c <= 255 for c in parts):
        raise ValueError(f"color must be three 0..255 ints, got {value!r}")
    return parts  # type: ignore[return-value]


def _validate_grace_label_template(value: object) -> str:
    template = str(value).strip()
    fields = {
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(template)
        if field_name is not None
    }
    if fields != {"seconds"}:
        raise ValueError(
            "pre_action_grace_label must contain exactly the {seconds} placeholder"
        )
    try:
        rendered = template.format(seconds=1)
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError("invalid pre_action_grace_label format") from exc
    if not rendered.strip():
        raise ValueError("pre_action_grace_label must render visible text")
    return template


@dataclass(frozen=True)
class SignalPresentation:
    """One deterministic visual/accessibility state of the signal overlay."""

    phase: str
    color: tuple[int, int, int]
    visual_label: str
    accessible_label: str
    seconds_remaining: int | None


def signal_presentation(
    *,
    base_label: str,
    active_color: tuple[int, int, int],
    grace_color: tuple[int, int, int],
    grace_label_template: str,
    remaining_seconds: float,
) -> SignalPresentation:
    """Build the visible and screenreader-readable signal state.

    ``ceil`` ensures a configured 20-second grace starts at 20 rather than 19.
    The countdown is text-first and uses a static color; color is redundant
    information, never the only way to understand the phase.
    """

    if remaining_seconds > 0:
        seconds = max(1, math.ceil(remaining_seconds))
        countdown = grace_label_template.format(seconds=seconds)
        return SignalPresentation(
            phase="countdown",
            color=grace_color,
            visual_label=f"{countdown} | ABBRUCH stoppt sofort",
            accessible_label=(
                f"Open Compute. {countdown}. {base_label}. "
                "Abbruch ist jederzeit möglich."
            ),
            seconds_remaining=seconds,
        )
    return SignalPresentation(
        phase="active",
        color=active_color,
        visual_label=base_label,
        accessible_label=(
            f"Open Compute aktiv. {base_label}. Abbruch ist jederzeit möglich."
        ),
        seconds_remaining=None,
    )


@dataclass
class SignalModeConfig:
    """Per-mode signal settings.

    ``border`` and ``cursor`` are independently toggleable — the use case
    "cursor highlight only when the model actively drives, and no screen
    border then" is simply ``control: {border: false, cursor: true}``.
    A mode with ``enabled: false`` renders nothing at all.
    """

    enabled: bool = True
    color: tuple[int, int, int] = (255, 255, 255)
    label: str = ""
    border: bool = True
    cursor: bool = True

    def __post_init__(self) -> None:
        self.color = _validate_color(self.color)


@dataclass
class SignalConfig:
    """Signal overlay configuration.

    Defaults are exactly the built-in palette (:data:`MODE_SIGNALS`) with
    border + cursor on for every mode. A JSON file only needs to carry the
    overrides — unknown keys are ignored, unspecified modes keep defaults.
    """

    modes: dict[SessionMode, SignalModeConfig] = field(default_factory=dict)
    thickness: int = 6
    abort_hotkey: str | None = None
    # Karenzzeit vor der ersten zustandsaendernden Aktion / dem ersten Screenshot
    # einer Sitzung (Not-Aus-Feature, Ticket T-20260818-895473048). 0 = sofort,
    # kein Countdown -- das ist die einzige Abschaltung, die zaehlt: sie steht
    # in dieser Datei (User-Config), nicht in einem vom Modell waehlbaren
    # Aufrufparameter (Ticket T-20260825-540085216, Bypass-Haertung).
    # Default 4.0s (Bereich 3-5s laut Ticket, 4.0 als Mittelwert) -- vorher
    # 20.0s, vom User als zu lang empfunden.
    pre_action_grace_seconds: float = 4.0
    # Aktivitaets-Cooldown (Ticket T-20260825-540085216, Teil 1b): innerhalb
    # dieses Zeitraums nach einer bereits abgewarteten/erfuellten Karenzzeit
    # erscheint KEIN neues Fenster erneut -- erst danach wieder. 0 = Cooldown
    # aus (jede Aktion wartet wieder die volle Karenzzeit ab, altes Verhalten).
    # Getrennt von pre_action_grace_seconds, damit beide unabhaengig
    # einstellbar bleiben (kurze Karenz + langer Cooldown ist z.B. sinnvoll
    # fuer haeufige, kurze Aktionsfolgen).
    grace_cooldown_seconds: float = 120.0
    # Eigene, statische Farbe und textliche (also nicht nur farbliche)
    # Kennzeichnung der Vorlaufphase. Der Platzhalter ist verpflichtend, damit
    # eine Konfiguration den Countdown nicht versehentlich unsichtbar macht.
    pre_action_grace_color: tuple[int, int, int] = DEFAULT_PRE_ACTION_GRACE_COLOR
    pre_action_grace_label: str = DEFAULT_PRE_ACTION_GRACE_LABEL
    # Freitext ODER 1-Klick: die Vorschlagsliste fuer den Abbruch-Dialog
    # (TkAbortChannel). Leer = nur Freitext, wie bisher.
    abort_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        merged = self.default_modes()
        merged.update({SessionMode(k): v for k, v in self.modes.items()})
        self.modes = merged
        if not 2 <= self.thickness <= 40:
            raise ValueError("thickness must be in 2..40")
        if self.abort_hotkey is not None:
            parse_hotkey(self.abort_hotkey)  # validate eagerly
        if (
            not math.isfinite(self.pre_action_grace_seconds)
            or self.pre_action_grace_seconds < 0
        ):
            raise ValueError("pre_action_grace_seconds must be finite and >= 0")
        if (
            not math.isfinite(self.grace_cooldown_seconds)
            or self.grace_cooldown_seconds < 0
        ):
            raise ValueError("grace_cooldown_seconds must be finite and >= 0")
        self.pre_action_grace_color = _validate_color(self.pre_action_grace_color)
        self.pre_action_grace_label = _validate_grace_label_template(
            self.pre_action_grace_label
        )
        self.abort_reasons = tuple(
            str(reason).strip() for reason in self.abort_reasons if str(reason).strip()
        )

    @staticmethod
    def default_modes() -> dict[SessionMode, SignalModeConfig]:
        return {
            mode: SignalModeConfig(color=color, label=label)
            for mode, (label, color) in MODE_SIGNALS.items()
        }

    def for_mode(self, mode: SessionMode | str) -> SignalModeConfig:
        return self.modes[SessionMode(mode)]

    @classmethod
    def from_dict(cls, data: dict) -> "SignalConfig":
        if not isinstance(data, dict):
            raise ValueError("signal config must be a JSON object")
        defaults = cls.default_modes()
        modes: dict[SessionMode, SignalModeConfig] = {}
        for name, raw in dict(data.get("modes", {})).items():
            mode = SessionMode(name)  # unknown mode names fail loud
            if not isinstance(raw, dict):
                raise ValueError(f"mode config for {name!r} must be an object")
            base = defaults[mode]
            modes[mode] = SignalModeConfig(
                enabled=bool(raw.get("enabled", base.enabled)),
                color=_validate_color(raw.get("color", base.color)),
                label=str(raw.get("label", base.label)),
                border=bool(raw.get("border", base.border)),
                cursor=bool(raw.get("cursor", base.cursor)),
            )
        hotkey = data.get("abort_hotkey")
        return cls(
            modes=modes,
            thickness=int(data.get("thickness", 6)),
            abort_hotkey=str(hotkey) if hotkey else None,
            pre_action_grace_seconds=float(data.get("pre_action_grace_seconds", 4.0)),
            grace_cooldown_seconds=float(data.get("grace_cooldown_seconds", 120.0)),
            pre_action_grace_color=_validate_color(
                data.get("pre_action_grace_color", DEFAULT_PRE_ACTION_GRACE_COLOR)
            ),
            pre_action_grace_label=str(
                data.get("pre_action_grace_label", DEFAULT_PRE_ACTION_GRACE_LABEL)
            ),
            abort_reasons=tuple(str(r) for r in data.get("abort_reasons", ())),
        )

    @classmethod
    def load(cls, path: str | Path) -> "SignalConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        return {
            "thickness": self.thickness,
            "abort_hotkey": self.abort_hotkey,
            "pre_action_grace_seconds": self.pre_action_grace_seconds,
            "grace_cooldown_seconds": self.grace_cooldown_seconds,
            "pre_action_grace_color": list(self.pre_action_grace_color),
            "pre_action_grace_label": self.pre_action_grace_label,
            "abort_reasons": list(self.abort_reasons),
            "modes": {
                mode.value: {
                    "enabled": cfg.enabled,
                    "color": list(cfg.color),
                    "label": cfg.label,
                    "border": cfg.border,
                    "cursor": cfg.cursor,
                }
                for mode, cfg in self.modes.items()
            },
        }

    def save(self, path: str | Path) -> None:
        """Atomic JSON write (temp file + os.replace), like SessionStore."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


class OverlayRenderer(Protocol):
    """Renderer seam. Implementations must be click-through and non-modal."""

    def show(
        self,
        *,
        color: tuple[int, int, int],
        label: str,
        border: bool = True,
        cursor: bool = True,
    ) -> None: ...

    def hide(self) -> None: ...

    def is_visible(self) -> bool: ...


class AbortChannel(Protocol):
    """Where the human abort reason comes from (console, Tk overlay, ...)."""

    def prompt_reason(self, *, context: str) -> str | None: ...


@dataclass
class NullOverlayRenderer:
    """Headless default; renders nothing, like ``NullOwnershipIndicator``."""

    def show(
        self,
        *,
        color: tuple[int, int, int],
        label: str,
        border: bool = True,
        cursor: bool = True,
    ) -> None:
        return None

    def hide(self) -> None:
        return None

    def is_visible(self) -> bool:
        return False


@dataclass
class NullAbortChannel:
    """Default abort channel: never captures a message."""

    def prompt_reason(self, *, context: str) -> str | None:
        return None


@dataclass
class ConsoleAbortChannel:
    """stdin prompt; usable in terminals, never pops a window."""

    input_fn: Callable[[str], str] = input

    def prompt_reason(self, *, context: str) -> str | None:
        try:
            text = self.input_fn(
                "[open-compute] Abbruch"
                + (f" ({context})" if context else "")
                + " — kurzer Grund fürs Modell (leer = keiner): "
            )
        except (EOFError, KeyboardInterrupt):
            return None
        text = text.strip()
        return text or None


@dataclass
class TkAbortChannel:
    """Small topmost Tk input box — the abort overlay with reason entry.

    tkinter is stdlib; the import is lazy so the module stays import-safe
    where Tk is missing. Returns the entered text, or ``None`` on cancel,
    empty input, or timeout.

    ``reasons`` renders one 1-click button per configured quick reason
    (:attr:`SignalConfig.abort_reasons`) above the free-text entry — either
    picks the abort message, matching "Freitext ODER 1-Klick aus
    konfigurierbarer Liste" (Ticket T-20260818-895473048). Empty (default)
    keeps the original free-text-only dialog.
    """

    timeout_seconds: float = 60.0
    reasons: tuple[str, ...] = ()

    def prompt_reason(self, *, context: str) -> str | None:
        import tkinter as tk

        result: dict[str, str | None] = {"text": None}
        root = tk.Tk()
        root.title("open-compute — Abbruch")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        if context:
            tk.Label(root, text=context, anchor="w").pack(
                fill="x", padx=10, pady=(10, 0)
            )
        if self.reasons:
            tk.Label(root, text="Schnellauswahl (1 Klick):", anchor="w").pack(
                fill="x", padx=10, pady=(10, 0)
            )
            quick = tk.Frame(root)
            quick.pack(fill="x", padx=10, pady=(2, 0))

            def _pick(value: str) -> None:
                result["text"] = value
                root.destroy()

            for reason in self.reasons:
                tk.Button(
                    quick, text=reason, command=lambda value=reason: _pick(value)
                ).pack(side="left", padx=(0, 4), pady=2)
        tk.Label(
            root,
            text="...oder eigener Grund fürs Modell (wird mitgesendet):",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(10, 0))
        entry = tk.Entry(root, width=50)
        entry.pack(fill="x", padx=10, pady=10)
        entry.focus_set()

        def _ok() -> None:
            text = entry.get().strip()
            result["text"] = text or None
            root.destroy()

        def _cancel() -> None:
            result["text"] = None
            root.destroy()

        buttons = tk.Frame(root)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(buttons, text="Senden", command=_ok).pack(side="left")
        tk.Button(buttons, text="Ohne Grund", command=_cancel).pack(
            side="left", padx=(8, 0)
        )
        root.bind("<Return>", lambda _event: _ok())
        root.bind("<Escape>", lambda _event: _cancel())
        if self.timeout_seconds > 0:
            root.after(int(self.timeout_seconds * 1000), _cancel)
        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
        x = (root.winfo_screenwidth() - width) // 2
        y = max(0, (root.winfo_screenheight() - height) // 3)
        root.geometry(f"+{x}+{y}")
        root.mainloop()
        return result["text"]


@dataclass
class ObservationOverlay:
    """Small, non-modal, always-on-top notes window — model writes, human
    reads (Ticket T-20260825-767105130, work-together mode, Baustein B).

    The mirror image of :class:`TkAbortChannel`: that one is human->model
    (blocks, asks a question, returns an answer); this one is model->human
    (fire-and-forget, never blocks, never asks anything). "Rauschfreier
    Sichtkanal" per the ticket — a narrow, glanceable window the human can
    read alongside whatever else they are doing, instead of a noisy console
    log that is hard to read in parallel with real window work.

    tkinter's ``mainloop()`` is blocking, so — unlike ``TkAbortChannel``,
    which is only ever used from a dedicated abort-handling call — this
    overlay runs its own ``Tk`` root on a **dedicated background thread**
    that stays alive across many :meth:`note` calls; the MCP tool call
    (``note_observation``) must return immediately, not wait for the human.
    New lines cross the thread boundary through a plain :class:`queue.Queue`
    (tkinter widgets are not safe to touch from another thread), drained by
    ``root.after()`` polling roughly every 100ms — the same "communicate via
    a thread-safe primitive, never touch Tk state directly" discipline
    :class:`WindowsBorderOverlay` already uses for its cursor-ring thread.

    Best-effort no-focus-steal (Windows): the previously foreground window
    is restored right after the Tk window is created, so opening/growing
    the overlay does not pull keyboard focus away from whatever application
    the human is actively using. This is a best effort, not a guarantee —
    plain tkinter has no reliable cross-platform "create without ever
    taking focus" primitive; a true guarantee would need the same
    ctypes/WS_EX_NOACTIVATE-level approach as ``WindowsBorderOverlay``,
    which is more machinery than this ticket's "klein halten" calls for.
    """

    title: str = "Open Compute — Beobachtungen"
    max_lines: int = 200
    poll_interval_ms: int = 100
    start_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._started_ok = False
        self.error: BaseException | None = None

    def show(self) -> None:
        """Start the overlay thread if not already running. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._started_ok = False
        self.error = None
        self._thread = threading.Thread(
            target=self._run, name="oc-observation-overlay", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=self.start_timeout_seconds)
        if self.error is not None:
            raise self.error

    def note(self, text: str) -> None:
        """Queue one line for the overlay. No-op on blank text."""
        text = text.strip()
        if not text:
            return
        self._queue.put(text)

    def hide(self) -> None:
        """Stop the overlay thread and destroy the window. Idempotent."""
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._thread = None

    def is_visible(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:  # pragma: no cover - exercised via a fake in tests
        try:
            import tkinter as tk

            prev_hwnd = None
            if sys.platform == "win32":
                import ctypes

                prev_hwnd = ctypes.windll.user32.GetForegroundWindow()

            root = tk.Tk()
            root.title(self.title)
            root.attributes("-topmost", True)
            root.resizable(True, True)
            root.geometry("360x220+40+40")
            text_widget = tk.Text(root, wrap="word", state="disabled")
            text_widget.pack(fill="both", expand=True, padx=6, pady=6)
            root.protocol("WM_DELETE_WINDOW", self._stop.set)

            if prev_hwnd:
                # Restore focus to whatever the human was using — this new
                # window otherwise takes it just by being created.
                ctypes.windll.user32.SetForegroundWindow(prev_hwnd)

            def _poll() -> None:
                if self._stop.is_set():
                    root.destroy()
                    return
                appended = False
                while True:
                    try:
                        line = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    text_widget.configure(state="normal")
                    text_widget.insert("end", line + "\n")
                    overflow = int(text_widget.index("end-1c").split(".")[0]) - self.max_lines
                    if overflow > 0:
                        text_widget.delete("1.0", f"{overflow + 1}.0")
                    text_widget.configure(state="disabled")
                    appended = True
                if appended:
                    text_widget.see("end")
                root.after(self.poll_interval_ms, _poll)

            self._started_ok = True
            self._ready.set()
            root.after(self.poll_interval_ms, _poll)
            root.mainloop()
        except BaseException as exc:  # noqa: BLE001 - surfaced via .error, never crashes the caller's thread
            self.error = exc
            self._ready.set()


@dataclass
class ScreenSignalIndicator:
    """``OwnershipIndicator`` implementation: renders mode color + agent + scope.

    Drop-in for ``CooperativeOrchestrator(indicator=…)``. All rendering goes
    through the injected :class:`OverlayRenderer`; the abort message goes
    through the injected :class:`AbortChannel` and is forwarded to
    ``on_abort_message`` (the caller passes it on to the model).
    """

    renderer: OverlayRenderer = field(default_factory=NullOverlayRenderer)
    abort_channel: AbortChannel = field(default_factory=NullAbortChannel)
    on_abort_message: Callable[[str], None] | None = None
    config: SignalConfig | None = None
    last_label: str = ""

    def show(self, *, agent: str, scope: str, mode: SessionMode) -> None:
        cfg = self.config or SignalConfig()
        mode_cfg = cfg.for_mode(mode)
        if not mode_cfg.enabled:
            # Mode is switched off in the config: render nothing at all.
            self.clear()
            return
        label_text = mode_cfg.label or signal_for_mode(mode)[0]
        self.last_label = f"{agent} | {label_text} | {scope}"
        self.renderer.show(
            color=mode_cfg.color,
            label=self.last_label,
            border=mode_cfg.border,
            cursor=mode_cfg.cursor,
        )

    def clear(self) -> None:
        self.renderer.hide()
        self.last_label = ""

    def capture_abort_message(self, *, context: str = "") -> str | None:
        """Collect the human's short abort message and forward it to the model.

        Returns the message (or ``None``); when a message exists and
        ``on_abort_message`` is set, the callback receives it.
        """

        reason = self.abort_channel.prompt_reason(
            context=context or self.last_label
        )
        if reason and self.on_abort_message is not None:
            self.on_abort_message(reason)
        return reason


# ---------------------------------------------------------------------------
# Windows overlay renderer (ctypes, lazy, click-through)
# ---------------------------------------------------------------------------

_BORDER_ALPHA = 235
_GLOW_ALPHA = 90
_CURSOR_RING = 48
_ABORT_DEBOUNCE_SECONDS = 0.5

# Abort button (Ticket T-20260818-895473048, "immer sichtbares, klickbares
# Abort-Element"): a small opaque, NON-click-through popup drawn top-right of
# the virtual desktop, independent of the abort hotkey.
_ABORT_BUTTON_W = 132
_ABORT_BUTTON_H = 34
_ABORT_BUTTON_MARGIN = 10
_ABORT_BUTTON_COLOR = (222, 32, 42)
_ABORT_BUTTON_LABEL = "✖ ABBRUCH"


def _abort_button_rect(
    vx: int,
    vy: int,
    vw: int,
    vh: int,
    glow: int,
    *,
    width: int = _ABORT_BUTTON_W,
    height: int = _ABORT_BUTTON_H,
    margin: int = _ABORT_BUTTON_MARGIN,
) -> tuple[int, int, int, int]:
    """Placement for the abort button: top-right, clear of the glow frame.

    Pure arithmetic (no Win32 call) so it is unit-testable on any platform.
    """

    x = vx + vw - width - margin - glow
    y = vy + glow + margin
    return x, y, width, height


class WindowsBorderOverlay:
    """Glowing screen border + neon cursor ring + status strip (Windows-only).

    Implementation: layered, topmost, click-through popup windows on a
    dedicated thread with its own message loop. The border is a bright inner
    frame plus a dimmer outer glow frame; the cursor ring follows the pointer
    (~30 Hz). Construction and use are confined to :meth:`show`/:meth:`hide`;
    instantiating on non-Windows raises ``RuntimeError``.
    """

    def __init__(
        self,
        *,
        thickness: int = 6,
        border: bool = True,
        cursor_ring: bool = True,
        on_abort: Callable[[], None] | None = None,
        abort_hotkey: str | None = None,
        grace_seconds: float = 0.0,
        grace_color: tuple[int, int, int] = DEFAULT_PRE_ACTION_GRACE_COLOR,
        grace_label_template: str = DEFAULT_PRE_ACTION_GRACE_LABEL,
    ) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsBorderOverlay is Windows-only")
        if not 2 <= thickness <= 40:
            raise ValueError("thickness must be in 2..40")
        if not math.isfinite(grace_seconds) or grace_seconds < 0:
            raise ValueError("grace_seconds must be finite and >= 0")
        self._thickness = thickness
        self._border = border
        self._cursor_ring = cursor_ring
        self._on_abort = on_abort
        # Parsed eagerly so a bad spec fails at construction, not in the thread.
        self._abort_hotkey = parse_hotkey(abort_hotkey) if abort_hotkey else None
        # Karenzzeit vor der ersten Aktion — nur eine Anzeige-/Timing-Angabe;
        # das eigentliche Blockieren macht der Aufrufer (mcp_server). Das
        # Overlay zeigt die aus der Konfiguration berechnete Restzeit an.
        self._grace_seconds = float(grace_seconds)
        self._grace_color = _validate_color(grace_color)
        self._grace_label_template = _validate_grace_label_template(
            grace_label_template
        )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._visible = False
        self.error: BaseException | None = None
        self._presentation_lock = threading.RLock()
        self._presentation = SignalPresentation(
            phase="hidden",
            color=(0, 0, 0),
            visual_label="",
            accessible_label="",
            seconds_remaining=None,
        )
        # Debounce a rapid double-fire (double-click on the abort button, or
        # a hotkey held slightly too long despite MOD_NOREPEAT) so the human
        # never sees the reason dialog pop up twice for one intent.
        self._last_abort_fire: float | None = None

    # -- public seam -----------------------------------------------------

    def show(
        self,
        *,
        color: tuple[int, int, int],
        label: str,
        border: bool | None = None,
        cursor: bool | None = None,
    ) -> None:
        self.hide()
        self._stop.clear()
        self.error = None
        use_border = self._border if border is None else border
        use_cursor = self._cursor_ring if cursor is None else cursor
        initial = signal_presentation(
            base_label=str(label),
            active_color=tuple(int(c) for c in color),
            grace_color=self._grace_color,
            grace_label_template=self._grace_label_template,
            remaining_seconds=(
                self._grace_seconds
                if self._grace_seconds > 0 and self._on_abort is not None
                else 0.0
            ),
        )
        self._set_presentation(initial)
        self._thread = threading.Thread(
            target=self._run,
            args=(tuple(int(c) for c in color), str(label), use_border, use_cursor),
            name="oc-border-overlay",
            daemon=True,
        )
        self._thread.start()
        self._visible = True

    def hide(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._visible = False
        self._set_presentation(
            SignalPresentation(
                phase="hidden",
                color=(0, 0, 0),
                visual_label="",
                accessible_label="",
                seconds_remaining=None,
            )
        )

    def is_visible(self) -> bool:
        return self._visible and self.error is None

    def _set_presentation(self, presentation: SignalPresentation) -> None:
        with self._presentation_lock:
            self._presentation = presentation

    def status_snapshot(self) -> dict[str, object]:
        """Thread-safe phase snapshot for MCP status/readback."""

        with self._presentation_lock:
            presentation = self._presentation
        return {
            "phase": presentation.phase,
            "color": list(presentation.color),
            "visual_label": presentation.visual_label,
            "accessible_label": presentation.accessible_label,
            "countdown_seconds": presentation.seconds_remaining,
        }

    def _fire_abort(self) -> None:
        """Invoke the abort callback; failures surface via ``self.error``.

        Debounced: a second trigger (button double-click, hotkey bounce)
        within :data:`_ABORT_DEBOUNCE_SECONDS` of the last one is dropped —
        one human abort gesture must not fire the kill switch / reason
        dialog twice.
        """

        now = time.monotonic()
        if (
            self._last_abort_fire is not None
            and now - self._last_abort_fire < _ABORT_DEBOUNCE_SECONDS
        ):
            return
        self._last_abort_fire = now
        try:
            if self._on_abort is not None:
                self._on_abort()
        except BaseException as exc:
            self.error = exc

    # -- thread internals ------------------------------------------------

    def _run(
        self,
        color: tuple[int, int, int],
        label: str,
        border: bool = True,
        cursor: bool = True,
    ) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32

        # 64-bit safety: handle-returning calls must not truncate to c_int.
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = wintypes.LPARAM
        user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.BeginPaint.restype = wintypes.HDC
        user32.EndPaint.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetClientRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT),
        ]
        user32.FillRect.argtypes = [
            wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH,
        ]
        user32.DrawTextW.argtypes = [
            wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
            ctypes.POINTER(wintypes.RECT), wintypes.UINT,
        ]
        user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t,
        ]
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetLayeredWindowAttributes.argtypes = [
            wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD,
        ]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT,
        ]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
        ]
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.InvalidateRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL,
        ]
        user32.UpdateWindow.argtypes = [wintypes.HWND]
        user32.NotifyWinEvent.argtypes = [
            wintypes.DWORD, wintypes.HWND, ctypes.c_long, ctypes.c_long,
        ]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        gdi32.CreatePen.restype = wintypes.HANDLE
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.GetStockObject.argtypes = [ctypes.c_int]
        gdi32.GetStockObject.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
        gdi32.Ellipse.argtypes = [wintypes.HDC] + [ctypes.c_int] * 4
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        WNDPROC = ctypes.WINFUNCTYPE(
            wintypes.LPARAM,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        def _colorref(rgb: tuple[int, int, int]) -> int:
            r, g, b = rgb
            return r | (g << 8) | (b << 16)

        active_presentation = signal_presentation(
            base_label=label,
            active_color=color,
            grace_color=self._grace_color,
            grace_label_template=self._grace_label_template,
            remaining_seconds=0.0,
        )
        grace_enabled = bool(
            label and self._grace_seconds > 0 and self._on_abort is not None
        )
        initial_presentation = signal_presentation(
            base_label=label,
            active_color=color,
            grace_color=self._grace_color,
            grace_label_template=self._grace_label_template,
            remaining_seconds=self._grace_seconds if grace_enabled else 0.0,
        )
        current: dict[str, SignalPresentation] = {
            "presentation": initial_presentation
        }
        self._set_presentation(initial_presentation)

        def _color_resources(rgb: tuple[int, int, int]) -> dict[str, int]:
            ref = _colorref(rgb)
            return {
                "colorref": ref,
                "brush": gdi32.CreateSolidBrush(ref),
                "glow_brush": gdi32.CreateSolidBrush(ref),
                "ring_pen": gdi32.CreatePen(0, 4, ref),
                "glow_pen": gdi32.CreatePen(0, 10, ref),
            }

        active_resources = _color_resources(active_presentation.color)
        grace_resources = _color_resources(initial_presentation.color)

        def _current_resources() -> dict[str, int]:
            if current["presentation"].phase == "countdown":
                return grace_resources
            return active_resources

        black_brush = gdi32.CreateSolidBrush(0)
        abort_brush = gdi32.CreateSolidBrush(_colorref(_ABORT_BUTTON_COLOR))
        created: dict[str, list] = {"hwnds": []}
        hotkey_id: int | None = None

        WM_PAINT = 0x000F
        WM_DESTROY = 0x0002
        WM_ERASEBKGND = 0x0014
        WM_LBUTTONDOWN = 0x0201
        _ROLE_RING = 1
        _ROLE_LABEL = 2
        _ROLE_ABORT_BUTTON = 3

        def _paint_ring(hwnd: int) -> None:
            resources = _current_resources()
            hdc = user32.GetDC(hwnd)
            try:
                old_glow = gdi32.SelectObject(hdc, resources["glow_pen"])
                old_brush = gdi32.SelectObject(
                    hdc, gdi32.GetStockObject(5)  # NULL_BRUSH
                )
                gdi32.Ellipse(hdc, 2, 2, _CURSOR_RING - 2, _CURSOR_RING - 2)
                gdi32.SelectObject(hdc, resources["ring_pen"])
                gdi32.Ellipse(hdc, 8, 8, _CURSOR_RING - 8, _CURSOR_RING - 8)
                gdi32.SelectObject(hdc, old_glow)
                gdi32.SelectObject(hdc, old_brush)
            finally:
                user32.ReleaseDC(hwnd, hdc)

        def _paint_label(hwnd: int) -> None:
            presentation = current["presentation"]
            resources = _current_resources()
            hdc = user32.GetDC(hwnd)
            try:
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
                gdi32.SetTextColor(hdc, resources["colorref"])
                gdi32.SelectObject(
                    hdc, gdi32.GetStockObject(17)  # DEFAULT_GUI_FONT
                )
                user32.DrawTextW(
                    hdc,
                    presentation.visual_label,
                    -1,
                    ctypes.byref(rect),
                    0x0024,  # DT_CENTER|DT_VCENTER
                )
            finally:
                user32.ReleaseDC(hwnd, hdc)

        def _paint_abort_button(hwnd: int) -> None:
            hdc = user32.GetDC(hwnd)
            try:
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                gdi32.SetBkMode(hdc, 1)  # TRANSPARENT (rect is already red)
                gdi32.SetTextColor(hdc, _colorref((255, 255, 255)))
                gdi32.SelectObject(hdc, gdi32.GetStockObject(17))  # DEFAULT_GUI_FONT
                user32.DrawTextW(
                    hdc, _ABORT_BUTTON_LABEL, -1, ctypes.byref(rect), 0x0024
                )
            finally:
                user32.ReleaseDC(hwnd, hdc)

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_ERASEBKGND:
                role = user32.GetWindowLongPtrW(hwnd, -21)  # GWLP_USERDATA
                if role == _ROLE_ABORT_BUTTON:
                    # opaque button, always the same bright red — recognizable
                    # regardless of the active mode color.
                    rect = wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(rect))
                    user32.FillRect(wparam, ctypes.byref(rect), abort_brush)
                    return 1
                if role:
                    # ring/label windows need a black backdrop so the color
                    # key makes everything but the drawing transparent
                    rect = wintypes.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(rect))
                    user32.FillRect(wparam, ctypes.byref(rect), black_brush)
                    return 1
                # Border bars use the current phase color. Filling them here,
                # instead of relying on the immutable class brush, makes the
                # countdown -> active transition visible without recreating
                # any overlay window.
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                user32.FillRect(
                    wparam, ctypes.byref(rect), _current_resources()["brush"]
                )
                return 1
            if msg == WM_PAINT:
                ps = ctypes.create_string_buffer(72)
                user32.BeginPaint(hwnd, ps)
                user32.EndPaint(hwnd, ps)
                role = user32.GetWindowLongPtrW(hwnd, -21)  # GWLP_USERDATA
                if role == _ROLE_RING:
                    _paint_ring(hwnd)
                elif role == _ROLE_LABEL:
                    _paint_label(hwnd)
                elif role == _ROLE_ABORT_BUTTON:
                    _paint_abort_button(hwnd)
                return 0
            if msg == WM_LBUTTONDOWN:
                role = user32.GetWindowLongPtrW(hwnd, -21)  # GWLP_USERDATA
                if role == _ROLE_ABORT_BUTTON:
                    # Fire on a side thread, same as the hotkey: a modal Tk
                    # reason dialog must not stall the overlay's message pump.
                    threading.Thread(
                        target=self._fire_abort,
                        name="oc-abort-button",
                        daemon=True,
                    ).start()
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wnd_proc = WNDPROC(_wnd_proc)  # keep alive for the thread lifetime

        instance = kernel32.GetModuleHandleW(None)
        class_name = f"OCSignalOverlay_{id(self) & 0xFFFFFFFF:x}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = wnd_proc
        wc.hInstance = instance
        wc.lpszClassName = class_name
        wc.hbrBackground = active_resources["brush"]
        if not user32.RegisterClassW(ctypes.byref(wc)):
            self.error = RuntimeError("RegisterClassW failed")
            return

        ex_style = (
            0x00000080  # WS_EX_TOOLWINDOW
            | 0x00000020  # WS_EX_TRANSPARENT (click-through)
            | 0x00080000  # WS_EX_LAYERED
            | 0x00000008  # WS_EX_TOPMOST
            | 0x08000000  # WS_EX_NOACTIVATE
        )
        # Same window, minus WS_EX_TRANSPARENT — the one popup on this
        # overlay that must actually receive a mouse click (the abort
        # button). WS_EX_NOACTIVATE is kept so clicking it never steals
        # foreground focus from whatever the human is working in.
        ex_style_clickable = ex_style & ~0x00000020
        WS_POPUP = 0x80000000

        def _create(
            x,
            y,
            w,
            h,
            role=0,
            alpha=_BORDER_ALPHA,
            colorkey=None,
            clickable=False,
            title="",
        ):
            hwnd = user32.CreateWindowExW(
                ex_style_clickable if clickable else ex_style,
                class_name, title, WS_POPUP, x, y, w, h,
                None, None, instance, None,
            )
            if not hwnd:
                raise RuntimeError("CreateWindowExW failed")
            if role:
                user32.SetWindowLongPtrW(hwnd, -21, role)  # GWLP_USERDATA
            if colorkey is not None:
                user32.SetLayeredWindowAttributes(hwnd, colorkey, 0, 1)  # LWA_COLORKEY
            else:
                user32.SetLayeredWindowAttributes(hwnd, 0, alpha, 2)  # LWA_ALPHA
            user32.ShowWindow(hwnd, 8)  # SW_SHOWNA
            created["hwnds"].append(hwnd)
            return hwnd

        try:
            vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            vy = user32.GetSystemMetrics(77)
            vw = user32.GetSystemMetrics(78)
            vh = user32.GetSystemMetrics(79)
            t = self._thickness
            glow = t + 6

            # outer glow frame (dimmer), then bright inner frame
            if border:
                for width, alpha in ((glow, _GLOW_ALPHA), (t, _BORDER_ALPHA)):
                    _create(vx, vy, vw, width, alpha=alpha)
                    _create(vx, vy + vh - width, vw, width, alpha=alpha)
                    _create(vx, vy + width, width, vh - 2 * width, alpha=alpha)
                    _create(vx + vw - width, vy + width, width, vh - 2 * width, alpha=alpha)

            cursor_hwnd = None
            if cursor:
                cursor_hwnd = _create(
                    0, 0, _CURSOR_RING, _CURSOR_RING, role=1, colorkey=0
                )
            label_hwnd = None
            if label:
                label_w = min(720, vw - 40)
                label_hwnd = _create(
                    vx + (vw - label_w) // 2, vy + glow + 4, label_w, 26,
                    role=2,
                    alpha=210,
                    colorkey=None,
                    title=initial_presentation.accessible_label,
                )

            # Abort button: always drawn whenever an abort path is wired
            # (independent of whether a hotkey is ALSO configured) — "immer
            # sichtbares, klickbares Abort-Element" (Ticket T-20260818-895473048).
            if self._on_abort is not None:
                bx, by, bw, bh = _abort_button_rect(vx, vy, vw, vh, glow)
                _create(
                    bx, by, bw, bh, role=_ROLE_ABORT_BUTTON, alpha=248,
                    clickable=True,
                    title="Open Compute: Abbruch — stoppt sofort",
                )

            hotkey_id = None
            if self._abort_hotkey is not None and self._on_abort is not None:
                mods, vk = self._abort_hotkey
                # 0x4000 = MOD_NOREPEAT: one fire per press, not a stream
                if user32.RegisterHotKey(None, 1, mods | 0x4000, vk):
                    hotkey_id = 1

            def _apply_presentation(presentation: SignalPresentation) -> None:
                current["presentation"] = presentation
                self._set_presentation(presentation)
                if label_hwnd:
                    # The HWND title is the accessibility name. NotifyWinEvent
                    # lets a screenreader observe the once-per-second semantic
                    # change; the painted label is separately invalidated below.
                    user32.SetWindowTextW(
                        label_hwnd, presentation.accessible_label
                    )
                    user32.NotifyWinEvent(
                        0x800C, label_hwnd, 0, 0  # EVENT_OBJECT_NAMECHANGE
                    )
                for overlay_hwnd in created["hwnds"]:
                    user32.InvalidateRect(overlay_hwnd, None, True)
                    user32.UpdateWindow(overlay_hwnd)

            _apply_presentation(initial_presentation)

            # Grace countdown — display-only (the actual blocking-before-first
            # -action wait lives server-side in mcp_server._await_grace_period).
            # Text changes once per second and the color changes exactly once
            # at activation: no flashing, pulsing or motion animation.
            grace_deadline = (
                time.monotonic() + self._grace_seconds
                if grace_enabled
                else None
            )
            last_shown_seconds = initial_presentation.seconds_remaining

            msg = wintypes.MSG()
            point = wintypes.POINT()
            WM_HOTKEY = 0x0312
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == WM_HOTKEY:
                        # Fire on a side thread so the Tk input box can run
                        # its own modal loop without stalling the overlay.
                        threading.Thread(
                            target=self._fire_abort,
                            name="oc-abort-hotkey",
                            daemon=True,
                        ).start()
                        continue
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                if cursor_hwnd and user32.GetCursorPos(ctypes.byref(point)):
                    user32.SetWindowPos(
                        cursor_hwnd, None,
                        point.x - _CURSOR_RING // 2,
                        point.y - _CURSOR_RING // 2,
                        0, 0,
                        0x0001 | 0x0004 | 0x0010,  # NOSIZE|NOACTIVATE|NOZORDER? keep topmost via ex style
                    )
                if grace_deadline is not None:
                    remaining = grace_deadline - time.monotonic()
                    if remaining <= 0:
                        _apply_presentation(active_presentation)
                        grace_deadline = None
                    else:
                        next_presentation = signal_presentation(
                            base_label=label,
                            active_color=color,
                            grace_color=self._grace_color,
                            grace_label_template=self._grace_label_template,
                            remaining_seconds=remaining,
                        )
                        if next_presentation.seconds_remaining != last_shown_seconds:
                            last_shown_seconds = next_presentation.seconds_remaining
                            _apply_presentation(next_presentation)
                self._stop.wait(0.033)
        except BaseException as exc:  # surface overlay failures, never crash caller
            self.error = exc
        finally:
            if hotkey_id is not None:
                user32.UnregisterHotKey(None, hotkey_id)
            for hwnd in created["hwnds"]:
                user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, instance)
            resource_objects = [
                value
                for resources in (active_resources, grace_resources)
                for key, value in resources.items()
                if key != "colorref"
            ]
            for obj in (*resource_objects, black_brush, abort_brush):
                gdi32.DeleteObject(obj)
