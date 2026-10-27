"""Contract tests for the native host-model chat bridge."""

import os
import queue
import io
import json
import subprocess
import sys
import time
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from open_compute.chat_ui import (
    COLLABORATION_PROMPTS,
    ChatBridge,
    ChatSession,
    HumanTurn,
    insert_collaboration_prompt,
    observation_capture_allowed,
)
from open_compute.voice_io_bridge import (
    MODEL_ENV,
    MicrophoneRecording,
    transcribe_local_wav,
    transcription_status,
)


class _PromptEntry:
    def __init__(self, selection=()):
        self.selection = selection
        self.operations = []

    def tag_ranges(self, _tag):
        return self.selection

    def delete(self, *args):
        self.operations.append(("delete", *args))

    def insert(self, *args):
        self.operations.append(("insert", *args))

    def focus_set(self):
        self.operations.append(("focus",))


class _FakeMci:
    def __init__(self, errors=None):
        self.commands = []
        self.errors = errors or {}

    def __call__(self, command):
        self.commands.append(command)
        if command.startswith("save "):
            path = Path(command.split('"')[1])
            path.write_bytes(b"RIFF" + b"0" * 64)
        for prefix, code in self.errors.items():
            if command.startswith(prefix):
                return code
        return 0


def _mci_factory(fake):
    return lambda: fake


def test_collaboration_prompts_are_inserted_for_editing_without_sending():
    assert [label for label, _ in COLLABORATION_PROMPTS] == [
        "Kurz weg", "Wieder da", "Hilfe und Vorschläge",
    ]
    entry = _PromptEntry()

    insert_collaboration_prompt(entry, COLLABORATION_PROMPTS[0][1])

    assert entry.operations == [
        ("insert", "insert", COLLABORATION_PROMPTS[0][1]),
        ("focus",),
    ]


def test_collaboration_prompt_replaces_selected_text_in_editable_field():
    entry = _PromptEntry(selection=("sel.first", "sel.last"))

    insert_collaboration_prompt(entry, "Bearbeite diesen Vorschlag")

    assert entry.operations == [
        ("delete", "sel.first", "sel.last"),
        ("insert", "insert", "Bearbeite diesen Vorschlag"),
        ("focus",),
    ]


def test_observation_event_is_captured_once_and_only_one_frame_is_cached():
    session = ChatSession(chat_id="observation-cache")
    calls = []

    first = session.capture_observation_once(
        "event-1", lambda: calls.append("capture") or {"status": "ok", "png": b"frame"}
    )
    replay = session.capture_observation_once(
        "event-1", lambda: calls.append("duplicate") or {"status": "ok", "png": b"wrong"}
    )

    assert first == replay == {"status": "ok", "png": b"frame"}
    assert calls == ["capture"]

    session.capture_observation_once(
        "event-2", lambda: {"status": "ok", "png": b"new frame"}
    )
    expired = session.capture_observation_once(
        "event-1", lambda: calls.append("expired recapture") or {"status": "ok", "png": b"wrong"},
        replayed=True,
    )
    assert expired["status"] == "expired"
    assert "expired recapture" not in calls
    session.dispose()
    assert session.observation_reply_cache is None
    assert session.observation_processed_ids == []


def test_observation_revocation_invalidates_queued_and_cached_frame_before_return():
    session = ChatSession(chat_id="observation-revoked")
    target = {"hwnd": 44, "pid": 55, "title": "Testfenster"}
    session.update_observation_state(
        enabled=True, screenshot_consent=True, window=target
    )
    queued = HumanTurn(
        "Prüfe das Fenster", "Bildschirmhilfe", True,
        observation_update=True, observation_enabled=True,
        observation_window=target, observation_event_id="queued-before-off",
        observation_generation=session.observation_generation,
    )
    session.update_observation_state(
        enabled=False, screenshot_consent=False, window=None
    )
    captures = []
    denied = session.capture_observation_once(
        queued.observation_event_id,
        lambda: captures.append(True) or {"status": "ok", "png": b"private"},
        authorized=lambda: session.observation_authorizes(queued),
    )
    assert denied["status"] == "revoked"
    assert captures == []


