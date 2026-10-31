"""Subprocess entry point for the native Tk chat window.

The UI owns Tk on this process's main thread. Parent/child communication uses
bounded UTF-8 JSON lines on stdin/stdout; stderr is kept separate from MCP and
the JSON protocol.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from typing import Any

from .chat_ui import ChatSession, HumanTurn, _STOP

_MAX_LINE_CHARS = 80_000


class _ProtocolOutput:
    def __init__(self) -> None:
        self.lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        if len(raw) > _MAX_LINE_CHARS:
            raise ValueError("chat IPC message exceeds the size limit")
        with self.lock:
            sys.stdout.write(raw + "\n")
            sys.stdout.flush()


class _TurnSink:
    def __init__(self, output: _ProtocolOutput) -> None:
        self.output = output

    def put(self, value: Any) -> None:
        if value == _STOP:
            self.output.send({"type": "closed"})
        elif isinstance(value, HumanTurn):
            self.output.send({
                "type": "human", "message": value.message, "mode": value.mode,
                "screenshot": value.screenshot, "choice_index": value.choice_index,
                "observation_update": value.observation_update,
                "observation_enabled": value.observation_enabled,
                "observation_window": value.observation_window,
                "observation_event_id": value.observation_event_id,
                "observation_generation": value.observation_generation,
            })
        elif isinstance(value, dict) and value.get("type") == "observation_state":
            self.output.send(value)


class _ReadySignal:
    def __init__(self, session: Any, output: _ProtocolOutput) -> None:
        self.session, self.output = session, output

    def set(self) -> None:
        if self.session.closed.is_set():
            error = self.session.startup_error
            self.output.send({
                "type": "error",
                "detail": f"{type(error).__name__}: {error}",
            })
        else:
            self.output.send({"type": "ready"})


class _WindowSession:
    def __init__(
        self,
        commands: queue.Queue[tuple[Any, ...]],
        output: _ProtocolOutput,
        *,
        chat_id: str = "",
    ) -> None:
        self.commands = commands
        self.turns = _TurnSink(output)
        self.ready = _ReadySignal(self, output)
        self.chat_id = chat_id
        self.closed = threading.Event()
        self.startup_error: BaseException | None = None
        self.cleanup_callback: Any = None

    def progress(self, _stage: str) -> None:
        return None

    def register_temp_audio(self, path: str) -> None:
        self.turns.output.send({"type": "temp_audio", "path": path})

    def unregister_temp_audio(self, path: str) -> None:
        self.turns.output.send({"type": "temp_audio_removed", "path": path})

    def _close(self, root: Any) -> None:
        if not self.closed.is_set():
            self.closed.set()
            try:
                if self.cleanup_callback is not None:
                    self.cleanup_callback()
            except Exception as exc:
                try:
                    self.turns.output.send({
                        "type": "error",
                        "detail": f"microphone cleanup failed during chat shutdown: {type(exc).__name__}: {exc}",
                    })
                except Exception:
                    pass
            finally:
                try:
                    self.turns.put(_STOP)
                except Exception:
                    pass
        try:
            root.destroy()
        except Exception:
            pass


def _read_commands(commands: queue.Queue[tuple[Any, ...]], output: _ProtocolOutput) -> None:
    try:
        for line in sys.stdin:
            if len(line) > _MAX_LINE_CHARS:
                output.send({"type": "error", "detail": "chat IPC input exceeds the size limit"})
                commands.put(("close",))
                return
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                output.send({"type": "error", "detail": f"invalid chat IPC JSON: {exc}"})
                commands.put(("close",))
                return
            kind = data.get("type")
            if kind == "assistant":
                message = data.get("message", "")
                choices = data.get("choices", [])
                if not isinstance(message, str) or len(message) > 20_000:
                    output.send({"type": "error", "detail": "assistant message exceeds 20000 characters"})
                    continue
                if not isinstance(choices, list) or len(choices) > 8:
                    output.send({"type": "error", "detail": "invalid assistant choices"})
                    continue
                if any(not isinstance(c, str) or len(c) > 120 for c in choices):
                    output.send({"type": "error", "detail": "assistant choices exceed their size limit"})
                    continue
                commands.put(("assistant", message, tuple(choices)))
            elif kind == "observation_error":
                message = data.get("message", "Beobachtung fehlgeschlagen.")
                if not isinstance(message, str):
                    output.send({"type": "error", "detail": "invalid observation error notice"})
                    continue
                commands.put(("observation_error", message[:2_000]))
            elif kind == "close":
                commands.put(("close",))
                return
    finally:
        commands.put(("close",))


def main() -> int:
    output = _ProtocolOutput()
    first_line = sys.stdin.readline()
    if not first_line or len(first_line) > _MAX_LINE_CHARS:
        output.send({"type": "error", "detail": "missing or oversized chat initialization"})
        return 2
    try:
        init = json.loads(first_line)
        if init.get("type") != "init":
            raise ValueError("first IPC message must be init")
        title = str(init.get("title", "open-compute — Chat"))[:256]
        context = str(init.get("context", ""))
        chat_id = init.get("chat_id", "")
        choices = init.get("choices", [])
        if (
            len(context) > 8_000
            or not isinstance(choices, list)
            or len(choices) > 8
            or not isinstance(chat_id, str)
            or len(chat_id) > 128
        ):
            raise ValueError("chat initialization exceeds its size limit")
        if any(not isinstance(c, str) or len(c) > 120 for c in choices):
            raise ValueError("chat initialization choices exceed their size limit")
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        output.send({"type": "error", "detail": f"invalid chat initialization: {exc}"})
        return 2

    commands: queue.Queue[tuple[Any, ...]] = queue.Queue()
    threading.Thread(
        target=_read_commands, args=(commands, output),
        name="open-compute-chat-stdin", daemon=True,
    ).start()
    session = _WindowSession(commands, output, chat_id=chat_id)
    ChatSession._run_ui(session, title, context, tuple(str(c) for c in choices))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
