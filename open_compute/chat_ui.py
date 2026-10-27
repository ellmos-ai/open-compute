"""Native Tk chat window for the host-model conversational bridge.

The MCP handler and Tk live in separate processes. The host model remains the
reasoner: it sends each assistant turn back through ``chat(chat_id=...)`` and
receives the next queued human turn. No model client or provider lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


MODES: tuple[str, ...] = ("Allgemein", "Kurz", "Brainstorming", "Bildschirmhilfe")
COLLABORATION_PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "Kurz weg",
        "Ich bin kurz weg. Arbeite am aktuellen Anliegen weiter und halte deine "
        "nächsten konkreten Schritte fest, bis ich zurück bin.",
    ),
    (
        "Wieder da",
        "Ich bin wieder da. Fasse kurz zusammen, woran du gearbeitet hast und "
        "was als Nächstes ansteht.",
    ),
    (
        "Hilfe und Vorschläge",
        "Gib mir Hilfestellungen oder konkrete Vorschläge zum aktuellen Schritt.",
    ),
)
_STOP = "__OPEN_COMPUTE_CHAT_STOP__"
OBSERVATION_INTERVAL_SECONDS = 15
MAX_OBSERVATION_WINDOWS = 64


def insert_collaboration_prompt(entry: Any, prompt: str) -> None:
    """Put a preset into the editable message field without sending it."""
    if entry.tag_ranges("sel"):
        entry.delete("sel.first", "sel.last")
    entry.insert("insert", prompt)
    entry.focus_set()


def _voice_status() -> tuple[bool, str]:
    from .voice_io_bridge import transcription_status

    return transcription_status()


def _microphone_recording() -> Any:
    from .voice_io_bridge import MicrophoneRecording

    return MicrophoneRecording()


def _transcribe_local_wav(path: Path) -> str:
    from .voice_io_bridge import transcribe_local_wav

    return transcribe_local_wav(path)


def observation_capture_allowed(
    *, enabled: bool, screenshot_consent: bool, window: Mapping[str, Any] | None
) -> bool:
    """Require explicit observe mode, current screenshot consent, and a scope."""
    return bool(enabled and screenshot_consent and window and window.get("title"))


def _window_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Pass only the live identity fields needed for a later fail-closed check."""
    return {
        "hwnd": int(value.get("hwnd", value.get("window_id"))),
        "pid": int(value.get("pid", value.get("process_id"))),
        "title": str(value["title"]).strip(),
    }


def _available_observation_windows(exclude_title: str = "") -> list[dict[str, Any]]:
    """List visible top-level windows without capturing any pixels."""
    try:
        from .drivers.local import list_windows

        windows = list_windows()
    except Exception:
        return []
    result: list[dict[str, Any]] = []
    for raw in windows:
        try:
            window = _window_identity(raw)
        except (KeyError, TypeError, ValueError):
            continue
        if not window["title"] or window["title"] == exclude_title:
            continue
        result.append(window)
        if len(result) >= MAX_OBSERVATION_WINDOWS:
            break
    return result


@dataclass(frozen=True)
class HumanTurn:
    message: str
    mode: str
    screenshot: bool
    choice_index: int | None = None
    observation_update: bool = False
    observation_enabled: bool = False
    observation_window: dict[str, Any] | None = None
    observation_event_id: str = ""


