# Eigenständiger Cowork-Modus

Cowork ist eine optionale lokale Oberfläche von open-compute. Es gibt keine Laufzeitabhängigkeit zu Cowork Protocol, keinen Node-Dienst und keinen erforderlichen Modell-API-Schlüssel. Der Python-Dienst besitzt die Sitzung; CLI, MCP und Oberfläche greifen auf dieselbe Instanz zu.

## Start

```console
pip install "open-compute[local,uia,compose,mcp]"
oc cowork
```

Für diesen noch unveröffentlichten Entwicklungsstand das gebaute Wheel installieren oder im isolierten Arbeitsbereich `python -m open_compute.cli cowork` verwenden. Desktop-Beobachtung und Eingaben benötigen Windows sowie die genannten optionalen Extras; der Text-/HTTP-Kern verwendet nur die Standardbibliothek.

Der Starter öffnet nach Möglichkeit ein eigenes Edge-/Chrome-App-Fenster, ansonsten ein Browserfenster. `oc cowork --no-open` gibt die lokale Zugangsadresse aus. Diese enthält einen geheimen UI-Zugang im Fragment: nicht weitergeben. Sie gehört ausschließlich dem Menschen. Ein laufender Host wird durch eine exklusiv angelegte lokale Sitzungsdatei geschützt. Standard: `~/.open-compute/cowork.json`; abweichend über `OC_COWORK_RUNTIME` oder `--runtime`. Nach einem Prozessabsturz eine verbleibende Datei erst nach Prüfung des darin genannten Prozesses entfernen. Der Host überschreibt keine fremde Sitzung.

## Arbeitsmodus wählen

Der sichere Erststart ist **Besprechen**: Text und optionale Sprachtranskription, ohne Windows-Ziel, Fensterliste, Desktop-Beobachtung oder Aktionsangebote. Dieser Weg funktioniert auch auf anderen Betriebssystemen. **Begleiten** erlaubt gefilterte Beobachtung und Vorschläge, führt aber auch bei einem manipulierten Annahmeaufruf keine Aktion aus. **Mit Bestätigung ausführen** erlaubt ausschließlich die einzeln sichtbare und angenommene Aktion. Keiner der Modi ist eine unbestätigte Vollautomatik.

Die Auswahl steht im Fenster über dem Gespräch. Alternativ beim Start `oc cowork --work-mode discuss|observe|confirm`. Der Modus steht im UI-Zustand, im kompakten MCP-Status und im `cowork_next`-Kontext. Ein menschlicher Wechsel widerruft offene Angebote und Modell-Turns; laufende Ausführung wird abgebrochen und pausiert. Der Modellkanal kann den Modus nicht ändern. Beim Wechsel zurück nach Besprechen wird das Desktop-Ziel entfernt. Begleiten/Ausführen benötigen erst dann ein ausgewähltes Ziel, wenn beobachtet oder ein Angebot erstellt werden soll.

## Mit einem Host-Modell arbeiten

Ein MCP-Client mit der hier enthaltenen Version von `open_compute.mcp_server` kann als schlüsselloses Host-Modell dienen. Der npm-Wrapper muss keine zweite Serverlogik bekommen. Bei bereits laufenden MCP-Servern die Paketversion aktualisieren und den Server neu starten. Die Sitzung bleibt beim separat gestarteten `oc cowork`.