def test_observation_revocation_during_capture_discards_bytes_before_cache():
    session = ChatSession(chat_id="observation-revoked-in-flight")
    target = {"hwnd": 44, "pid": 55, "title": "Testfenster"}
    session.update_observation_state(
        enabled=True, screenshot_consent=True, window=target
    )
    turn = HumanTurn(
        "Prüfe das Fenster", "Bildschirmhilfe", True,
        observation_update=True, observation_enabled=True,
        observation_window=target, observation_event_id="pixel-read-revoke",
        observation_generation=session.observation_generation,
    )
    checks = []

    def authorized():
        checks.append(session.observation_authorizes(turn))
        return checks[-1]

    def capture():
        session.update_observation_state(
            enabled=False, screenshot_consent=False, window=None
        )
        return {"status": "ok", "metadata": {"kind": "fake"}, "png": b"synthetic-frame"}

    result = session.capture_observation_once(
        turn.observation_event_id, capture, authorized=authorized
    )
    assert checks[0] is True and checks[-1] is False
    assert result["status"] == "revoked"
    assert session.observation_reply_cache is None
    replay = session.capture_observation_once(
        turn.observation_event_id,
        lambda: pytest.fail("revoked event must not be captured again"),
        replayed=True, authorized=authorized,
    )
    assert replay["status"] == "revoked"


def test_observation_revocation_after_cache_lookup_discards_replay_frame():
    session = ChatSession(chat_id="observation-cache-revoked-return")
    target = {"hwnd": 44, "pid": 55, "title": "Testfenster"}
    session.update_observation_state(
        enabled=True, screenshot_consent=True, window=target
    )
    turn = HumanTurn(
        "Prüfe das Fenster", "Bildschirmhilfe", True,
        observation_update=True, observation_enabled=True,
        observation_window=target, observation_event_id="cached-revoke-event",
        observation_generation=session.observation_generation,
    )
    first = session.capture_observation_once(
        turn.observation_event_id,
        lambda: {"status": "ok", "metadata": {"kind": "fake"}, "png": b"synthetic"},
        authorized=lambda: session.observation_authorizes(turn),
    )
    assert first["status"] == "ok"
    checks = 0

    def revoke_after_cache_lookup():
        nonlocal checks
        checks += 1
        if checks == 1:
            return True
        session.update_observation_state(
            enabled=False, screenshot_consent=False, window=None
        )
        return False

    replay = session.capture_observation_once(
        turn.observation_event_id,
        lambda: pytest.fail("cached frame path must not recapture"),
        authorized=revoke_after_cache_lookup,
    )
    assert replay["status"] == "revoked"
    assert session.observation_reply_cache is None


def test_observation_scope_change_invalidates_turn_and_previously_cached_image():
    session = ChatSession(chat_id="observation-window-change")
    first = {"hwnd": 44, "pid": 55, "title": "Testfenster"}
    other = {"hwnd": 45, "pid": 56, "title": "Anderes Fenster"}
    session.update_observation_state(
        enabled=True, screenshot_consent=True, window=first
    )
    turn = HumanTurn(
        "Prüfe das Fenster", "Bildschirmhilfe", True,
        observation_update=True, observation_enabled=True,
        observation_window=first, observation_event_id="scope-event",
        observation_generation=session.observation_generation,
    )
    captures = []
    captured = session.capture_observation_once(
        turn.observation_event_id,
        lambda: captures.append(True) or {"status": "ok", "png": b"private"},
        authorized=lambda: session.observation_authorizes(turn),
    )
    assert captured["status"] == "ok"
    session.update_observation_state(
        enabled=True, screenshot_consent=True, window=other
    )
    replay = session.capture_observation_once(
        turn.observation_event_id,
        lambda: captures.append("duplicate") or {"status": "ok", "png": b"wrong"},
        authorized=lambda: session.observation_authorizes(turn),
    )
    assert replay["status"] == "revoked"
    assert captures == [True]