@dataclass
class ChatSession:
    chat_id: str
    commands: queue.Queue[tuple[Any, ...]] = field(default_factory=queue.Queue)
    turns: queue.Queue[Any] = field(default_factory=queue.Queue)
    ready: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)
    startup_error: BaseException | None = None
    process: subprocess.Popen[str] | None = None
    reader_thread: threading.Thread | None = None
    exchange_lock: threading.Lock = field(default_factory=threading.Lock)
    input_lock: threading.Lock = field(default_factory=threading.Lock)
    replies: dict[str, tuple[str, HumanTurn | None, bool]] = field(default_factory=dict)
    observation_reply_cache: tuple[str, dict[str, Any]] | None = None
    observation_capture_lock: threading.Lock = field(default_factory=threading.Lock)
    observation_processed_ids: list[str] = field(default_factory=list)
    temp_audio_paths: set[Path] = field(default_factory=set)

    def start(self, *, title: str, context: str, choices: tuple[str, ...]) -> None:
        if len(context) > 8_000:
            raise ValueError("chat context must be at most 8000 characters")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "open_compute.chat_ui_process"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self._send_command({
            "type": "init", "title": title, "context": context,
            "choices": list(choices),
        })
        self.reader_thread = threading.Thread(
            target=self._read_child_output,
            name=f"open-compute-chat-reader-{self.chat_id[:8]}",
            daemon=True,
        )
        self.reader_thread.start()
        deadline = time.monotonic() + 20
        last_stage = "child did not report startup"
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                exit_code = self.process.poll()
                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=2)
                self.dispose()
                raise RuntimeError(
                    f"native chat startup timed out at {last_stage}; child exit={exit_code}"
                )
            try:
                notice = self.turns.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self.process.poll() is not None:
                    exit_code = self.process.returncode
                    self.dispose()
                    raise RuntimeError(
                        f"native chat UI child exited before ready (exit={exit_code}; {last_stage})"
                    )
                continue
            if notice.get("type") == "ready":
                self.ready.set()
                break
            if notice.get("type") == "error":
                self.closed.set()
                self.process.wait(timeout=2)
                self.dispose()
                raise RuntimeError(f"could not open native chat window: {notice.get('detail', '')}")
            if notice.get("type") == "eof":
                raise RuntimeError(
                    f"native chat UI child exited before ready (exit={notice.get('exit_code')}; {last_stage})"
                )
            last_stage = str(notice.get("type", "unknown child message"))

    def _read_child_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                if len(line) > 80_000:
                    self.turns.put({"type": "error", "detail": "native chat IPC response exceeds the size limit"})
                    break
                try:
                    message = json.loads(line)
                    if message.get("type") == "temp_audio":
                        self._track_temp_audio(message.get("path"))
                    elif message.get("type") == "temp_audio_removed":
                        self._untrack_temp_audio(message.get("path"))
                    else:
                        self.turns.put(message)
                except json.JSONDecodeError as exc:
                    self.turns.put({"type": "error", "detail": f"invalid UI child output: {exc}"})
        finally:
            exit_code = self.process.wait()
            self._cleanup_temp_audio()
            self.turns.put({"type": "eof", "exit_code": exit_code})

    @staticmethod
    def _owned_temp_audio_path(value: Any) -> Path | None:
        if not isinstance(value, str):
            return None
        try:
            path = Path(value)
            if (
                path.suffix.lower() != ".wav"
                or not path.name.startswith("open-compute-chat-audio-")
                or path.parent.resolve() != Path(tempfile.gettempdir()).resolve()
            ):
                return None
            return path
        except (OSError, RuntimeError, ValueError):
            return None

    def _track_temp_audio(self, value: Any) -> None:
        path = self._owned_temp_audio_path(value)
        if path is not None:
            self.temp_audio_paths.add(path)

    def _untrack_temp_audio(self, value: Any) -> None:
        path = self._owned_temp_audio_path(value)
        if path is not None:
            self.temp_audio_paths.discard(path)

    def _cleanup_temp_audio(self) -> None:
        for path in tuple(self.temp_audio_paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            else:
                self._untrack_temp_audio(str(path))

    def dispose(self) -> None:
        with self.observation_capture_lock:
            self.observation_reply_cache = None
            self.observation_processed_ids.clear()
        if self.process is not None and self.process.poll() is None:
            return
        if self.process is not None:
            if self.reader_thread is not None and self.reader_thread is not threading.current_thread():
                self.reader_thread.join(timeout=1)
            for stream in (self.process.stdin, self.process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def add_assistant(self, message: str, choices: tuple[str, ...]) -> None:
        if self.closed.is_set():
            return
        if len(message) > 20_000:
            raise ValueError("assistant_message must be at most 20000 characters")
        if self.process is None:
            self.commands.put(("assistant", message, choices))
            return
        self._send_command({"type": "assistant", "message": message, "choices": list(choices)})

    def report_observation_error(self, message: str) -> None:
        """Tell the visible UI that an observation was refused or failed."""
        safe_message = str(message)[:2_000]
        if self.process is None:
            self.commands.put(("observation_error", safe_message))
            return
        self._send_command({"type": "observation_error", "message": safe_message})

    def capture_observation_once(
        self, event_id: str, capture: Any, *, replayed: bool = False
    ) -> dict[str, Any]:
        """Capture at most once per UI event and cache only one <=2-MB frame."""
        key = str(event_id).strip()
        if not key or len(key) > 128:
            return {"status": "error", "message": "Ungültige Beobachtungs-ID."}
        with self.observation_capture_lock:
            cached = self.observation_reply_cache
            if cached is not None and cached[0] == key:
                return dict(cached[1])
            if replayed or key in self.observation_processed_ids:
                return {
                    "status": "expired",
                    "message": "Dieses Beobachtungsbild ist nicht mehr im begrenzten Cache.",
                }
            try:
                result = capture()
            except Exception as exc:
                result = {
                    "status": "error",
                    "message": f"Bildschirmbeobachtung fehlgeschlagen: {type(exc).__name__}: {exc}",
                }
            if not isinstance(result, dict):
                result = {"status": "error", "message": "Ungültiges Beobachtungsergebnis."}
            image = result.get("png")
            if result.get("status") == "ok" and (
                not isinstance(image, bytes) or len(image) > 2_000_000
            ):
                result = {
                    "status": "error",
                    "message": "Beobachtungsbild fehlt oder überschreitet das 2-MB-Limit.",
                }
            self.observation_reply_cache = (key, dict(result))
            self.observation_processed_ids.append(key)
            if len(self.observation_processed_ids) > 64:
                self.observation_processed_ids.pop(0)
            return dict(result)

    def _send_command(self, command: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            return
        with self.input_lock:
            self.process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def next_turn(self, timeout: float) -> tuple[HumanTurn | None, bool]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, True
            try:
                value = self.turns.get(timeout=remaining)
            except queue.Empty:
                return None, True
            if isinstance(value, HumanTurn):
                return value, False
            if value == _STOP or value.get("type") in {"closed", "eof"}:
                self.closed.set()
                if self.process is not None:
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.terminate()
                        self.process.wait(timeout=2)
                return None, False
            if value.get("type") == "error":
                self.closed.set()
                if self.process is not None and self.process.poll() is None:
                    self._send_command({"type": "close"})
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.terminate()
                        self.process.wait(timeout=2)
                raise RuntimeError(value.get("detail", "native chat UI failed"))
            if value.get("type") == "human":
                return HumanTurn(
                    message=value["message"], mode=value["mode"],
                    screenshot=bool(value.get("screenshot")),
                    choice_index=value.get("choice_index"),
                    observation_update=bool(value.get("observation_update")),
                    observation_enabled=bool(value.get("observation_enabled")),
                    observation_window=(
                        _window_identity(value["observation_window"])
                        if isinstance(value.get("observation_window"), dict)
                        else None
                    ),
                    observation_event_id=(
                        str(value.get("observation_event_id", ""))[:128]
                        if value.get("observation_update") else ""
                    ),
                ), False

    def _run_ui(self, title: str, context: str, choices: tuple[str, ...]) -> None:
        try:
            import tkinter as tk
            from tkinter import ttk

            root = tk.Tk()
            if hasattr(self, "progress"):
                self.progress("tk_root_created")
            root.title("open-compute — Chat")
            root.geometry("760x650")
            root.minsize(560, 430)
            root.protocol("WM_DELETE_WINDOW", lambda: self._close(root))
            observation_windows = _available_observation_windows(root.title())

            outer = ttk.Frame(root, padding=12)
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text=title, font=("Segoe UI", 13, "bold")).pack(
                anchor="w", pady=(0, 8)
            )

            history = tk.Text(
                outer, wrap="word", state="disabled", height=20,
                font=("Segoe UI", 10), padx=8, pady=8,
            )
            history.pack(fill="both", expand=True)
            history.tag_configure("human", foreground="#174b8a", spacing1=10)
            history.tag_configure("assistant", foreground="#176b44", spacing1=10)
            history.tag_configure("system", foreground="#666666", spacing1=6)
            history.tag_configure("observation", foreground="#8a4b08", spacing1=10)

            def append(role: str, message: str) -> None:
                history.configure(state="normal")
                label = {
                    "human": "Du", "assistant": "Assistent", "system": "Hinweis",
                    "observation": "Beobachtung",
                }[role]
                history.insert("end", f"{label}\n", role)
                history.insert("end", message.strip() + "\n\n", role)
                history.see("end")
                history.configure(state="disabled")

            if context.strip():
                append("system", context.strip())

            mode_row = ttk.Frame(outer)
            mode_row.pack(fill="x", pady=(10, 5))
            ttk.Label(mode_row, text="Modus:").pack(side="left")
            mode = tk.StringVar(value=MODES[0])
            ttk.Combobox(
                mode_row, textvariable=mode, values=MODES,
                state="readonly", width=22,
            ).pack(side="left", padx=(6, 14))
            include_shot = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                mode_row, text="Screenshot für diesen Turn freigeben (auch für Beobachtung)",
                variable=include_shot,
            ).pack(side="left")

            observation_row = ttk.Frame(outer)
            observation_row.pack(fill="x", pady=(3, 5))
            observe_enabled = tk.BooleanVar(value=False)
            window_choice = tk.StringVar(value="")
            observation_window_by_label: dict[str, dict[str, Any]] = {}
            window_selector = ttk.Combobox(
                observation_row, textvariable=window_choice,
                values=(), state="readonly", width=48,
            )

            def refresh_window_choices() -> None:
                nonlocal observation_windows
                current = window_choice.get()
                observation_windows = _available_observation_windows(root.title())
                observation_window_by_label.clear()
                labels: list[str] = []
                for identity in observation_windows:
                    label = f"{identity['title']} — PID {identity['pid']}"
                    observation_window_by_label[label] = identity
                    labels.append(label)
                window_selector.configure(values=tuple(labels))
                if current in observation_window_by_label:
                    window_choice.set(current)
                else:
                    window_choice.set("")
                if not labels:
                    status.set("Keine sichtbaren Zielfenster gefunden; Beobachtung bleibt aus.")

            observe_toggle = ttk.Checkbutton(
                observation_row, text="Fenster beobachten (AN/AUS)",
                variable=observe_enabled,
            )
            observe_toggle.pack(side="left")
            window_selector.pack(side="left", padx=(8, 5))
            ttk.Button(
                observation_row, text="Fenster aktualisieren",
                command=refresh_window_choices,
            ).pack(side="left")

            suggestions = ttk.Frame(outer)
            suggestions.pack(fill="x", pady=(2, 5))

            collaboration = ttk.Frame(outer)
            collaboration.pack(fill="x", pady=(0, 5))
            ttk.Label(collaboration, text="Zusammenarbeit:").pack(side="left")
            for label, prompt in COLLABORATION_PROMPTS:
                ttk.Button(
                    collaboration,
                    text=label,
                    command=lambda value=prompt: insert_collaboration_prompt(entry, value),
                ).pack(side="left", padx=(6, 0))

            entry = tk.Text(outer, height=4, wrap="word", font=("Segoe UI", 10))
            entry.pack(fill="x")

            status = tk.StringVar(value="Der Assistent wartet auf deine Nachricht.")
            ttk.Label(outer, textvariable=status).pack(anchor="w", pady=(5, 0))

            observation_after_id: str | None = None
            last_observation_at = 0.0

            def cancel_observation_timer() -> None:
                nonlocal observation_after_id
                if observation_after_id is not None:
                    try:
                        root.after_cancel(observation_after_id)
                    except Exception:
                        pass
                    observation_after_id = None

            def refresh_observation_state(*_args: Any) -> None:
                nonlocal observation_after_id
                cancel_observation_timer()
                if self.closed.is_set() or not observe_enabled.get():
                    return
                selected = observation_window_by_label.get(window_choice.get())
                if not include_shot.get():
                    status.set("Beobachtung pausiert: Screenshotfreigabe für den Turn ist aus.")
                    return
                if selected is None:
                    status.set("Beobachtung pausiert: Bitte ein Zielfenster auswählen.")
                    return
                delay = max(
                    0.0,
                    OBSERVATION_INTERVAL_SECONDS - (time.monotonic() - last_observation_at),
                )
                status.set(
                    f"Beobachtung AN: {selected['title']} — begrenzte Abfrage alle "
                    f"{OBSERVATION_INTERVAL_SECONDS} Sekunden. Zum Stoppen AUS wählen."
                )
                observation_after_id = root.after(
                    int(delay * 1_000), capture_observation_turn
                )

            def capture_observation_turn() -> None:
                nonlocal observation_after_id, last_observation_at
                observation_after_id = None
                selected = observation_window_by_label.get(window_choice.get())
                if not observation_capture_allowed(
                    enabled=bool(observe_enabled.get()),
                    screenshot_consent=bool(include_shot.get()),
                    window=selected,
                ):
                    refresh_observation_state()
                    return
                if self.closed.is_set():
                    return
                last_observation_at = time.monotonic()
                message = (
                    "Bitte beobachte das freigegebene Fenster auf relevante Änderungen "
                    "und gib mir kurze Hilfestellungen oder Vorschläge. "
                    f"Fenster: {selected['title']}."
                )
                submit(
                    message,
                    observation_update=True,
                    observation_window=selected,
                    observation_event_id=str(uuid.uuid4()),
                )

            def cleanup_chat() -> None:
                cancel_observation_timer()
                cancel_microphone_work()

            refresh_window_choices()
            observe_enabled.trace_add("write", refresh_observation_state)
            include_shot.trace_add("write", refresh_observation_state)
            window_choice.trace_add("write", refresh_observation_state)

            voice_row = ttk.Frame(outer)
            voice_row.pack(fill="x", pady=(5, 0))
            voice_ready, voice_detail = _voice_status()
            recording = _microphone_recording()
            active_audio_path: Path | None = None
            recording_started: float | None = None
            transcription_cancelled: threading.Event | None = None
            transcription_results: queue.Queue[tuple[str | None, str | None]] = queue.Queue()
            mic_button = ttk.Button(
                voice_row,
                text="Mikrofon starten" if voice_ready else "Mikrofon nicht verfügbar",
                state="normal" if voice_ready else "disabled",
            )
            mic_button.pack(side="left")
            if not voice_ready:
                status.set(voice_detail)

            def unregister_audio(path: Path) -> None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    return
                unregister = getattr(self, "unregister_temp_audio", None)
                if unregister is not None:
                    unregister(str(path))

            def cancel_microphone_work() -> None:
                nonlocal active_audio_path, recording_started
                if transcription_cancelled is not None:
                    transcription_cancelled.set()
                path = recording.cancel()
                if active_audio_path is not None:
                    path = active_audio_path
                recording_started = None
                if path is not None:
                    unregister_audio(path)
                active_audio_path = None

            if hasattr(self, "cleanup_callback"):
                self.cleanup_callback = cleanup_chat

            def poll_transcription() -> None:
                nonlocal transcription_cancelled
                try:
                    transcript, error = transcription_results.get_nowait()
                except queue.Empty:
                    if not self.closed.is_set():
                        root.after(100, poll_transcription)
                    return
                cancelled = transcription_cancelled is not None and transcription_cancelled.is_set()
                transcription_cancelled = None
                mic_button.configure(text="Mikrofon starten", state="normal")
                if cancelled:
                    status.set("Transkription verworfen; temporäre Audiodaten wurden gelöscht.")
                elif error:
                    status.set(f"Transkription fehlgeschlagen: {error}")
                elif transcript:
                    insert_collaboration_prompt(entry, transcript)
                    status.set("Transkript eingefügt. Bitte prüfen und normal senden.")
                else:
                    status.set("Kein Text erkannt. Bitte erneut aufnehmen oder tippen.")
                if not self.closed.is_set():
                    root.after(100, poll_transcription)

            def begin_transcription(path: Path) -> None:
                nonlocal transcription_cancelled
                transcription_cancelled = threading.Event()
                mic_button.configure(text="Transkript verwerfen", state="normal")
                status.set("Sprache wird lokal transkribiert …")

                def worker() -> None:
                    transcript: str | None = None
                    error: str | None = None
                    try:
                        transcript = _transcribe_local_wav(path)
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                    finally:
                        unregister_audio(path)
                    if not self.closed.is_set():
                        transcription_results.put((transcript, error))

                threading.Thread(
                    target=worker, name="open-compute-local-transcription", daemon=True
                ).start()
                root.after(100, poll_transcription)

            def toggle_microphone() -> None:
                nonlocal active_audio_path, recording_started
                if transcription_cancelled is not None:
                    transcription_cancelled.set()
                    mic_button.configure(text="Verwerfe …", state="disabled")
                    status.set("Transkript wird verworfen, sobald die lokale Verarbeitung endet.")
                    return
                if recording_started is None:
                    try:
                        active_audio_path = recording.start()
                        register = getattr(self, "register_temp_audio", None)
                        if register is not None:
                            register(str(active_audio_path))
                    except Exception as exc:
                        if recording.alias is not None:
                            active_audio_path = recording.path
                            recording_started = time.monotonic()
                            if active_audio_path is not None:
                                register = getattr(self, "register_temp_audio", None)
                                if register is not None:
                                    register(str(active_audio_path))
                            mic_button.configure(text="Aufnahme erneut stoppen", state="normal")
                            status.set(
                                f"Mikrofon nicht sicher freigegeben: {type(exc).__name__}: {exc}. "
                                "Erneut stoppen oder Chat beenden."
                            )
                        else:
                            active_audio_path = None
                            status.set(f"Mikrofon nicht verfügbar: {type(exc).__name__}: {exc}")
                        return
                    recording_started = time.monotonic()
                    mic_button.configure(text="Aufnahme stoppen")
                    status.set("Mikrofonaufnahme läuft. Stoppen, um lokal zu transkribieren.")
                    root.after(500, update_recording_timer)
                    return
                stop_microphone()

            def stop_microphone() -> None:
                nonlocal active_audio_path, recording_started
                path = active_audio_path
                try:
                    path = recording.stop()
                except Exception as exc:
                    if recording.alias is not None:
                        active_audio_path = recording.path or path
                        mic_button.configure(text="Aufnahme erneut stoppen", state="normal")
                        status.set(
                            f"Mikrofon nicht sicher freigegeben: {type(exc).__name__}: {exc}. "
                            "Erneut stoppen oder Chat beenden."
                        )
                        return
                    recording_started = None
                    if path is not None:
                        unregister_audio(path)
                    active_audio_path = None
                    mic_button.configure(text="Mikrofon starten", state="normal")
                    status.set(f"Aufnahme fehlgeschlagen: {type(exc).__name__}: {exc}")
                    return
                recording_started = None
                active_audio_path = None
                begin_transcription(path)

            def update_recording_timer() -> None:
                if recording_started is None or self.closed.is_set():
                    return
                elapsed = time.monotonic() - recording_started
                if elapsed >= 60:
                    status.set("Maximale Aufnahmedauer von 60 Sekunden erreicht.")
                    stop_microphone()
                    return
                status.set(f"Mikrofonaufnahme läuft ({int(elapsed)} s). Stoppen zum Transkribieren.")
                root.after(500, update_recording_timer)

            mic_button.configure(command=toggle_microphone)
            root.after(100, poll_transcription)

            def submit(
                message: str | None = None,
                choice_index: int | None = None,
                *,
                observation_update: bool = False,
                observation_window: Mapping[str, Any] | None = None,
                observation_event_id: str = "",
            ) -> None:
                text = message if message is not None else entry.get("1.0", "end-1c").strip()
                if not text.strip() or self.closed.is_set():
                    return
                if len(text) > 20_000:
                    status.set("Nachricht zu lang (maximal 20.000 Zeichen).")
                    return
                if observation_update and not observation_capture_allowed(
                    enabled=bool(observe_enabled.get()),
                    screenshot_consent=bool(include_shot.get()),
                    window=observation_window,
                ):
                    status.set("Beobachtung gestoppt: Fenster- oder Screenshotfreigabe fehlt.")
                    refresh_observation_state()
                    return
                cancel_observation_timer()
                if recording_started is not None or transcription_cancelled is not None:
                    try:
                        cancel_microphone_work()
                    except Exception as exc:
                        status.set(
                            f"Mikrofon nicht sicher freigegeben: {type(exc).__name__}: {exc}. "
                            "Nachricht wurde nicht gesendet."
                        )
                        return
                mic_button.configure(state="disabled")
                if observation_update and observation_window is not None:
                    append("observation", f"Fenster: {observation_window['title']}\n{text}")
                else:
                    append("human", text)
                self.turns.put(HumanTurn(
                    message=text.strip(), mode=mode.get(),
                    screenshot=bool(include_shot.get()), choice_index=choice_index,
                    observation_update=observation_update,
                    observation_enabled=(
                        bool(observe_enabled.get()) if observation_update else False
                    ),
                    observation_window=(
                        _window_identity(observation_window)
                        if observation_window is not None else None
                    ),
                    observation_event_id=(
                        observation_event_id[:128] if observation_update else ""
                    ),
                ))
                if not observation_update:
                    entry.delete("1.0", "end")
                entry.configure(state="disabled")
                send_button.configure(state="disabled")
                set_choices(())
                status.set(
                    "Beobachtung übergeben. Der Assistent prüft das Fenster …"
                    if observation_update
                    else "Nachricht übergeben. Der Assistent denkt …"
                )

            def set_choices(items: tuple[str, ...]) -> None:
                for child in suggestions.winfo_children():
                    child.destroy()
                if not items:
                    return
                ttk.Label(suggestions, text="Vorschläge:").pack(side="left", padx=(0, 6))
                for index, choice in enumerate(items):
                    ttk.Button(
                        suggestions, text=choice,
                        command=lambda text=choice, i=index: submit(text, i),
                    ).pack(side="left", padx=(0, 5), pady=2)

            set_choices(choices)
            send_button = ttk.Button(mode_row, text="Senden", command=submit)
            send_button.pack(side="right")
            ttk.Button(
                mode_row, text="Chat beenden", command=lambda: self._close(root)
            ).pack(side="right", padx=(0, 8))
            entry.bind("<Return>", lambda _event: (submit(), "break")[1])
            entry.bind("<Shift-Return>", lambda _event: None)

            def poll_commands() -> None:
                try:
                    while True:
                        kind, *payload = self.commands.get_nowait()
                        if kind == "assistant":
                            message, next_choices = payload
                            if message:
                                append("assistant", message)
                            set_choices(next_choices)
                            entry.configure(state="normal")
                            send_button.configure(state="normal")
                            mic_button.configure(state="normal" if voice_ready else "disabled")
                            entry.focus_set()
                            status.set("Der Assistent wartet auf deine Nachricht.")
                            refresh_observation_state()
                        elif kind == "observation_error":
                            append("system", payload[0])
                            observe_enabled.set(False)
                            entry.configure(state="normal")
                            send_button.configure(state="normal")
                            mic_button.configure(state="normal" if voice_ready else "disabled")
                            status.set("Beobachtung pausiert. Prüfe Fenster und Freigabe, dann erneut aktivieren.")
                            entry.focus_set()
                        elif kind == "close":
                            self._close(root)
                except queue.Empty:
                    pass
                if not self.closed.is_set():
                    root.after(100, poll_commands)

            self.ready.set()
            entry.focus_set()
            poll_commands()
            root.mainloop()
        except BaseException as exc:
            self.startup_error = exc
            self.closed.set()
            self.ready.set()
            self.turns.put(_STOP)

    def _close(self, root: Any) -> None:
        first_close = not self.closed.is_set()
        if first_close:
            self.closed.set()
            while True:
                try:
                    self.turns.get_nowait()
                except queue.Empty:
                    break
        cleanup = getattr(self, "cleanup_callback", None)
        if cleanup is not None:
            try:
                cleanup()
            except Exception as exc:
                self.turns.put({
                    "type": "error",
                    "detail": f"microphone cleanup failed during chat shutdown: {type(exc).__name__}: {exc}",
                })
        if first_close:
            self.turns.put(_STOP)
        try:
            root.destroy()
        except Exception:
            pass


class ChatBridge:
    """Own in-memory sessions that pair a native chat window with its host."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.RLock()

    def open(
        self, *, context: str = "", choices: tuple[str, ...] = (), timeout: float = 300.0
    ) -> tuple[ChatSession, HumanTurn | None, bool]:
        session = ChatSession(chat_id=str(uuid.uuid4()))
        with self._lock:
            for old_id, old in tuple(self._sessions.items()):
                if old.closed.is_set():
                    self._sessions.pop(old_id, None)
            if len(self._sessions) >= 32:
                raise RuntimeError("too many open chat sessions; close an existing chat first")
            self._sessions[session.chat_id] = session
        try:
            session.start(title="Chat mit deinem Desktop-Assistenten", context=context, choices=choices)
        except Exception:
            self._forget(session.chat_id)
            raise
        try:
            turn, timed_out = session.next_turn(timeout)
        except Exception:
            self.close(session.chat_id)
            raise
        if turn is None:
            if session.closed.is_set():
                self._forget(session.chat_id)
        return session, turn, timed_out

    def reply(
        self, chat_id: str, *, assistant_message: str, assistant_turn_id: str,
        choices: tuple[str, ...] = (), timeout: float = 300.0,
    ) -> tuple[ChatSession, HumanTurn | None, bool, bool]:
        with self._lock:
            session = self._sessions.get(chat_id)
        if session is None:
            raise ValueError("unknown or expired chat_id; start a new chat")
        import hashlib
        from json import dumps

        fingerprint = hashlib.sha256(dumps(
            [assistant_message, choices], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        try:
            with session.exchange_lock:
                cached = session.replies.get(assistant_turn_id)
                if cached is not None:
                    if cached[0] != fingerprint:
                        raise ValueError("assistant_turn_id was already used for different content")
                    return session, cached[1], cached[2], True
                session.add_assistant(assistant_message, choices)
                turn, timed_out = session.next_turn(timeout)
                session.replies[assistant_turn_id] = (fingerprint, turn, timed_out)
                if len(session.replies) > 64:
                    session.replies.pop(next(iter(session.replies)))
        except Exception:
            self.close(chat_id)
            raise
        if turn is None and session.closed.is_set():
            self._forget(chat_id)
        return session, turn, timed_out, False

    def poll(self, chat_id: str, *, timeout: float = 300.0) -> tuple[ChatSession, HumanTurn | None, bool]:
        with self._lock:
            session = self._sessions.get(chat_id)
        if session is None:
            raise ValueError("unknown or expired chat_id; start a new chat")
        try:
            turn, timed_out = session.next_turn(timeout)
        except Exception:
            self.close(chat_id)
            raise
        if turn is None and session.closed.is_set():
            self._forget(chat_id)
        return session, turn, timed_out

    def report_observation_error(self, chat_id: str, message: str) -> None:
        with self._lock:
            session = self._sessions.get(chat_id)
        if session is not None and not session.closed.is_set():
            session.report_observation_error(message)

    def _forget(self, chat_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(chat_id, None)
        if session is not None:
            session.dispose()

    def close(self, chat_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(chat_id)
        if session is None:
            return False
        failure: str | None = None
        process = session.process

        def child_running() -> bool:
            if process is None:
                return False
            try:
                return process.poll() is None
            except Exception:
                return True

        try:
            if child_running():
                try:
                    session._send_command({"type": "close"})
                except (BrokenPipeError, OSError, ValueError):
                    # The child may already be exiting. Still run the bounded
                    # wait/terminate path below before dropping its session.
                    pass
            if not session.closed.is_set():
                session.closed.set()
                while True:
                    try:
                        session.turns.get_nowait()
                    except queue.Empty:
                        break
                session.turns.put(_STOP)
            if child_running():
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        process.terminate()
                    except Exception as exc:
                        failure = f"native chat UI terminate failed: {type(exc).__name__}: {exc}"
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                            process.wait(timeout=2)
                        except Exception as exc:
                            failure = f"native chat UI kill failed: {type(exc).__name__}: {exc}"
            elif process is not None:
                # A child that exited between the close send and wait still
                # needs a collected exit status where the platform allows it.
                try:
                    process.wait(timeout=3)
                except Exception:
                    pass
            if child_running() and failure is None:
                failure = "native chat UI child could not be stopped"
        except Exception as exc:
            failure = f"native chat UI close failed: {type(exc).__name__}: {exc}"
        finally:
            if child_running():
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                        process.wait(timeout=2)
                    except Exception as exc:
                        if failure is None:
                            failure = f"native chat UI forced shutdown failed: {type(exc).__name__}: {exc}"
            if not child_running():
                failure = None
            session.closed.set()
            with self._lock:
                if self._sessions.get(chat_id) is session:
                    self._sessions.pop(chat_id, None)
            try:
                session.dispose()
            except Exception as exc:
                if failure is None:
                    failure = f"native chat UI dispose failed: {type(exc).__name__}: {exc}"
        if failure is not None:
            raise RuntimeError(failure)
        return True

    def close_all(self) -> None:
        with self._lock:
            chat_ids = tuple(self._sessions)
        for chat_id in chat_ids:
            try:
                self.close(chat_id)
            except Exception:
                # One broken child must not keep other native chat windows
                # from receiving their own close/cleanup attempt.
                continue


__all__ = ["ChatBridge", "ChatSession", "HumanTurn", "MODES"]
