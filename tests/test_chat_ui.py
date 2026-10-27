"""Contract tests for the native host-model chat bridge."""

import os
import sys
import time

import pytest

from open_compute.chat_ui import ChatBridge, ChatSession, HumanTurn


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

\n