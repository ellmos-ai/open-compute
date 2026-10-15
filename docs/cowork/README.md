# Eigenständiger Cowork-Modus

Cowork ist eine optionale lokale Oberfläche von open-compute. Es gibt keine Laufzeitabhängigkeit zu Cowork Protocol, keinen Node-Dienst und keinen erforderlichen Modell-API-Schlüssel. Der Python-Dienst besitzt die Sitzung; CLI, MCP und Oberfläche greifen auf dieselbe Instanz zu.

## Start

```console
pip install "open-compute[local,uia,compose,mcp]"
oc cowork
```

Für diesen noch unveröffentlichten Entwicklungsstand das gebaute Wheel installieren oder im isolierten Arbeitsbereich `python -m open_compute.cli cowork` verwenden. Desktop-Beobachtung und Eingaben benötigen Windows sowie die genannten optionalen Extras; der Text-/HTTP-Kern verwendet nur die Standardbibliothek.

Der Starter öffnet nach Möglichkeit ein eigenes Edge-/Chrome-App-Fenster, ansonsten ein Browserfenster. `oc cowork --no-open` gibt die lokale Zugangsadresse aus. Diese enthält einen geheimen UI-Zugang im Fragment: nicht weitergeben. Sie gehört ausschließlich dem Menschen. Ein laufender Host wird durch eine exklusiv angelegte lokale Sitzungsdatei geschützt. Standard: `~/.open-compute/cowork.json`; abweichend über `OC_COWORK_RUNTIME` oder `--runtime`. Nach einem Prozessabsturz eine verbleibende Datei erst nach Prüfung des darin genannten Prozesses entfernen. Der Host überschreibt keine fremde Sitzung.

## Vier Arbeitsrollen und integrierter Worker

| Rolle | Verhalten |
| --- | --- |
| **Ich arbeite allein** (`human_only`) | Modell und Beobachtung aus; Nachrichten bleiben lokale Notizen. Kein Ziel nötig. |
| **Ich führe – Modell begleitet und schlägt vor** (`human_leads`, Standard) | Nach ausdrücklichem Auftrag kommentiert das Modell Nachrichten und Änderungen; einzelne sichtbare Vorschläge lassen sich annehmen. Textdialog geht ohne Ziel. |
| **Modell führt – ich schaue zu** (`model_leads`) | Begrenzter menschlich freigegebener Auftrag im ausgewählten Fenster, UI-Anwesenheit erforderlich. |
| **Modell arbeitet allein – ich komme später zurück** (`model_alone`) | Derselbe begrenzte Auftrag ohne UI-Anwesenheit; Abschlussbericht beim Wiederöffnen. |

CLI: `--work-mode human_only|human_leads|model_leads|model_alone`. Die alten Namen `discuss`, `observe`, `confirm` sind Kompatibilitätsaliase für `human_leads`. Öffnen allein startet keine Inferenz. Rollenwechsel, Übernahme und Stopp widerrufen Freigaben und Workerarbeit; kein Modell kann sich selbst Autorität geben.

Standard ist ein tatsächlich laufender **Codex-Worker mit `gpt-6-astra`**. Modellwahl: `--worker-model MODELL` oder `OC_COWORK_WORKER_MODEL`. Die UI zeigt tatsächlichen Adapter und Modellkonfiguration. Codex verwendet seine bestehende Anmeldung, ohne API-Schlüssel/Authkopie oder Änderung persönlicher Konfiguration. Die lokale Codex-CLI einschließlich ihrer Laufzeit muss verfügbar sein. Der kurzlebige Reasoning-Prozess läuft im eigenen temporären Verzeichnis, ignoriert Benutzerkonfiguration/Projektregeln und deaktiviert Tools, MCPs, Apps und Plugins. Strukturierte Vorschläge besitzen keine eigene Desktop-Autorität. Prozessgruppe, Ausgabe und Deadline sind begrenzt; Stopp beendet eigene Kindprozesse.

