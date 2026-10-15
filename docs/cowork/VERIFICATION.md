# Verifikation des Cowork-Entwicklungsstands

Stand: 15.10.2026. Basis-Commit: `762cb15042f144d31e99e5834f9508dc9769dd66`.

Der eigenständige Python-Host, die gebündelte Oberfläche und der getrennte MCP-/CLI-Modellkanal bilden einen vertikalen Schnitt. Cowork Protocol und der npm-Wrapper wurden nicht verändert. Es gibt keine neue Pflichtabhängigkeit für den Python-Kern und keine Node-Laufzeit für die Oberfläche. Diese Arbeit ist ein Entwicklungsstand ohne PyPI-/npm-Release. Eine Aktualisierung bestehender MCP-Installationen erfolgt separat als lokale Anwendung des geprüften Artefakts und wird im jeweiligen lokalen Abnahmebericht ausgewiesen.

## Aktuelle Erweiterung: vier Rollen und laufender Codex-Worker

Die nachfolgenden ursprünglichen Befunde betreffen den ersten vertikalen Schnitt. Die Erweiterung ergänzt vier Arbeitsrollen, ephemere begrenzte TaskGrants, laufenden Codex-Reasoning-Worker, frische Beobachtung vor jedem Folgeschritt, getrennte Dispatch-/Erfolgsquittungen, private Wiederöffnung und höchstens fünf persistierte Berichte ohne wiederherstellbare Autorität.

Aktuelle unabhängige Gesamtsuite: **910 bestanden, drei Auslassungen, Exit 0**. Eigene gezielte Task-/Worker-Prüfung nach dem Dispatchkontext-Fix: **20 bestanden**, einschließlich echter begrenzter Kindprozesse, Prozessbaum-Abbruch, unerwarteter Tool-Events, Multi-Step-Kontextvertrag, read_only/OC_DENY, Rollenwechsel und Wiederöffnung ohne Replay. Eigene Edge-/HTTP-/Desktop-Prüfung: **19 bestanden**, einschließlich opt-in echtem Browser und Fake-Desktop.

Die eigene abschließende Gesamtsuite nach Dispatchkontext-/Cleanup-Fix bestätigt ebenfalls **910 bestanden, drei Auslassungen, Exit 0**. Repository-Ruff, JavaScript-Syntaxprüfung und `git diff --check` sind sauber. Das neue Wheel wurde getrennt gebaut, in einer isolierten Umgebung installiert und außerhalb des Quellbaums geprüft: site-packages-Import, drei UI-Assets, echter CLI-/HTTP-Start mit `--worker none`, ein deterministischer Modell-Turn, Stopp und Entfernen eigener Runtime-Dateien bestanden. Dieser Packaging-Smoke verwendet keine Provideraufrufe oder Desktop-Eingaben und verändert keine bestehende Nutzerruntime.

Eine anschließende unabhängige HTTP-Prüfung fand einen sporadischen Windows-Socketabbruch statt des erwarteten 403 bei ungültigem Host. Der Server liest deshalb POST-Bodies vor der Autoritätsablehnung ausschließlich begrenzt (32 KiB, zwei Sekunden), führt weiterhin nichts vor der Autoritätsprüfung aus und schließt POST-Verbindungen ausdrücklich. Der Regressionstest prüft jede abgewiesene Origin-/Host-/Capability-/Fetch-Site-Variante achtmal einschließlich vollständiger JSON-Fehlerrückgabe.

Unabhängige echte Codex-Abnahme mit bestehender Anmeldung und deterministischem Editor: **Ctrl+A → Texteingabe „Hallo Welt!“ → frische Beobachtung → done**, zwei Schritte, drei echte Reasoning-Turns, exakter Editor-Readback, rund 49 Sekunden ohne UI-Heartbeat im Soloauftrag und authentifizierte Wiederöffnung; Harness Exit 0. Gemeldete Usage: 31.589 Eingabe-/181 Ausgabetokens über drei Turns. Das ist kein allgemeiner Benchmark. `semantic_verified` blieb korrekt false. Dieser Editor simuliert Anwendungseffekte; er beweist keine neuen nativen Windows-Eingaben. Native Mehrschrittproben wurden durch erkannte physische Nutzereingaben vor Dispatch abgebrochen und sind noch nicht abgenommen.

Konkrete aktuelle Produktgrenze: Modellaufträge benötigen vollständig lesbaren ausgewählten Editorinhalt bis 350 Zeichen; reine Navigation ohne lesbaren Editor stoppt vorsorglich. Reale Audiogeräte bleiben ungeprüft. Die UI meldet die tatsächliche Adapter-/Modellkonfiguration (Standard `gpt-6-astra`, überschreibbar); echte Usage kommt aus `turn.completed`, nicht aus Zeichenbudgets. Kein neuer PyPI-/npm-Release.

## Historische automatisierte Prüfungen des ersten Schnitts

