"""Loopback-only Cowork host and separate, capability-authenticated model client."""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import webbrowser

from .cowork import Budgets, CoworkService, encoded


def runtime_path() -> Path:
    return Path(os.environ.get("OC_COWORK_RUNTIME", str(Path.home() / ".open-compute" / "cowork.json")))


class CoworkHost:
    def __init__(self, service: CoworkService, *, port: int = 0):
        self.service = service
        self.model_key = secrets.token_urlsafe(32)
        host = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass  # Request paths/credentials never enter a request log.

            def _send(self, code, value, content_type="application/json; charset=utf-8"):
                data = value if isinstance(value, bytes) else encoded(value).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                if self.close_connection:
                    self.send_header("Connection", "close")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                                 "style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                                 "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    self.close_connection = True

            def _check(self):
                if self.headers.get("Host") != host.authority:
                    raise PermissionError("invalid Host")
                origin = self.headers.get("Origin")
                if origin is not None and origin != host.origin:
                    raise PermissionError("invalid Origin")
                if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
                    raise PermissionError("cross-origin request blocked")

            def _auth(self, model=False):
                self._check()
                expected = host.model_key if model else service._ui_key
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied, "Bearer " + expected):
                    raise PermissionError("invalid capability")
                if not model and self.command == "POST":
                    if self.headers.get("Origin") != host.origin:
                        raise PermissionError("UI mutation requires same-origin request")

            def do_GET(self):
                try:
                    self._check()
                    path = urlsplit(self.path).path
                    if path in {"/", "/app.js", "/style.css"}:
                        name, mime = {"/": ("index.html", "text/html; charset=utf-8"),
                                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                                      "/style.css": ("style.css", "text/css; charset=utf-8")}[path]
                        return self._send(200, files("open_compute").joinpath("cowork_ui", name).read_bytes(), mime)
                    self._auth()
                    if path == "/ui/state":
                        return self._send(200, service.snapshot(human_key=service._ui_key))
                    if path == "/ui/reopen":
                        with service.lock:
                            service.ui_seen = service.clock()
                        return self._send(200, {"session_id": service.session_id, "pid": os.getpid()})
                    if path == "/ui/windows":
                        if service.work_mode == "human_only":
                            raise PermissionError("Ich arbeite allein: kein Desktop-Zugriff")
                        return self._send(200, {"windows": service.backend.windows()})
                    raise ValueError("unknown route")
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
                except Exception as exc:
                    self._send(400, {"error": str(exc)})

            def do_POST(self):
                try:
                    # Consume a bounded body before rejecting credentials/Host:
                    # unread POST bytes can reset a Windows socket before 403 arrives.
                    self.close_connection = True
                    self.connection.settimeout(2)
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= 32768:
                        raise ValueError("request body budget exceeded")
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError("incomplete request body")
                    path = urlsplit(self.path).path
                    model = path.startswith("/model/")
                    self._auth(model=model)
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise ValueError("application/json required")
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError("object body required")
                    key = service._ui_key
                    if path == "/ui/configure":
                        result = service.configure(key, **data)
                    elif path == "/ui/work-mode":
                        result = service.set_work_mode(key, **data)
                    elif path == "/ui/task-start":
                        result = service.tasks.start_task(key, **data)
                    elif path == "/ui/message":
                        result = service.message(key, **data)
                    elif path == "/ui/decide":
                        result = service.decide(key, **data)
                    elif path == "/ui/pause":
                        result = service.interrupt(key)
                    elif path == "/ui/stop":
                        result = service.interrupt(key, stop=True)
                    elif path == "/ui/resume":
                        result = service.resume(key)
                    elif path == "/ui/observe":
                        result = service.observe()
                    elif path == "/model/observe":
                        result = service.model_observe()
                    elif path == "/model/lens":
                        result = service.model_lens(**data)
                    elif path == "/ui/lens":
                        with service.lock:
                            service._active()
                            if service.work_mode == "human_only":
                                raise PermissionError("Ich arbeite allein: keine Desktop-Beobachtung")
                            if not service.target:
                                raise ValueError("choose target first")
                            result = service.backend.lens(service.target)
                    elif path == "/model/next":
                        result = service.next_turn(**data)
                    elif path == "/model/reply":
                        result = service.reply(**data)
                    elif path == "/model/state":
                        # No UI nonces or human capability on the model channel.
                        result = service.model_status()
                    else:
                        raise ValueError("unknown or unauthorized route")
                    self._send(200, result)
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
                except Exception as exc:
                    self._send(400, {"error": f"{type(exc).__name__}: {exc}"})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.authority = f"127.0.0.1:{self.server.server_port}"
        self.origin = f"http://{self.authority}"
        self.done = threading.Event()
        self.thread = None
        self.watch = None
        self.descriptor_path: Path | None = None
        self.ui_descriptor_path: Path | None = None

    @property
    def ui_url(self):
        return self.origin + "/#" + self.service._ui_key

    def start(self, descriptor: Path | None = None):
        if descriptor is not None:
            descriptor = Path(descriptor)
            descriptor.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents a second execution authority replacing a live session.
            with descriptor.open("x", encoding="utf-8") as stream:
                os.chmod(descriptor, 0o600)
                json.dump({"schema": 1, "origin": self.origin, "model_key": self.model_key,
                           "session_id": self.service.session_id, "pid": os.getpid()}, stream)
            self.descriptor_path = descriptor
            self.ui_descriptor_path = descriptor.with_suffix(".ui.json")
            try:
                with self.ui_descriptor_path.open("x", encoding="utf-8") as stream:
                    os.chmod(self.ui_descriptor_path, 0o600)
                    json.dump({"schema": 1, "origin": self.origin, "ui_key": self.service._ui_key,
                               "session_id": self.service.session_id, "pid": os.getpid()}, stream)
            except Exception:
                descriptor.unlink()
                self.descriptor_path = None
                raise
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        def watchdog():
            while not self.done.wait(1):
                self.service.watchdog()
        self.watch = threading.Thread(target=watchdog, daemon=True)
        self.watch.start()
        return self

    def close(self):
        self.done.set()
        try:
            self.service.interrupt(self.service._ui_key, stop=True, reason="host closed")
        finally:
            self.service.tasks.close()
            self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=3)
            if self.watch:
                self.watch.join(timeout=2)
            if self.descriptor_path:
                try:
                    data = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
                    if data.get("session_id") == self.service.session_id:
                        self.descriptor_path.unlink()
                except (OSError, ValueError):
                    pass
            if self.ui_descriptor_path:
                try:
                    data = json.loads(self.ui_descriptor_path.read_text(encoding="utf-8"))
                    if data.get("session_id") == self.service.session_id:
                        self.ui_descriptor_path.unlink()
                except (OSError, ValueError):
                    pass