Für Modellrollen Ziel und Einschränkungen sowie Aktionsklassen, Zeit-/Schritt-/Turnbudget bewusst wählen und **Auftrag freigeben**. Dies erlaubt mehrere Schritte ausschließlich am exakten Ziel, einschließlich Aktivierung nach vier Sekunden Abbruchzeit. Für Alleinarbeit **Freigeben und Fenster schließen** nutzen: Die UI schließt vor dem Eingabemonitor; falls der Browser dies verweigert, innerhalb der Vorlaufzeit manuell schließen. Physische Eingabe während aktiver Arbeit bedeutet Übernahme. Systemwechsel wie Win+R/Alt+Tab sind ausgeschlossen. `read_only` und `OC_DENY` bleiben Obergrenze.

Vor jedem Folgeschritt wird frisch beobachtet. Vollständiger `dispatch=executed` erlaubt einen anderen nächsten Schritt, beweist jedoch keinen Anwendungserfolg. Unklarer/partieller Dispatch, Fehler, gleiche Aktion bei unveränderter Beobachtung oder fehlender Fortschritt führen ohne Replay zu Prüfbedarf. Abschluss bedeutet **„Modell meldet Auftrag erledigt – Ergebnis prüfen“**, `completion_source=model`, `semantic_verified=false`.

Beobachtung: höchstens zwölf Elemente / 1.200 Unicode-Zeichen, Editorinhalt **350 Zeichen**. Passwortwerte werden vor Zugriff ausgeschlossen. Unlesbarer/abgeschnittener Inhalt beendet Modellaufträge mit Prüfbedarf. Aktuelle konkrete Grenze: autonome Aufträge benötigen einen vollständig lesbaren ausgewählten Editor; reine Klick-/Navigationsaufträge ohne lesbaren Editor werden vorsorglich ebenfalls gestoppt. Es wird kein allgemeines selbständiges Desktoparbeiten behauptet.

`oc cowork` öffnet eine laufende Sitzung nach Abgleich von privater UI-Datei, Session/PID und authentifiziertem Host erneut, ohne zweite Autorität. Höchstens fünf begrenzte Abschlussberichte persistieren neben der Runtime in `.reports.json`. Nach Hostneustart erscheinen Berichte, niemals wiederbelebte Grants. Fortsetzen spielt keine Aktion erneut ab.

## Mit einem Host-Modell arbeiten

Mit `oc cowork --worker none` kann alternativ ein MCP-Client den menschlich geführten Dialog bedienen. Der integrierte Worker beansprucht Turns exklusiv; paralleles manuelles `cowork_next` ist dann blockiert. Modellrollen erfordern einen laufenden Worker. Der npm-Wrapper braucht keine zweite Serverlogik.

1. Im Fenster `human_leads` wählen, Ziel und Einschränkungen eintragen. Textdialog funktioniert ohne Desktop-Ziel.
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