def test_observation_session_close_invalidates_queued_turn_before_capture():
    session = ChatSession(chat_id="observation-session-close")
    target = {"hwnd": 44, "pid": 55, "title": "Testfenster"}
    session.update_observation_state(
        enabled=True, screenshot_consent=True, window=target
    )
    turn = HumanTurn(
        "Prüfe das Fenster", "Bildschirmhilfe", True,
        observation_update=True, observation_enabled=True,
        observation_window=target, observation_event_id="closed-session-event",
        observation_generation=session.observation_generation,
    )
    session.closed.set()
    captures = []
    outcome = session.capture_observation_once(
        turn.observation_event_id,
        lambda: captures.append(True) or {"status": "ok", "png": b"private"},
        authorized=lambda: session.observation_authorizes(turn),
    )
    assert outcome["status"] == "revoked"
    assert captures == []


def test_observation_ipc_revocation_after_turn_is_queued_prevents_capture():
    session = ChatSession(chat_id="observation-ipc-revoke")
    target = {"hwnd": 44, "pid": 55, "title": "Testfenster"}
    messages = [
        {"type": "observation_state", "generation": 1, "enabled": True,
         "screenshot_consent": True, "window": target},
        {"type": "human", "message": "Prüfe das Fenster", "mode": "Bildschirmhilfe",
         "screenshot": True, "observation_update": True, "observation_enabled": True,
         "observation_window": target, "observation_event_id": "queued-then-off",
         "observation_generation": 1},
        {"type": "observation_state", "generation": 2, "enabled": False,
         "screenshot_consent": False, "window": None},
    ]
    session.process = SimpleNamespace(
        stdout=io.StringIO("".join(json.dumps(item) + "\n" for item in messages)),
        wait=lambda: 0,
    )
    session._read_child_output()
    turn, timed_out = session.next_turn(timeout=0.1)
    assert not timed_out and turn is not None
    captures = []
    outcome = session.capture_observation_once(
        turn.observation_event_id,
        lambda: captures.append(True) or {"status": "ok", "png": b"private"},
        authorized=lambda: session.observation_authorizes(turn),
    )
    assert outcome["status"] == "revoked"
    assert captures == []


def test_ui_child_turn_sink_publishes_consent_generation_and_scope():
    from open_compute.chat_ui_process import _TurnSink

    class Output:
        def __init__(self):
            self.messages = []

        def send(self, message):
            self.messages.append(message)

    output = Output()
    sink = _TurnSink(output)
    target = {"hwnd": 44, "pid": 55, "title": "Testfenster"}
    sink.put({
        "type": "observation_state", "generation": 4, "enabled": True,
        "screenshot_consent": True, "window": target,
    })
    turn = HumanTurn(
        "Prüfe das Fenster", "Bildschirmhilfe", True,
        observation_update=True, observation_enabled=True,
        observation_window=target, observation_event_id="generation-4",
        observation_generation=4,
    )
    sink.put(turn)
    assert output.messages[0]["generation"] == 4
    assert output.messages[0]["window"] == target
    assert output.messages[1]["observation_generation"] == 4


def test_observation_event_rejects_empty_or_oversized_id_without_capture():
    session = ChatSession(chat_id="observation-invalid")
    calls = []

    for event_id in ("", "x" * 129):
        result = session.capture_observation_once(
            event_id, lambda: calls.append(event_id) or {"status": "ok", "png": b"frame"}
        )
        assert result["status"] == "error"
    assert calls == []

    replay = session.capture_observation_once(
        "uncached-replay",
        lambda: calls.append("replay recapture") or {"status": "ok", "png": b"frame"},
        replayed=True,
    )
    assert replay["status"] == "expired"
    assert calls == []


