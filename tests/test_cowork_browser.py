"""Opt-in real browser/HTTP UI acceptance with a fake desktop, never OS inputs.

Run: OC_BROWSER_SMOKE=1 python -m pytest tests/test_cowork_browser.py
"""
import os
from pathlib import Path

import pytest

from open_compute.cowork import CoworkService
from open_compute.cowork_http import CoworkHost, model_request
from test_cowork import FakeDesktop

pytestmark = pytest.mark.skipif(os.environ.get("OC_BROWSER_SMOKE") != "1",
                               reason="opt-in browser contract test; no real desktop input")


def test_browser_chat_model_offers_pause_reconnect_and_small_window(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    backend = FakeDesktop()
    service = CoworkService(backend, mode="confirm", _grace_seconds=0)
    host = CoworkHost(service).start(tmp_path / "runtime.json")
    errors = []
    try:
        with playwright.sync_playwright() as pw:
            options = {"headless": True}
            for root in (os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", "")):
                edge = Path(root) / "Microsoft/Edge/Application/msedge.exe"
                if edge.is_file():
                    options["executable_path"] = str(edge)
                    break
            browser = pw.chromium.launch(**options)
            context = browser.new_context(viewport={"width": 1100, "height": 850})
            page = context.new_page()
            page.add_init_script("""window.SpeechRecognition=class {
              start(){this.onstart();this.onresult({results:[[{transcript:'Audioentwurf äöü'}]]});}
              stop(){this.onend();} abort(){this.onend();}
            };""")
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.goto(host.ui_url)
            page.get_by_text("Kein ausführbarer Worker verbunden.", exact=False).first.wait_for()
            playwright.expect(page.locator("#work-mode")).to_have_value("human_leads")
            page.locator("#work-mode").select_option("human_only")
            playwright.expect(page.locator("#connection")).to_contain_text("Ich arbeite allein")
            assert page.locator("#observe").is_disabled()
            page.locator("#mic").click()
            playwright.expect(page.locator("#mic")).to_have_attribute("aria-pressed", "true")
            playwright.expect(page.locator("#text")).to_have_value("Audioentwurf äöü")
            assert not service.messages  # transcript is an editable draft, never autosent
            page.locator("#mic").click()
            playwright.expect(page.locator("#mic")).to_have_attribute("aria-pressed", "false")
            page.locator("#text").fill("🙂" * 1199)
            assert len(page.locator("#text").input_value()) == 1199
            page.locator("#text").fill("")
            page.locator("#work-mode").select_option("human_leads")
            playwright.expect(page.locator("#connection")).to_contain_text("Ich führe")
            playwright.expect(page.locator("#target option")).to_have_count(2)
            page.locator("#goal").fill("Text prüfen äöü")
            page.locator("#constraints").fill("Nichts löschen")
            page.locator("#target").select_option(backend.window["window_token"])
            page.get_by_role("button", name="Auftrag übernehmen").click()
            page.get_by_text("Gebunden: Test editor", exact=True).wait_for()

            def reply(text, action=None):
                turn = model_request("next", {"identity": "Browser test host / simulated inference"}, descriptor=host.descriptor_path)
                data = {"turn_id": turn["context"]["turn_id"], "claim": turn["claim"], "text": text}
                if action:
                    obs = model_request("observe", descriptor=host.descriptor_path)
                    data["proposal"] = {"action": action, "observation_id": obs["observation_id"]}
                return model_request("reply", data, descriptor=host.descriptor_path)

            page.locator("#text").fill("Bitte einen Vorschlag")
            page.get_by_role("button", name="Senden", exact=True).click()
            page.locator("#messages .message").filter(has_text="Bitte einen Vorschlag").wait_for()
            reply("Vorschlag für dich", {"type": "type", "text": "Hallo äöü"})
            page.get_by_text("Text eingeben: „Hallo äöü“", exact=True).wait_for()
            assert not backend.calls
            page.get_by_role("button", name="Ablehnen", exact=True).click()
            page.get_by_text("Abgelehnt", exact=True).wait_for()
            assert not backend.calls
            page.locator("#text").fill("Neuen Vorschlag bitte")
            page.locator("#text").press("Control+Enter")
            page.locator("#messages .message").filter(has_text="Neuen Vorschlag bitte").wait_for()
            reply("<img src=x onerror=alert(1)>", {"type": "type", "text": "Zweiter Text"})
            page.get_by_text("Text eingeben: „Zweiter Text“", exact=True).wait_for()
            assert page.locator("#messages img").count() == 0
            page.get_by_role("button", name="Annehmen und einmal ausführen", exact=True).click()
            page.get_by_text("Ergebnis prüfen – ungewiss", exact=True).wait_for()
            assert len(backend.calls) == 1
            page.get_by_role("button", name="Übernehmen / Pause").click()
            playwright.expect(page.locator("#connection")).to_contain_text("Pausiert")
            page.get_by_role("button", name="Fortsetzen", exact=True).click()
            playwright.expect(page.locator("#connection")).to_contain_text("Bereit")
            before = service.metrics["delivered_turns"]
            page.reload()
            page.get_by_text("Ergebnis prüfen – ungewiss", exact=True).wait_for()
            assert len(backend.calls) == 1 and service.metrics["delivered_turns"] == before
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("#text").focus()
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.id") == "send"
            artifact = Path(os.environ.get("OC_COWORK_ARTIFACT_DIR", str(tmp_path)))
            artifact.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(artifact / "cowork-mobile.png"), full_page=True)
            # Dropped connection means paused authority, not replay on reconnect.
            context.set_offline(True)
            page.wait_for_timeout(9500)
            assert service.control.mode.value == "paused"
            context.set_offline(False)
            playwright.expect(page.locator("#connection")).to_contain_text("Pausiert", timeout=10000)
            assert len(backend.calls) == 1
            assert not errors
            browser.close()
    finally:
        host.close()
