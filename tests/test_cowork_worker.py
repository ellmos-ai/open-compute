import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from open_compute.cowork_worker import CodexWorker, DISABLED


def worker_script(tmp_path, body):
    path = tmp_path / "fake_cli.py"
    path.write_text("import sys,json,os,time,subprocess\nsys.stdin.read()\n" + body, encoding="utf-8")
    return CodexWorker(model="test-explicit-model", command=[sys.executable, str(path)], deadline_seconds=3)


def event(kind, **data):
    return "print(" + repr(json.dumps({"type": kind, **data})) + ",flush=True)\n"


def test_worker_structured_output_usage_and_sandbox_argv(tmp_path):
    record = tmp_path / "record.json"
    reply = {"text": "Vorschlag", "action": {"type": "type", "text": "äöü", "x": None,
             "y": None, "scroll_direction": None, "scroll_amount": None}, "done": False, "needs_human": None}
    body = "from pathlib import Path\nPath(" + repr(str(record)) + ").write_text(json.dumps({'argv':sys.argv,'cwd':os.getcwd()}))\n"
    body += event("thread.started", thread_id="fake")
    body += event("item.completed", item={"type": "agent_message", "text": json.dumps(reply)})
    body += event("turn.completed", usage={"input_tokens": 99, "cached_input_tokens": 12,
                                          "output_tokens": 7, "reasoning_output_tokens": 2})
    worker = worker_script(tmp_path, body)
    result = worker.reason({"goal": "nur testen"}, threading.Event())
    assert result["action"] == {"type": "type", "text": "äöü"}
    assert worker.last_usage == {"input_tokens": 99, "cached_input_tokens": 12,
                                 "output_tokens": 7, "reasoning_output_tokens": 2}
    actual = json.loads(record.read_text())
    argv = actual["argv"]
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv and "--ephemeral" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--model") + 1] == "test-explicit-model"
    assert all(argv[argv.index(feature) - 1] == "--disable" for feature in DISABLED)
    assert "skills.include_instructions=false" in argv
    assert "model_instructions_file" not in " ".join(argv)
    assert Path(actual["cwd"]).name.startswith("open-compute-reason-")
    assert worker.process is None and worker.job is None


@pytest.mark.parametrize("bad", ["command_execution", "mcp_tool_call", "web_search", "file_change"])
def test_unexpected_tool_events_fail_closed(tmp_path, bad):
    worker = worker_script(tmp_path, event("item.started", item={"type": bad}) + "time.sleep(30)\n")
    with pytest.raises(PermissionError, match="unexpected"):
        worker.reason({}, threading.Event())
    assert worker.process is None and worker.job is None


def process_running(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as w
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat")
        return not stat.exists() or stat.read_text().split()[2] != "Z"
    except ProcessLookupError:
        return False


def test_cancel_kills_owned_process_tree(tmp_path):
    record = tmp_path / "child.pid"
    body = "from pathlib import Path\nchild=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
    body += "Path(" + repr(str(record)) + ").write_text(str(child.pid))\ntime.sleep(30)\n"
    worker = worker_script(tmp_path, body)
    cancel = threading.Event()
    errors = []
    def run():
        try:
            worker.reason({}, cancel)
        except PermissionError as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 3
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert record.exists()
    child = int(record.read_text())
    cancel.set()
    thread.join(5)
    assert not thread.is_alive() and errors
    deadline = time.monotonic() + 3
    while process_running(child) and time.monotonic() < deadline:
        time.sleep(.02)
    assert not process_running(child)
    assert worker.process is None and worker.job is None


def test_no_completed_turn_is_not_treated_as_success(tmp_path):
    worker = worker_script(tmp_path, event("item.completed", item={"type": "agent_message", "text": '{}'}))
    with pytest.raises(RuntimeError):
        worker.reason({}, threading.Event())