def test_observation_tick_requires_current_mode_consent_and_selected_window():
    target = {"hwnd": 44, "pid": 55, "title": "Testfenster"}
    assert observation_capture_allowed(
        enabled=True, screenshot_consent=True, window=target
    )
    assert not observation_capture_allowed(
        enabled=False, screenshot_consent=True, window=target
    )
    assert not observation_capture_allowed(
        enabled=True, screenshot_consent=False, window=target
    )
    assert not observation_capture_allowed(
        enabled=True, screenshot_consent=True, window=None
    )


def test_microphone_recording_saves_one_temporary_wav_and_closes_device(tmp_path):
    mci = _FakeMci()
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci), temp_dir=tmp_path)

    path = recorder.start()
    assert not path.exists()
    recorded_path = recorder.stop()

    assert recorded_path == path
    assert recorded_path.read_bytes().startswith(b"RIFF")
    assert any(command.startswith("record ") for command in mci.commands)
    assert any(command.startswith("save ") for command in mci.commands)
    assert any(command.startswith("close ") for command in mci.commands)
    recorded_path.unlink()


def test_microphone_cancel_closes_device_and_deletes_temporary_audio(tmp_path):
    mci = _FakeMci()
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci), temp_dir=tmp_path)
    path = recorder.start()

    assert recorder.cancel() == path
    assert not path.exists()
    assert recorder.alias is None
    assert any(command.startswith("close ") for command in mci.commands)


def test_microphone_stop_error_is_reported_and_successful_close_releases_handle(tmp_path):
    mci = _FakeMci({"stop ": 277})
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci), temp_dir=tmp_path)
    path = recorder.start()

    with pytest.raises(RuntimeError, match="gestoppt.*MCI 277"):
        recorder.stop()

    assert recorder.alias is None
    assert recorder.mci is None
    assert not path.exists()
    assert any(command.startswith("close ") for command in mci.commands)


def test_microphone_stop_close_error_keeps_handle_for_retry(tmp_path):
    mci = _FakeMci({"close ": 277})
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci), temp_dir=tmp_path)
    path = recorder.start()

    with pytest.raises(RuntimeError, match="freigegeben.*MCI 277"):
        recorder.stop()

    assert recorder.alias is not None
    assert recorder.mci is mci
    assert recorder.path == path
    mci.errors.clear()
    assert recorder.stop() == path
    assert recorder.alias is None
    assert path.is_file()
    path.unlink()


def test_microphone_cancel_reports_stop_error_but_clears_after_successful_close(tmp_path):
    mci = _FakeMci({"stop ": 277})
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci), temp_dir=tmp_path)
    path = recorder.start()

    with pytest.raises(RuntimeError, match="Abbruch.*MCI 277"):
        recorder.cancel()

    assert recorder.alias is None
    assert recorder.mci is None
    assert not path.exists()


def test_microphone_cancel_close_error_preserves_handle_and_retry_cleans(tmp_path):
    mci = _FakeMci({"close ": 277})
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci), temp_dir=tmp_path)
    path = recorder.start()

    with pytest.raises(RuntimeError, match="freigegeben.*MCI 277"):
        recorder.cancel()

    assert recorder.alias is not None
    assert recorder.mci is mci
    assert recorder.path == path
    mci.errors.clear()
    assert recorder.cancel() == path
    assert recorder.alias is None
    assert not path.exists()


def test_microphone_cleanup_error_is_reported_on_window_shutdown():
    from open_compute.chat_ui_process import _WindowSession

    mci = _FakeMci({"close ": 277})
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci))
    recorder.start()
    output = SimpleNamespace(sent=[])
    output.send = lambda message: output.sent.append(message)
    window = _WindowSession(queue.Queue(), output)
    window.cleanup_callback = recorder.cancel
    root = SimpleNamespace(destroy=lambda: None)

    window._close(root)

    assert window.closed.is_set()
    assert recorder.alias is not None
    assert [item["type"] for item in output.sent] == ["error", "closed"]
    assert "MCI 277" in output.sent[0]["detail"]