- Ausgangsbasis: 839 bestandene Tests, zwei Auslassungen, vom unabhängigen Reviewer auf der Remote-Basis gemessen.
- Abschließende Gesamtsuite: `python -X utf8 -m pytest -q -p no:cacheprovider` — **885 bestanden, drei begründete Auslassungen, Exit 0**, im unabhängigen Review auf dem finalen Produktstand geprüft. Der zusätzliche Grenztest prüft drei realistische native Typing-Quittungen unter maximalen Ziel-/Nachrichten-/Einschränkungsbudgets. Ausgelassen: optionales externes clirec-Extra nicht installiert, nicht anwendbarer SDK-ImportError-Pfad und der gesondert erfolgreich ausgeführte opt-in Browser-Smoke.
- Die Service-Prüfungen decken einmalige Freigaben, manipulierte Nonces/Digests/Revisionen, Ziel-/Beobachtungsänderungen, Ablauf, read_only für alle synthetischen Eingaben, Moduswechsel, Stopps während Dispatch, Cleanup-Fehler und Unicode ab. Die kompakte Modellprojektion enthält keine wiederholten nativen Receipt-/Fensterbäume; neueste Nachricht, Ziel und aktuelle Einschränkungen bleiben geschützt.
- HTTP-/MCP-Prüfung führt den tatsächlichen lokalen Transport bis zu den registrierten MCP-Funktionen durch. Modell-Capability auf UI-Endpunkten, fremde Origins/Hosts, falsche Capabilities, falsches JSON und wiederholte Freigaben werden blockiert.
- Die Transportprüfung besitzt getrennte HTTP- und MCP-Testfälle. Der vollständige HTTP-Turn einschließlich Beobachtung, Angebot und Annahme läuft auch bei einer Installation nur mit `[dev]`. Ausschließlich der zusätzliche MCP-Wrapper-Fall wird ohne das optionale MCP-Extra übersprungen; HTTP-Kernabdeckung wird nicht ausgelassen.
- Desktop-Grenztests verwenden injizierte Betriebssystemgrenzen: Fokus/Abbruch vor jedem Textsegment, Teilübertragung mit Zeichenanzahl, erneute Inhaltsprüfung nach Zielaktivierung und Vordergrund-Readback trotz irreführendem Win32-Returncode. Temporäre Thread-Anbindungen werden in jedem Pfad gelöst.
- `OC_BROWSER_SMOKE=1 python -X utf8 -m pytest -q -p no:cacheprovider tests/test_cowork_browser.py`: echter Edge-Browser mit echtem HTTP-Host und deterministischem Host-Modell-/Fake-Desktop. Geprüft: Moduswahl, bearbeitbares simuliertes Sprachtranskript ohne automatisches Senden, Mikrofon-/Stoppzustand, Text, Vorschlag, Ablehnung, einmalige Annahme, Pause/Fortsetzen, Wiederanbindung, Tastaturbedienung, 390-Pixel-Fenster, Unicode-Codepoints und keine Skriptausführung aus Modelltext. Dieser Test erzeugt keine realen Desktop-Eingaben und prüft keine Audiogeräte.
- `python -m ruff check` auf den neuen Python-Modulen/Tests, `node --check open_compute/cowork_ui/app.js` und `git diff --check` wurden ausgeführt. Node wird ausschließlich als Entwicklungsprüfer verwendet.
- Wheel über `python -m pip wheel . --no-deps --no-build-isolation` gebaut. In einer getrennten virtuellen Umgebung aus dem Wheel installiert; `docs/cowork/check_installed.py` wird außerhalb des Quellbaums mit deren Python ausgeführt. Dieser Smoke prüft importierte site-packages, alle drei UI-Dateien, echten CLI-/HTTP-Start, Besprechen ohne Desktop-Ziel, Text-Turn/Antwort über den Modellkanal, Stopp und Entfernen der eigenen Runtime-Datei. Keine Modell-SDKs oder Desktop-Extras werden hierfür installiert.

## Unabhängige empirische Abnahme

Ein separater Reviewer prüfte die Oberfläche in einem eigenen Edge-Harness einschließlich Modusgrenzen, Sprachentwurf, Modelltransport, Nicht-Ausführung vor Annahme, Wiederanbindung, schmalem Layout und Sicherheitsgrenzen.

Zusätzlich wurde ein eigenes, isoliertes Tk-Eingabefenster mit sichtbarem Edge, echtem HTTP-Host, echtem DesktopBackend und Modelltransport geprüft. Vor der Annahme war der Inhalt leer. Nach dem sichtbaren Annahmeklick enthielt das Ziel exakt **„Cowork geprüft – äöü“**: 20 von 20 Zeichen, drei Segmente. Ein separater Datei-Readback bestätigte das Ergebnis; gehaltene Tasten und Mausbuttons waren leer. Der Backend-Receipt blieb korrekt **uncertain**, da dieser Receipt selbst keinen semantischen Anwendungserfolg beweist. Der Harness endete mit Exit 0 und räumte seine eigenen Fenster/Prozesse auf.

Frühere Proben blockierten eine fehlgeschlagene Aktivierung, geänderte Zielzustände und erkannte physische Nutzereingaben. Die Schutzprüfungen wurden dafür nicht abgeschwächt. Die Aktivierung verwendet jetzt die genaue Fensteridentität, temporäre Thread-Anbindung mit sicherem Ablösen und tatsächlichen Vordergrund-Readback; Beobachtung und Inhaltsprüfung erfolgen weiterhin erneut nach Aktivierung.

## Verbleibende Grenzen

- Echtes Mikrofon, Lautsprecher und Sprachqualität wurden nicht empirisch abgenommen. Browser-Spracherkennung kann externe Dienste verwenden und ist optional.
- Echte Codex-Reasoning-Aufrufe sind für die Erweiterung oben separat belegt; die frühere native Einzelannahme verwendete deterministische Modellantworten.
- Keine behauptete prozentuale Tokenersparnis. Provider-Usage und lokale Zeichen-/Byte-/Bild-/Turnmetriken werden getrennt ausgewiesen.
- Desktop-Eingaben und UIA benötigen Windows und optionale Extras. Der Text-/HTTP-Kern funktioniert ohne diese Desktop-Funktionen.
- Der lokale Capability-Schutz trennt Modell und menschliche UI. Er ist keine Isolation gegenüber bösartigem Code mit vollständigem Zugriff auf dasselbe Betriebssystemkonto.
