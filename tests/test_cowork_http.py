import json
import urllib.error
import urllib.request

import pytest

from open_compute.cowork import CoworkService
from open_compute.cowork_http import CoworkHost, model_request
from open_compute.cowork_http import existing_ui_url
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
    for _ in range(8):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(host, "/ui/pause", {}, headers=headers)
        try:
            assert error.value.code == 403
            assert error.value.headers["Connection"] == "close"
            assert json.load(error.value)["error"]
        finally:
            error.value.close()
    assert host.service.control.mode.value == "companion"


def test_model_channel_cannot_approve_even_with_valid_model_key(host):
    for path in ("/ui/decide", "/ui/stop", "/ui/configure"):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(host, path, {}, model=True)
        assert error.value.code == 403
    descriptor = json.loads(host.descriptor_path.read_text())
    assert "ui_key" not in descriptor
    assert host.service._ui_key not in json.dumps(descriptor)


@pytest.mark.parametrize("channel", ["http", "mcp"])
def test_actual_turn_observation_reply_and_approval(host, monkeypatch, channel):
    if channel == "mcp":
        pytest.importorskip("mcp", reason="optional MCP wrapper coverage; HTTP case always runs")
        from open_compute import mcp_server
        monkeypatch.setenv("OC_COWORK_RUNTIME", str(host.descriptor_path))

    def model(operation, data=None):
        if channel == "http":
            return model_request(operation, data, descriptor=host.descriptor_path)
        tool = {"next": mcp_server.cowork_next, "observe": mcp_server.cowork_observe,
                "reply": mcp_server.cowork_reply, "state": mcp_server.cowork_status}[operation]
        return tool(**(data or {}))

    request(host, "/ui/state")
    request(host, "/ui/configure", {"goal": "Helfen", "constraints": "", "target_token": host.service.backend.window["window_token"]})
    request(host, "/ui/message", {"text": "Bitte hallo schreiben", "message_id": "browser-1"})
    turn = model("next", {"identity": "Test host-model / no provider call"})
    obs = model("observe")
    result = model("reply", {"turn_id": turn["context"]["turn_id"], "claim": turn["claim"],
                "text": "Vorschlag aus Modellkanal", "proposal": {
                    "action": {"type": "type", "text": "Hallo"}, "observation_id": obs["observation_id"]}})
    assert result["status"] == "offered"
    assert not host.service.backend.calls
    ui = request(host, "/ui/state")
    offer = ui["offers"][-1]
    assert "Vorschlag aus Modellkanal" in json.dumps(ui, ensure_ascii=False)
    status = model("state")
    assert "messages" not in status and "approval_nonce" not in json.dumps(status)
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


def test_reopen_authenticates_same_live_session_without_inference(host):
    before = host.service.metrics["delivered_turns"]
    assert existing_ui_url(host.descriptor_path) == host.ui_url
    assert host.service.metrics["delivered_turns"] == before
    assert "ui_key" not in json.loads(host.descriptor_path.read_text())
    private = host.ui_descriptor_path
    data = json.loads(private.read_text())
    data["pid"] += 1
    private.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PermissionError):
        existing_ui_url(host.descriptor_path)