def test_window_shutdown_destroys_root_even_if_client_pipe_is_broken():
    from open_compute.chat_ui_process import _WindowSession

    class _BrokenOutput:
        def send(self, _message):
            raise BrokenPipeError("parent exited")

    destroyed = []
    window = _WindowSession(queue.Queue(), _BrokenOutput())
    root = SimpleNamespace(destroy=lambda: destroyed.append(True))

    window._close(root)

    assert window.closed.is_set()
    assert destroyed == [True]


def test_chat_session_only_cleans_its_temporary_audio_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    session = ChatSession(chat_id="temp-audio-cleanup")
    owned = tmp_path / "open-compute-chat-audio-owned.wav"
    other = tmp_path / "user-audio.wav"
    owned.write_bytes(b"temporary")
    other.write_bytes(b"preserve")

    session._track_temp_audio(str(owned))
    session._track_temp_audio(str(other))
    session._cleanup_temp_audio()

    assert not owned.exists()
    assert other.read_bytes() == b"preserve"


def test_voice_io_bridge_uses_the_configured_local_model_and_german(tmp_path, monkeypatch):
    model = tmp_path / "vosk"
    model.mkdir()
    monkeypatch.setenv(MODEL_ENV, str(model))
    audio = tmp_path / "spoken.wav"
    audio.write_bytes(b"local audio")
    calls = []

    class FakeSpeechToText:
        def __init__(self, *, engine, model_path):
            calls.append(("init", engine, Path(model_path)))

        def available(self):
            return True, "vosk"

        def transcribe_file(self, path, *, language):
            calls.append(("transcribe", Path(path), language))
            return "Das ist ein lokaler Test."

    monkeypatch.setitem(
        sys.modules, "ellmos_voice_io", SimpleNamespace(SpeechToText=FakeSpeechToText)
    )

    assert transcription_status() == (True, "Bereit: Sprache wird lokal transkribiert.")
    assert transcribe_local_wav(audio) == "Das ist ein lokaler Test."
    assert calls[-1] == ("transcribe", audio, "de")


def _bridge_with_pending_turn() -> tuple[ChatBridge, ChatSession]:
    bridge = ChatBridge()
    session = ChatSession(chat_id="stable-chat")
    bridge._sessions[session.chat_id] = session
    session.turns.put(HumanTurn("Ja, bitte", "Kurz", False, 0))
    return bridge, session


def test_assistant_reply_roundtrip_and_duplicate_turn_replay():
    bridge, session = _bridge_with_pending_turn()

    _, first, timed_out, replayed = bridge.reply(
        session.chat_id,
        assistant_message="Ich kann helfen.",
        assistant_turn_id="host-turn-1",
        choices=("Weiter",),
        timeout=0.1,
    )
    _, retry, retry_timeout, retry_replayed = bridge.reply(
        session.chat_id,
        assistant_message="Ich kann helfen.",
        assistant_turn_id="host-turn-1",
        choices=("Weiter",),
        timeout=0.1,
    )

    assert first == retry == HumanTurn("Ja, bitte", "Kurz", False, 0)
    assert timed_out is retry_timeout is False
    assert replayed is False
    assert retry_replayed is True
    assert session.commands.qsize() == 1
    assert session.commands.get_nowait() == ("assistant", "Ich kann helfen.", ("Weiter",))


def test_reused_turn_id_with_different_content_is_rejected():
    bridge, session = _bridge_with_pending_turn()
    bridge.reply(
        session.chat_id, assistant_message="Erste Antwort",
        assistant_turn_id="same-id", timeout=0.1,
    )

    try:
        bridge.reply(
            session.chat_id, assistant_message="Andere Antwort",
            assistant_turn_id="same-id", timeout=0.1,
        )
    except ValueError as exc:
        assert "different content" in str(exc)
    else:
        raise AssertionError("changed content under a reused turn ID must fail")