def existing_ui_url(runtime: Path) -> str:
    """Reopen one authenticated live session; no new authority and no model turn."""
    private = runtime.with_suffix(".ui.json")
    if runtime.stat().st_size > 4096 or private.stat().st_size > 4096:
        raise PermissionError("session descriptor exceeds budget")
    model = json.loads(runtime.read_text(encoding="utf-8"))
    human = json.loads(private.read_text(encoding="utf-8"))
    if any(model.get(k) != human.get(k) for k in ("origin", "session_id", "pid")):
        raise PermissionError("session descriptors do not match")
    parsed = urlsplit(human["origin"])
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise PermissionError("UI session must be loopback-only")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(human["origin"] + "/ui/reopen",
                                 headers={"Authorization": "Bearer " + human["ui_key"]})
    with opener.open(req, timeout=5) as response:
        raw = response.read(4097)
        if len(raw) > 4096:
            raise PermissionError("UI readback exceeds budget")
        live = json.loads(raw)
    if live.get("session_id") != human["session_id"] or live.get("pid") != human["pid"]:
        raise PermissionError("live session/PID mismatch")
    return human["origin"] + "/#" + human["ui_key"]


def model_request(operation: str, data: dict | None = None, *, descriptor: Path | None = None) -> dict:
    """Shared MCP/CLI transport. It cannot request a human/UI endpoint."""
    if operation not in {"next", "reply", "observe", "state", "lens"}:
        raise ValueError("unsupported model operation")
    config = json.loads((descriptor or runtime_path()).read_text(encoding="utf-8"))
    url = urlsplit(config["origin"])
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
            or url.username or url.password or url.path or url.query or url.fragment):
        raise PermissionError("descriptor must name a loopback Cowork host")
    req = urllib.request.Request(config["origin"] + "/model/" + operation,
                                 data=encoded(data or {}).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + config["model_key"],
                                          "Content-Type": "application/json"})
    # Ignore proxy environment; never disclose capability to a proxy or redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=15) as response:
            raw = response.read(262145)
            if len(raw) > 262144:
                raise ValueError("host response budget exceeded")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise PermissionError(exc.read(4096).decode("utf-8")) from exc


