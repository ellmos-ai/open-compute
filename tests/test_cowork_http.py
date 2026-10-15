import json
import urllib.error
import urllib.request

import pytest

from open_compute.cowork import CoworkService
from open_compute.cowork_http import CoworkHost, model_request
from test_cowork import FakeDesktop


@pytest.fixture
def host(tmp_path):
    service = CoworkService(FakeDesktop(), mode="confirm", _grace_seconds=0, work_mode="confirm")
    host = CoworkHost(service).start(tmp_path / "cowork.json")
    yield host
    host.close()
    assert not (tmp_path / "cowork.json").exists()


def request(host, path, data=None, *, model=False, headers=None):
    auth = host.model_key if model else host.service._ui_key
    actual = {"Authorization": "Bearer " + auth, "Origin": host.origin,
              "Content-Type": "application/json"}
    actual.update(headers or {})
    req = urllib.request.Request(host.origin + path,
            data=json.dumps(data).encode() if data is not None else None, headers=actual)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


@pytest.mark.parametrize("headers", [{"Origin": "https://attacker.example"},
                                     {"Host": "attacker.example"},
                                     {"Authorization": "Bearer wrong"},
                                     {"Sec-Fetch-Site": "cross-site"}])
def test_cross_origin_host_and_bad_capability_blocked(host, headers):
    with pytest.raises(urllib.error.HTTPError) as error:
        request(host, "/ui/pause", {}, headers=headers)
    assert error.value.code == 403
    assert host.service.control.mode.value == "companion"


def test_model_channel_cannot_approve_even_with_valid_model_key(host):
    for path in ("/ui/decide", "/ui/stop", "/ui/configure"):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(host, path, {}, model=True)
        assert error.value.code == 403
    descriptor = json.loads(host.descriptor_path.read_text())
    assert "ui_key" not in descriptor
    assert host.service._ui_key not in json.dumps(descriptor)


def test_actual_http_turn_observation_reply_and_approval(host, monkeypatch):
    from open_compute import mcp_server
    monkeypatch.setenv("OC_COWORK_RUNTIME", str(host.descriptor_path))
    request(host, "/ui/state")
    request(host, "/ui/configure", {"goal": "Helfen", "constraints": "", "target_token": host.service.backend.window["window_token"]})
    request(host, "/ui/message", {"text": "Bitte hallo schreiben", "message_id": "browser-1"})
    turn = mcp_server.cowork_next("Test host-model / no provider call")
    obs = mcp_server.cowork_observe()
    result = mcp_server.cowork_reply(turn["context"]["turn_id"], turn["claim"], "Vorschlag aus MCP",
                {"action": {"type": "type", "text": "Hallo"}, "observation_id": obs["observation_id"]})
    assert result["status"] == "offered"
    assert not host.service.backend.calls
    ui = request(host, "/ui/state")
    offer = ui["offers"][-1]
    assert "Vorschlag aus MCP" in json.dumps(ui, ensure_ascii=False)
    model = mcp_server.cowork_status()
    assert "messages" not in model and "approval_nonce" not in json.dumps(model)
    result = request(host, "/ui/decide", {"offer_id": offer["id"], "accept": True,
               "nonce": offer["approval_nonce"], "revision": offer["revision"], "argument_digest": offer["digest"]})
    assert result["status"] == "uncertain" and len(host.service.backend.calls) == 1


def test_same_origin_required_for_ui_mutation_and_json_only(host):
    req = urllib.request.Request(host.origin + "/ui/pause", data=b"{}",
                headers={"Authorization": "Bearer " + host.service._ui_key,
                         "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(req)
    assert error.value.code == 403
    with pytest.raises(urllib.error.HTTPError):
        request(host, "/ui/pause", {}, headers={"Content-Type": "text/plain"})


def test_assets_csp_no_tokens_and_no_external_scripts(host):
    with urllib.request.urlopen(host.origin) as response:
        body = response.read().decode("utf-8")
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-store"
    assert "Übernehmen" in body and "ä" in body
    assert host.service._ui_key not in body and host.model_key not in body
    assert "https://" not in body


def test_exclusive_descriptor_and_only_safe_model_operations(host):
    other = CoworkHost(CoworkService(FakeDesktop(), _grace_seconds=0))
    try:
        with pytest.raises(FileExistsError):
            other.start(host.descriptor_path)
    finally:
        other.server.server_close()
    with pytest.raises(ValueError):
        model_request("decide", {}, descriptor=host.descriptor_path)
    with pytest.raises(PermissionError):
        model_request("reply", {"turn_id": "x", "claim": "x", "text": "bad"}, descriptor=host.descriptor_path)