def test_timeout_then_poll_delivers_a_later_human_turn():
    bridge, session = _bridge_with_pending_turn()
    session.turns.get_nowait()

    _, absent, timed_out = bridge.poll(session.chat_id, timeout=0.001)
    assert absent is None
    assert timed_out is True

    session.turns.put(HumanTurn("Weiter bitte", "Brainstorming", False))
    _, later, timed_out = bridge.poll(session.chat_id, timeout=0.1)
    assert later == HumanTurn("Weiter bitte", "Brainstorming", False)
    assert timed_out is False


def test_close_releases_waiting_turn_and_forgets_session():
    bridge, session = _bridge_with_pending_turn()
    assert bridge.close(session.chat_id) is True
    assert session.closed.is_set()
    assert session.next_turn(timeout=0.1) == (None, False)
    assert bridge.close(session.chat_id) is False


def test_close_all_cleans_broken_pipe_child_and_continues_to_other_sessions():
    class _BrokenPipeStream:
        def write(self, _text):
            raise BrokenPipeError("pipe closed")

        def flush(self):
            raise BrokenPipeError("pipe closed")

        def close(self):
            pass

    class _FakeProcess:
        def __init__(self):
            self.stdin = _BrokenPipeStream()
            self.stdout = SimpleNamespace(close=lambda: None)
            self.returncode = None
            self.wait_calls = []
            self.terminate_calls = 0
            self.kill_calls = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.wait_calls.append(timeout)
            self.returncode = 0
            return self.returncode

        def terminate(self):
            self.terminate_calls += 1
            self.returncode = 0

        def kill(self):
            self.kill_calls += 1
            self.returncode = 0

    bridge = ChatBridge()
    disposed = []
    sessions = []
    for chat_id in ("broken-child", "second-child"):
        session = ChatSession(chat_id=chat_id)
        session.process = _FakeProcess()
        session.dispose = lambda key=chat_id: disposed.append(key)
        bridge._sessions[chat_id] = session
        sessions.append(session)

    bridge.close_all()

    assert bridge._sessions == {}
    assert all(session.closed.is_set() for session in sessions)
    assert all(session.process.wait_calls == [3] for session in sessions)
    assert disposed == ["broken-child", "second-child"]


def test_close_escalates_timeout_to_terminate_and_collects_child_exit():
    class _FakeStream:
        def write(self, _text):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    class _SlowProcess:
        def __init__(self):
            self.stdin = _FakeStream()
            self.stdout = _FakeStream()
            self.returncode = None
            self.wait_calls = []
            self.terminate_calls = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.wait_calls.append(timeout)
            if len(self.wait_calls) == 1:
                raise subprocess.TimeoutExpired("chat-ui", timeout)
            self.returncode = 0
            return 0

        def terminate(self):
            self.terminate_calls += 1

        def kill(self):
            raise AssertionError("kill is unnecessary after terminate succeeds")

    bridge = ChatBridge()
    session = ChatSession(chat_id="slow-child")
    process = _SlowProcess()
    session.process = process
    session.dispose = lambda: None
    bridge._sessions[session.chat_id] = session

    assert bridge.close(session.chat_id) is True
    assert process.wait_calls == [3, 2]
    assert process.terminate_calls == 1
    assert process.returncode == 0


@pytest.mark.skipif(
    sys.platform != "win32" and not os.environ.get("DISPLAY"),
    reason="native Tk process smoke requires a desktop display",
)
def test_native_tk_child_starts_and_shuts_down_cleanly():
    session = ChatSession(chat_id="pytest-native-window")
    session.start(title="open-compute Chat Test", context="Startprüfung", choices=("Ja",))
    try:
        assert session.process is not None and session.process.poll() is None
        session.add_assistant("Antwort des Hostmodells", ("Weiter",))
        time.sleep(0.2)
        session._send_command({"type": "close"})
        session.process.wait(timeout=5)
        assert session.process.poll() is not None
    finally:
        if session.process is not None and session.process.poll() is None:
            session.process.terminate()
            session.process.wait(timeout=2)