1. Im Fenster den Arbeitsmodus auswählen, ein Ziel und aktuelle Einschränkungen eintragen und den Auftrag übernehmen. Für Begleiten/Ausführen ein konkretes Zielfenster wählen. Besprechen funktioniert ohne Desktop-Ziel.
2. Eine Nachricht senden. Sie wartet auf genau einen Modell-Turn; es wird kein Anbieter im Hintergrund gestartet.
3. Das Host-Modell ruft `cowork_next(identity="tatsächlicher Host / tatsächliches Modell")` auf. `idle` bedeutet: keine neue Inferenz nötig. Der angezeigte Modellname ist eine Selbstauskunft des Hosts, kein unabhängig nachgewiesener Anbietername.
4. Bei Bedarf `cowork_observe()` für gefilterte Semantik aufrufen. Höchstens eine Modellbeobachtung je beanspruchtem Turn; die Oberfläche kann lokal separat beobachten. Für eine begründete visuelle Eskalation steht anschließend einmal `cowork_lens(reason="…")` zur Verfügung. Das Ziel muss für die Linse bereits im Vordergrund sein.
5. Mit `cowork_reply(turn_id, claim, text, proposal=None)` antworten. Ein optionales Angebot hat genau diese Form:

```json
{
  "action": {"type": "type", "text": "Der tatsächlich einzugebende Text"},
  "observation_id": "die zurückgegebene Beobachtungs-ID"
}
```

Für Textantworten ist kein Desktop-Ziel erforderlich. Vorschläge erfordern eine aktuelle Beobachtung des menschlich gewählten Ziels. Unterstützt werden einzelne Aktionen `type`, `key`, `left_click`, `right_click`, `mouse_move`, `scroll`. Keine Halteaktionen, Makros oder Programmstarts. Koordinaten liegen im normalisierten virtuellen Desktop-Rahmen der Beobachtung; der lokale Bildausschnitt ist kein neuer Koordinatenrahmen.

Die Modellseite kann keine Angebote annehmen. Erst der Mensch sieht tatsächlichen Text/Tasten/Ziel und nimmt einmal an oder lehnt ab. Die Annahme umfasst ausdrücklich die Aktivierung des exakt gewählten Fensters. Es folgt ein sichtbares viersekündiges Abbruchfenster, eine erneute Ziel-/Beobachtungs-/Rahmenprüfung nach Aktivierung und die begrenzte Ausführung. Acht-Zeichen-Segmente prüfen bei Texteingabe jeweils Abbruch und Fokus. Physische Eingaben während der Ausführung setzen einen Abbruch; Hooks werden danach entfernt.

Ein Dispatch wird als **ungewiss / Ergebnis prüfen** dargestellt. Weder SendInput noch ein Screenshotvergleich beweisen den semantischen Erfolg. Teilübertragungen enthalten die gesendete Zeichenanzahl; ungewisse Aktionen werden niemals automatisch wiederholt.

CLI für denselben Modellkanal:

```console
oc cowork state
oc cowork next --identity "Mein Host / Modell"
oc cowork observe
oc cowork reply --data "{...Antwortargumente...}"
```

## Sicherheit und Lebenszyklus

