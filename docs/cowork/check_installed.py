"""Run with the isolated wheel environment, outside the source root.

This smoke starts the real packaged CLI/host with no model SDK or desktop extras.
The host response is supplied deterministically; no provider inference occurs.
"""
from importlib.resources import files
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit

import open_compute
from open_compute.cowork_http import model_request


def main():
    package = Path(open_compute.__file__).resolve()
    assert "site-packages" in str(package), f"source tree leaked into smoke: {package}"
    for name in ("index.html", "app.js", "style.css"):
        data = files("open_compute").joinpath("cowork_ui", name).read_text(encoding="utf-8-sig")
        assert len(data) > 100
    with tempfile.TemporaryDirectory(prefix="oc-cowork-wheel-") as temp:
        runtime = Path(temp) / "runtime.json"
        child = subprocess.Popen([sys.executable, "-m", "open_compute.cli", "cowork", "--no-open",
                                  "--runtime", str(runtime), "--max-turns", "3"],
                                  cwd=temp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8",
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                  env={**__import__("os").environ, "PYTHONUTF8": "1"})
        try:
            first = child.stdout.readline().strip()
            assert first.startswith("open-compute Cowork: "), first
            url = urlsplit(first.split(": ", 1)[1])
            origin = f"{url.scheme}://{url.netloc}"
            def ui(path, data=None):
                headers = {"Authorization": "Bearer " + url.fragment, "Origin": origin,
                           "Content-Type": "application/json"}
                req = urllib.request.Request(origin + path,
                     data=json.dumps(data).encode("utf-8") if data is not None else None,
                     headers=headers)
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.load(response)
            with urllib.request.urlopen(origin) as response:
                html = response.read().decode("utf-8-sig")
            assert "Mikrofon" in html and "Besprechen" in html and "Übernehmen" in html
            state = ui("/ui/state")
            assert state["work_mode"] == "discuss" and state["target"] is None
            ui("/ui/message", {"text": "Text ohne Desktop äöü", "message_id": "wheel-1"})
            turn = model_request("next", {"identity": "wheel smoke / deterministic host"}, descriptor=runtime)
            assert turn["context"]["work_mode"] == "discuss"
            model_request("reply", {"turn_id": turn["context"]["turn_id"], "claim": turn["claim"],
                                     "text": "Antwort aus dem installierten Paket"}, descriptor=runtime)
            state = ui("/ui/state")
            assert state["messages"][-1]["text"] == "Antwort aus dem installierten Paket"
            assert state["metrics"]["delivered_turns"] == 1 and not state["offers"]
            ui("/ui/stop", {})
            assert child.wait(timeout=15) == 0
            assert not runtime.exists()
            print(json.dumps({"status": "PASS", "package": str(package),
                              "assets": 3, "model_turns_delivered": 1,
                              "provider_calls": 0, "desktop_inputs": 0,
                              "runtime_removed": True}, ensure_ascii=False))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


if __name__ == "__main__":
    main()
