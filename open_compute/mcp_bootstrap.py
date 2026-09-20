"""Fast stdio bootstrap for the open-compute MCP server.

The FastMCP import graph can take several seconds on a cold Python process.
MCP clients, however, only need the small ``initialize`` response before they
can keep the connection alive.  This module answers that request with the
standard library alone, then hands the same stdio stream to the real server.

Keep this module deliberately free of ``mcp`` imports: importing
``mcp.server.fastmcp`` is the latency this path is designed to hide.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

_SUPPORTED_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)
_LATEST_PROTOCOL_VERSION = _SUPPORTED_PROTOCOL_VERSIONS[-1]
_SERVER_CAPABILITIES = {
    "experimental": {},
    "prompts": {"listChanged": False},
    "resources": {"subscribe": False, "listChanged": False},
    "tools": {"listChanged": False},
}


def _server_version() -> str:
    """Return the installed MCP SDK version without importing the SDK."""
    try:
        return version("mcp")
    except PackageNotFoundError:  # pragma: no cover - editable/dev fallback
        return "unknown"


def initialization_response(request: dict[str, Any]) -> dict[str, Any]:
    """Build the same protocol-level response FastMCP sends for initialize."""
    params = request.get("params") or {}
    requested_version = params.get("protocolVersion")
    protocol_version = (
        requested_version
        if requested_version in _SUPPORTED_PROTOCOL_VERSIONS
        else _LATEST_PROTOCOL_VERSION
    )
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {
            "protocolVersion": protocol_version,
            "capabilities": _SERVER_CAPABILITIES,
            "serverInfo": {"name": "open-compute", "version": _server_version()},
        },
    }


def _write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _read_initialize() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.readline()
    if not raw:
        return None
    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(request, dict) or request.get("method") != "initialize":
        return None
    if "id" not in request:
        return None
    return request


def main() -> None:
    """Answer initialize immediately, then load and run the full server."""
    request = _read_initialize()
    if request is None:
        return

    _write_message(initialization_response(request))

    # Import only after the response is on the wire.  The real server owns all
    # tool registration and safety behavior; this module only hides cold-start
    # latency for the mandatory handshake.
    from . import mcp_server

    mcp_server.run_stdio_after_initialize()


if __name__ == "__main__":  # pragma: no cover - exercised by the console entry point
    main()
