# Verifikation des Cowork-Entwicklungsstands

Stand: 15.10.2026. Basis-Commit: `762cb15042f144d31e99e5834f9508dc9769dd66`.

Der eigenständige Python-Host, die gebündelte Oberfläche und der getrennte MCP-/CLI-Modellkanal bilden einen vertikalen Schnitt. Cowork Protocol und der npm-Wrapper wurden nicht verändert. Es gibt keine neue Pflichtabhängigkeit für den Python-Kern und keine Node-Laufzeit für die Oberfläche. Diese Arbeit ist ein Entwicklungsstand ohne PyPI-/npm-Release. Eine Aktualisierung bestehender MCP-Installationen erfolgt separat als lokale Anwendung des geprüften Artefakts und wird im jeweiligen lokalen Abnahmebericht ausgewiesen.

## Automatisierte Prüfungen

- Ausgangsbasis: 839 bestandene Tests, zwei Auslassungen, vom unabhängigen Reviewer auf der Remote-Basis gemessen.
- Abschließende Gesamtsuite: `python -X utf8 -m pytest -q -p no:cacheprovider` — **885 bestanden, drei begründete Auslassungen, Exit 0**, im unabhängigen Review auf dem finalen Produktstand geprüft. Der zusätzliche Grenztest prüft drei realistische native Typing-Quittungen unter maximalen Ziel-/Nachrichten-/Einschränkungsbudgets. Ausgelassen: optionales externes clirec-Extra nicht installiert, nicht anwendbarer SDK-ImportError-Pfad und der gesondert erfolgreich ausgeführte opt-in Browser-Smoke.
- Die Service-Prüfungen decken einmalige Freigaben, manipulierte Nonces/Digests/Revisionen, Ziel-/Beobachtungsänderungen, Ablauf, read_only für alle synthetischen Eingaben, Moduswechsel, Stopps während Dispatch, Cleanup-Fehler und Unicode ab. Die kompakte Modellprojektion enthält keine wiederholten nativen Receipt-/Fensterbäume; neueste Nachricht, Ziel und aktuelle Einschränkungen bleiben geschützt.
- HTTP-/MCP-Prüfung führt den tatsächlichen lokalen Transport bis zu den registrierten MCP-Funktionen durch. Modell-Capability auf UI-Endpunkten, fremde Origins/Hosts, falsche Capabilities, falsches JSON und wiederholte Freigaben werden blockiert.
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
- Ein echter Modellanbieter-Livecall wurde nicht gemessen; geprüft wurden der schlüssellose Host-Kanal und deterministische Antworten. Modellnamen sind ausdrücklich Host-Selbstauskünfte.
- Keine behauptete prozentuale Tokenersparnis oder Provider-Usage. Gezählt werden ausgelieferte Turns, Zeichen/Bytes, Bilder und getrennte lokale UI-Updates.
- Desktop-Eingaben und UIA benötigen Windows und optionale Extras. Der Text-/HTTP-Kern funktioniert ohne diese Desktop-Funktionen.
- Der lokale Capability-Schutz trennt Modell und menschliche UI. Er ist keine Isolation gegenüber bösartigem Code mit vollständigem Zugriff auf dasselbe Betriebssystemkonto.