def open_window(url: str) -> str:
    """Prefer an Edge/Chrome app window. Never create a browser profile in OneDrive."""
    candidates = []
    for root in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        if root:
            candidates.extend([Path(root) / "Microsoft/Edge/Application/msedge.exe",
                               Path(root) / "Google/Chrome/Application/chrome.exe"])
    for executable in candidates:
        if executable.is_file():
            subprocess.Popen([str(executable), "--app=" + url, "--window-size=1080,800"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "browser-app"
    webbrowser.open(url, new=1)
    return "browser-fallback"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Standalone open-compute Cowork")
    parser.add_argument("operation", nargs="?", default="start", choices=["start", "next", "reply", "observe", "state"])
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--runtime", type=Path, default=None)
    parser.add_argument("--identity", default="CLI host model")
    parser.add_argument("--data", default="{}", help="JSON model reply arguments")
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument("--work-mode", choices=["human_only", "human_leads", "model_leads", "model_alone",
                                               "discuss", "observe", "confirm"], default="human_leads")
    parser.add_argument("--worker", choices=["codex", "none"], default="codex")
    parser.add_argument("--worker-model", default=os.environ.get("OC_COWORK_WORKER_MODEL", "gpt-6-astra"))
    args = parser.parse_args(argv)
    if args.operation != "start":
        data = {"identity": args.identity} if args.operation == "next" else json.loads(args.data)
        print(encoded(model_request(args.operation, data, descriptor=args.runtime)))
        return
    if not 1 <= args.max_turns <= 10000:
        parser.error("--max-turns must be 1..10000")
    from .cowork_desktop import DesktopBackend
    from .cowork_worker import CodexWorker
    runtime = args.runtime or runtime_path()
    if runtime.exists():
        try:
            url = existing_ui_url(runtime)
        except Exception as exc:
            parser.error(f"Existing session cannot be safely reopened: {exc}")
        print("open-compute Cowork: " + url, flush=True)
        if not args.no_open:
            open_window(url)
        return
    worker = CodexWorker(model=args.worker_model) if args.worker == "codex" else None
    host = CoworkHost(CoworkService(DesktopBackend(), budgets=Budgets(turns=args.max_turns),
                                  work_mode=args.work_mode, worker=worker,
                                  report_path=runtime.with_suffix(".reports.json")), port=args.port)
    try:
        host.start(runtime)
    except FileExistsError:
        host.service.tasks.close()
        host.server.server_close()
        parser.error("Cowork descriptor exists. Close the existing host, or inspect/remove a stale descriptor first.")
    print("open-compute Cowork: " + host.ui_url, flush=True)
    print(f"Worker: {worker.identity if worker and worker.available else 'nicht verfügbar / externer MCP-Host mit --worker none'}; bestehende Codex-Anmeldung, kein API-Schlüssel nötig.", flush=True)
    if not args.no_open:
        print("Fenster: " + open_window(host.ui_url), flush=True)
    try:
        while not host.done.wait(0.5):
            if host.service.closed and not host.service.executing:
                break
    except KeyboardInterrupt:
        pass
    finally:
        host.close()


if __name__ == "__main__":
    main()
