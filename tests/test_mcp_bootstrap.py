"""Protocol tests for the fast MCP stdio bootstrap."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _start_server() -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(ROOT) if not current_pythonpath else str(ROOT) + os.pathsep + current_pythonpath
    )
    return subprocess.Popen(
        [sys.executable, "-m", "open_compute.mcp_bootstrap"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _send(process: subprocess.Popen[bytes], message: dict) -> None:
    assert process.stdin is not None
    process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
    process.stdin.flush()


def _read_line(process: subprocess.Popen[bytes], timeout: float = 10.0) -> bytes:
    assert process.stdout is not None
    result: list[bytes] = []

    def read() -> None:
        assert process.stdout is not None
        result.append(process.stdout.readline())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        process.kill()
        pytest.fail(f"MCP bootstrap produced no response within {timeout:.1f}s")
    assert result and result[0], "MCP bootstrap closed stdout without a response"
    return result[0]


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


def test_initialize_is_answered_before_fastmcp_import() -> None:
    process = _start_server()
    try:
        started = time.perf_counter()
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        response = json.loads(_read_line(process))
        elapsed = time.perf_counter() - started

        assert response["id"] == 1
        assert response["result"]["protocolVersion"] == "2025-06-18"
        assert response["result"]["serverInfo"]["name"] == "open-compute"
        assert elapsed < 3.0, f"initialize response took {elapsed:.3f}s"
    finally:
        _stop(process)


def test_bootstrap_handoff_keeps_tools_available() -> None:
    pytest.importorskip("mcp")
    process = _start_server()
    try:
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        initialize_response = json.loads(_read_line(process))
        assert initialize_response["id"] == 1

        _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_response = json.loads(_read_line(process, timeout=20.0))
        names = {tool["name"] for tool in tools_response["result"]["tools"]}

        assert tools_response["id"] == 2
        assert "chat" in names
        assert len(names) == 19
    finally:
        _stop(process)