- `OC_SAFETY_MODE` bleibt die Obergrenze. `read_only` blockiert hier jede synthetische Eingabe einschließlich Mausbewegung/Scrollen und Zielaktivierung. `confirm` verwendet ausschließlich den privaten, auf die konkrete menschliche Annahme beschränkten Callback. Es wird kein globales `allow_all` gesetzt.
- `OC_DENY` bleibt bindend. Eine gesperrte Zielaktivierung blockiert auch nachfolgende Eingaben.
- UI und Modell haben getrennte Bearer-Capabilities. Die lokale Sitzungsdatei enthält ausschließlich den Modellzugang. Der Modellstatus enthält weder UI-Token noch Annahme-Nonce noch die lokale Gesprächshistorie.
- Der Host bindet ausschließlich `127.0.0.1`. Host-, Origin-, Fetch-Site- und JSON-Prüfung sowie eine restriktive CSP schützen den Browserkanal. Annahmen binden Angebots-ID, Revision, Argumentdigest, Ziel und Beobachtung an eine einmalige Nonce. Kein Schutz gegen bösartigen Code mit vollständigem Zugriff auf dasselbe Betriebssystemkonto wird behauptet.
- Neue menschliche Nachrichten, neue Beobachtungen bei offenen Angeboten, Ziel-/Einschränkungsänderung, Übernahme und Stopp widerrufen alte Freigaben. Eine unterbrochene UI-Verbindung pausiert nach acht Sekunden. Wiederanbindung spielt keine Aktion erneut ab; bewusst fortsetzen und gegebenenfalls neu beobachten/fragen.
- Stopp beendet die Sitzung. Der CLI-Host schließt daraufhin den Server und entfernt seine eigene Sitzungsdatei; Ctrl+C räumt ebenfalls auf. Die Oberfläche stoppt ihre Sprachaufnahme/-ausgabe beim Verlassen. Ein vom Browser verwaltetes App-Fenster ist kein eigener Modellprozess.
- Kein Roh-Desktop oder vollständiger UIA-Baum geht ans Modell. Semantik: maximal zwölf Elemente / 1.200 Zeichen; die optionale Linse maximal 400 × 400 Pixel. Bekannte Agenten-/Passwortmanagerfenster werden als Ziel ausgeschlossen. Passwortwerte werden nicht gelesen. Die lokale Linse wartet nach einem Klick drei Sekunden zum manuellen Wechsel ins Ziel, aktiviert es aber nicht selbst. Beobachtungsansichten sind keine Sicherheits- oder Erfolgsbeweise.

## Sprache und Budgets

Text bleibt der vollständige Standardweg. Die sichtbare **🎙 Mikrofon**-Taste startet ausschließlich nach einem Klick; während der Aufnahme zeigt sie **■ Aufnahme stoppen**, einen gedrückten Zustand und einen Aufnahmehinweis. Die Erkennung schreibt zunächst einen bearbeitbaren Entwurf und sendet nie automatisch. Browserunterstützung und Mikrofonberechtigung variieren; Browsererkennung kann einen externen Dienst verwenden. Es wird keine Offline-Eigenschaft behauptet. Vorlesen ist abschaltbar, unterbrechbar und verwendet eine Sprecherinstanz. Audio ersetzt keine Annahme.

Alle Zeichenbudgets zählen Python-Unicode-Codepoints, nicht UTF-16-Code-Units oder Tokens. Jüngste Gesprächszüge werden begrenzt; Ziel und aktuelle Einschränkungen bleiben als strukturierte Felder erhalten. Fortschritt und Modellstatus sind latest-only, die lokale Kontrollhistorie hält höchstens 100 Ereignisse/Nachrichten und zwölf Angebote im Arbeitsspeicher. Es gibt keine unbegrenzte Rohhistorie auf dem Datenträger. `--max-turns` begrenzt ausgelieferte Modell-Turns (Standard 100); Antworten maximal 1.200 Zeichen, Kontext maximal 10.000 Zeichen. Diese Metrik zählt ausgelieferte Turns und beweist keine Provider-Inferenz. Bytes/Bilder werden separat gezählt; echte Provider-Usage steht im schlüssellosen Host-Weg nicht zur Verfügung. Stille, lokale Bilder und UI-Polling erzeugen keinen Modell-Turn. Es wird keine prozentuale Tokenersparnis behauptet.

## Verifikation

`tests/test_cowork*.py` prüfen Service, HTTP-/MCP-Grenze, Manipulationen, Wiederverwendung, veraltete Beobachtungen, read_only, Teilübertragung und Wiederanbindung. Der Browser-Smoke ist mit `OC_BROWSER_SMOKE=1` ausdrücklich opt-in und verwendet einen Fake-Desktop. Er prüft einen echten Browser mit echter HTTP-/Modelltransportstrecke, keine reale Maus-/Tastaturwirkung oder Mikrofon-/Lautsprechergeräte. Reale Desktop- und Audioabnahmen sind getrennt auszuweisen.

Der verbindliche Auftrag und die ursprünglichen Befunde stehen in [PLAN-2026-10-15.md](PLAN-2026-10-15.md).