- `OC_SAFETY_MODE` bleibt die Obergrenze. `read_only` blockiert hier jede synthetische Eingabe einschließlich Mausbewegung/Scrollen und Zielaktivierung. `confirm` verwendet ausschließlich private Callbacks, gebunden an die konkrete Einzelannahme oder den begrenzten menschlichen TaskGrant. Es wird kein globales `allow_all` gesetzt.
- `OC_DENY` bleibt bindend. Eine gesperrte Zielaktivierung blockiert auch nachfolgende Eingaben.
- UI und Modell haben getrennte Bearer-Capabilities. Die lokale Sitzungsdatei enthält ausschließlich den Modellzugang. Der Modellstatus enthält weder UI-Token noch Annahme-Nonce noch die lokale Gesprächshistorie.
- Der Host bindet ausschließlich `127.0.0.1`. Host-, Origin-, Fetch-Site- und JSON-Prüfung sowie eine restriktive CSP schützen den Browserkanal. Annahmen binden Angebots-ID, Revision, Argumentdigest, Ziel und Beobachtung an eine einmalige Nonce. Kein Schutz gegen bösartigen Code mit vollständigem Zugriff auf dasselbe Betriebssystemkonto wird behauptet.
- Neue menschliche Nachrichten, neue Beobachtungen bei offenen Angeboten, Ziel-/Einschränkungsänderung, Übernahme und Stopp widerrufen alte Freigaben. Unterbrochene UI-Verbindung pausiert nach acht Sekunden, außer beim ausdrücklich freigegebenen `model_alone`-Auftrag. Wiederanbindung spielt keine Aktion erneut ab.
- Stopp beendet die Sitzung. Der CLI-Host schließt daraufhin den Server und entfernt seine eigene Sitzungsdatei; Ctrl+C räumt ebenfalls auf. Die Oberfläche stoppt ihre Sprachaufnahme/-ausgabe beim Verlassen. Ein vom Browser verwaltetes App-Fenster ist kein eigener Modellprozess.
- Kein Roh-Desktop oder vollständiger UIA-Baum geht ans Modell. Semantik: maximal zwölf Elemente / 1.200 Zeichen; die optionale Linse maximal 400 × 400 Pixel. Bekannte Agenten-/Passwortmanagerfenster werden als Ziel ausgeschlossen. Passwortwerte werden nicht gelesen. Die lokale Linse wartet nach einem Klick drei Sekunden zum manuellen Wechsel ins Ziel, aktiviert es aber nicht selbst. Beobachtungsansichten sind keine Sicherheits- oder Erfolgsbeweise.

## Sprache und Budgets

Text bleibt der vollständige Standardweg. Die sichtbare **🎙 Mikrofon**-Taste startet ausschließlich nach einem Klick; während der Aufnahme zeigt sie **■ Aufnahme stoppen**, einen gedrückten Zustand und einen Aufnahmehinweis. Die Erkennung schreibt zunächst einen bearbeitbaren Entwurf und sendet nie automatisch. Browserunterstützung und Mikrofonberechtigung variieren; Browsererkennung kann einen externen Dienst verwenden. Es wird keine Offline-Eigenschaft behauptet. Vorlesen ist abschaltbar, unterbrechbar und verwendet eine Sprecherinstanz. Audio ersetzt keine Annahme.

Alle Zeichenbudgets zählen Unicode-Codepoints, keine Tokens. Begrenzte jüngste Nachrichten, Ziel, aktuelle Einschränkungen und kompakte Dispatchdaten bleiben strukturiert. Lokale Historie ist begrenzt. `--max-turns` begrenzt ausgelieferte Turns zusätzlich zum Auftragsbudget; Antworten maximal 1.200, Kontext maximal 10.000 Zeichen. Stille und UI-Polling erzeugen keine Inferenz; Begleitung verarbeitet Nachrichten oder geänderte Beobachtungsfingerprints mit Debounce. Echte Provider-Usage stammt getrennt aus Codex `turn.completed.usage`: Eingabe, Cache-Lesen/-Schreiben, Ausgabe und Reasoning-Ausgabe, soweit vorhanden. Sie kann lokalen Payload deutlich übersteigen; fehlende Werte bleiben unbekannt. Keine prozentuale Ersparnis wird behauptet.

## Verifikation

`tests/test_cowork*.py` prüfen Service, HTTP-/MCP-Grenze, Manipulationen, Wiederverwendung, veraltete Beobachtungen, read_only, Teilübertragung und Wiederanbindung. Der Browser-Smoke ist mit `OC_BROWSER_SMOKE=1` ausdrücklich opt-in und verwendet einen Fake-Desktop. Er prüft einen echten Browser mit echter HTTP-/Modelltransportstrecke, keine reale Maus-/Tastaturwirkung oder Mikrofon-/Lautsprechergeräte. Reale Desktop- und Audioabnahmen sind getrennt auszuweisen.

Der verbindliche Auftrag und die ursprünglichen Befunde stehen in [PLAN-2026-10-15.md](PLAN-2026-10-15.md).
