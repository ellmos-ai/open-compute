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
import threading
import time
import uuid
from typing import Any


MODES: tuple[str, ...] = ("Allgemein", "Kurz", "Brainstorming", "Bildschirmhilfe")
_STOP = "__OPEN_COMPUTE_CHAT_STOP__"


@dataclass(frozen=True)
class HumanTurn:
    message: str
    mode: str
    screenshot: bool
    choice_index: int | None = None


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
                    self.turns.put(json.loads(line))
                except json.JSONDecodeError as exc:
                    self.turns.put({"type": "error", "detail": f"invalid UI child output: {exc}"})
        finally:
            exit_code = self.process.wait()
            self.turns.put({"type": "eof", "exit_code": exit_code})

    def dispose(self) -> None:
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

            def append(role: str, message: str) -> None:
                history.configure(state="normal")
                label = {"human": "Du", "assistant": "Assistent", "system": "Hinweis"}[role]
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
                mode_row, text="Vollbild-Screenshot an den Assistenten senden",
                variable=include_shot,
            ).pack(side="left")

            suggestions = ttk.Frame(outer)
            suggestions.pack(fill="x", pady=(2, 5))

            entry = tk.Text(outer, height=4, wrap="word", font=("Segoe UI", 10))
            entry.pack(fill="x")

            status = tk.StringVar(value="Der Assistent wartet auf deine Nachricht.")
            ttk.Label(outer, textvariable=status).pack(anchor="w", pady=(5, 0))

            def submit(message: str | None = None, choice_index: int | None = None) -> None:
                text = message if message is not None else entry.get("1.0", "end-1c").strip()
                if not text.strip() or self.closed.is_set():
                    return
                if len(text) > 20_000:
                    status.set("Nachricht zu lang (maximal 20.000 Zeichen).")
                    return
                append("human", text)
                self.turns.put(HumanTurn(
                    message=text.strip(), mode=mode.get(),
                    screenshot=bool(include_shot.get()), choice_index=choice_index,
                ))
                entry.delete("1.0", "end")
                entry.configure(state="disabled")
                send_button.configure(state="disabled")
                set_choices(())
                status.set("Nachricht übergeben. Der Assistent denkt …")

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
                            entry.focus_set()
                            status.set("Der Assistent wartet auf deine Nachricht.")
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
        if not self.closed.is_set():
            self.closed.set()
            while True:
                try:
                    self.turns.get_nowait()
                except queue.Empty:
                    break
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

    def _forget(self, chat_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(chat_id, None)
        if session is not None:
            session.dispose()

    def close(self, chat_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(chat_id, None)
        if session is None:
            return False
        if session.process is not None and session.process.poll() is None:
            session._send_command({"type": "close"})
        if not session.closed.is_set():
            session.closed.set()
            while True:
                try:
                    session.turns.get_nowait()
                except queue.Empty:
                    break
            session.turns.put(_STOP)
        if session.process is not None:
            try:
                session.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                session.process.terminate()
                session.process.wait(timeout=2)
        session.dispose()
        return True

    def close_all(self) -> None:
        with self._lock:
            chat_ids = tuple(self._sessions)
        for chat_id in chat_ids:
            self.close(chat_id)


__all__ = ["ChatBridge", "ChatSession", "HumanTurn", "MODES"]
