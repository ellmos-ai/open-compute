# open-compute — Architektur: Multi-Feed-Wahrnehmung

> Design-Leitbild (2026-06-20). Status: Zielarchitektur; bisher implementiert ist der
> Windows-`LocalExecutor` (Pixel + Input) und validiert der UIA-Element-Baum (siehe TODO.md).

## Leitprinzip

**Das LLM entscheidet, welche Daten es nutzt.** Deshalb werden ALLE Wahrnehmungs-Kanäle
(„Feeds") kontinuierlich und automatisch **vorerhoben** und stehen dem Modell gleichzeitig
bereit → blitzschnelle Reaktion ohne Pull-Latenz. Es gibt eine bevorzugte Reihenfolge, aber
das Modell **gewichtet situativ** und wählt selbst.

**Multi-Feed = Robustheit:** Mehr Kanäle = mehr Wege zum Ziel. Fällt einer weg (anderes OS,
schlecht „verkabelte" App), tragen die anderen.

## Die Feeds

1. **Screenshot-Live-Feed** — `live.png`, ~1×/s neu geschrieben; immer ~aktueller Pixel-Stand.
2. **Action-Chain-Feed** — Historie der Aktionen + Vorher/Nachher (Provenance/Change-Log):
   was wurde getan, was hat sich geändert (mit Erfolgs-/Fehler-Markierung → Lernschicht).
3. **OCR- & Symbol-/Lexika-Detektion-Feed** (OS-abhängig) — Text- + Icon/Symbol-Erkennung;
   liefert eine Text→Bounding-Box-Karte und erkannte Symbole/Programm-Icons.
4. **Narrator-/Vision-Prebuffer-Feed** — kleines lokales Vision-Modell beschreibt den Screen
   laufend in einen **vorgeladenen Puffer** (sofort lesbar, statt erst bei Bedarf zu rechnen).
   → **Bewertung (2026-06-20):** lohnt v. a. als **billiger Always-on-Sentinel/Triage** im Push-Modus
   (Change-Narration „Dialog erschienen / Fensterwechsel"; entscheidet, WANN ein voller Screenshot ans
   Hauptmodell gepusht wird) und als Fallback, wo UIA/OCR dünn sind (custom-gezeichnete UIs/Spiele).
   Für reines On-Demand-**Pull eher redundant**, weil das Hauptmodell selbst stark sieht. → niedrigere
   Priorität als UIA/OCR/Directory-Watch/Push; Modell muss GUI-tauglich sein (Florence-2/Moondream/
   MiniCPM-V), generische Tiny-Captioner sind bei UI schwach.
5. **Element-/Stammbaum-Feed** (OS-abhängig) — Accessibility-Baum: Name / Rolle / Wert /
   Rechteck je Element + **direktes Invoke** (klick-frei). Stärkster Kanal für native Apps.
6. **Directory-/Filesystem-Watch-Feed** (Event-Feed, OS-abhängig) — überwacht definierte
   Verzeichnisse und meldet Änderungen **automatisch** (erstellt / geändert / gelöscht /
   umbenannt), ohne Polling. Use case: sofort wissen, wann ein Speichern/Export **fertig** ist
   (statt zu früh zu pollen — genau die Reibung beim `wikipedia-word-test.docx`-Speichern
   2026-06-20: Datei wurde ~5 s nach dem Pull geschrieben), Downloads/Outputs erkennen,
   „hat meine Aktion eine Datei erzeugt?" verlässlich beantworten.
   **Reuse-Quelle:** FileCommander „directory scan" als Basis; cross-platform `watchdog` (MIT).

## Always-on + modell-gewichtet

- Alle Feeds laufen im Hintergrund mit **Change-Detection** (nur bei Bildänderung neuer
  Eintrag → günstig; erkennt Fensterwechsel etc.).
- **Default-Reihenfolge (native Apps):** Element-Baum (5) > OCR (3) > Vision-Prebuffer (4) >
  Pixel (1), mit Action-Chain (2) als Kontext. Das Modell darf abweichen/umgewichten.

## Feed-Bereitstellung: Push (Auto-Injektion) statt Pull

**Status v0.6.0:** `feed_manager.py` implementiert den Push-Layer vollständig (real, unit-getestet).
`oc push --status` / `oc push --once` als CLI. Die Hintergrund-Daemon-Schleife (permanenter Push)
ist noch nicht implementiert — Trigger, Frequenz und always-on-Loop folgen in einem späteren Release.

**Status v0.5.0 (veraltet):** der Agent **pullt** die Feeds manuell (führt `oc tree`/`oc capture`/… aus und
liest das Ergebnis). Das ist die Hauptquelle für „schleppend" und Reibung (z. B. zu früh nach
einer Datei pollen, jeden Schritt einzeln auslösen).

**Ziel: Push** — die Feeds werden kontinuierlich erhoben und **automatisch in den Agent-Kontext
injiziert**; das laufende Modell bekommt den aktuellen Stand „von selbst" (ohne jeden Feed einzeln
abzurufen) und reagiert blitzschnell.

**Wiederverwendung: BACH-Injektoren.** BACHs vorhandenen Kontext-Injektions-Mechanismus als
Transport prüfen, statt einen neuen Push-Kanal von Grund auf zu bauen. Offene Punkte:
- **Trigger:** an Change-Detection koppeln — nur bei Änderung injizieren, sonst Stille (kein Spam).
- **Budget:** Kontextgröße begrenzen — nur Deltas / die je Situation relevanten Feeds, nicht alles roh.
- **Mapping:** Feed → Injektor (welcher Feed wird wie/als was injiziert: Live-Bild-Referenz,
  Element-Liste, Change-Log-Zeile, FS-Event).

**Dosierung (wie BACH-Injektoren) — pro Feed einstellbare Push-Granularität:**
- **Voll-Push:** kleine, diskrete Ereignisse (z. B. einzelne Dateiänderung) → immer ganz pushen.
- **Delta/Notify-Push:** große/kontinuierliche Feeds (Screenshot, Element-Baum) → nur
  Aktualisierungen / Teile / Benachrichtigungen pushen (kontextschonend). Das Modell kann den
  **vollen Feed bei Bedarf „bestellen"** → manueller Pull bleibt als Ergänzung erhalten.
- **LLM-selbst-justierbar:** das Modell passt Gewicht / Frequenz / Kontextmenge pro Feed SELBST an —
  merkt im Verlauf „Feed X ist für Software Y verlässlicher" → lässt dort mehr Kontext herein / pusht öfter.
- **Use-Case-Profile:** (Programm/Usecase) → welche Feeds erfolgreich + welche Injektor-Settings →
  als Profil gespeichert und beim nächsten Mal automatisch geladen (Warmstart).

## Daten-Lebenszyklus / Aufräumen

Feeds zerfallen nach Aufbewahrung in zwei Klassen:

**A) State-Feeds — überschreiben, KEINE Historie.** Sie repräsentieren den AKTUELLEN Stand;
nur der letzte Wert zählt → fester Dateiname, in-place überschreiben (Ring-Puffer = 1):
- ① Screenshot-Live (`live.png`)
- ⑤ Element-/Stammbaum (`tree.json`) — ist ebenfalls ein **Live-Feed** (überschreiben, nicht akkumulieren)
- ③ OCR-Karte (`ocr.json`) und ④ Vision-Beschreibung (`caption.txt`) — vom Live-Bild abgeleitet
- **Change-Detection-Kopplung:** nur neu berechnen, wenn sich das Live-Bild ändert (Hash);
  sonst den vorhandenen Wert weiterverwenden (spart Rechenzeit/Strom). Kein Aufräumen nötig,
  da nichts akkumuliert.

**B) Event-Feed — begrenzte Historie.** ② Action-Chain ist eine Folge von Aktionen. In der
Regel zählt nur die **letzte** Aktion (deren Vorher/Nachher) → sie wird standardmäßig
herausgereicht. Für **Ketten-Prüfung** (mehrere Aktionen am Stück) ein **rollendes Fenster**
der letzten N behalten (`OC_SESSION_KEEP`), ältere automatisch verwerfen. Gilt für die
Aktions-Composites in `_session/` (rotieren bereits).

Ebenso Event-Feed: ⑥ **Directory-Watch** (Datei-Änderungs-Events; jüngste zuerst, rollendes Fenster).

**Default-Sicht:** „letzter Zustand + letzte Aktion". Kette nur auf Anforderung.
**Folge:** `_session/` bleibt klein — State-Dateien überschreiben sich, Action-Composites
rotieren (keep N). Optionaler `oc clean`-Befehl fürs manuelle Leeren.

## Lernschicht (Erfolgs-/Fehlerraten)

**Status v0.6.0:** `learning.py` implementiert die gesamte Lernschicht (real, unit-getestet).

- **`LearningManager.log_outcome(feed, app, action_type, success)`**: protokolliert Outcomes in
  `_state/outcomes.jsonl` und aktualisiert Bandit/Bayes-Gewicht in `_state/weights.json`.
- **`BetaPrior`**: geschlossene Beta-Posterior-Lösung (deterministisch, ohne Random Sampling),
  `expected_rate` = Alpha/(Alpha+Beta); Prior Beta(1,1) = Laplace-Glättung.
- **`success_rate(feed, app, action_type)`**: Beta-Posterior-Erwartungswert (0..1); Prior 0.5.
- **`best_feed(app, action_type, candidates)`**: wählt den Feed mit höchstem expected_rate.
- **Use-Case-Profile:** `save_profile / load_profile` → `_state/profiles.json`; Warmstart via
  `apply_profile_to_manager(manager, program, usecase)` → ruft `set_dosage()` des FeedManagers.
- **Cross-Session LESSONS-LEARNED:** `add_lesson(text, tags)` / `get_lessons(tag)` →
  `_state/lessons.jsonl` (JSONL, persistent über Sessions); `max_lessons`-Cap beim Laden.
- Alle State-Dateien in `_state/` (gitignored, lokal, nie commitet).

Ursprüngliches Zieldesign (weiterhin gültig):
- Pro Aktion wird **(genutzter Feed, App, Aktionstyp) → Erfolg/Fehler** protokolliert.
- Daraus lernt das System, **welcher Feed wann am verlässlichsten** ist, und gewichtet dynamisch
  (Bandit/Bayes pro App×Feed×Aktionstyp). So wird der „bevorzugte Weg" empirisch statt fix.
- **Use-Case-Profile:** erfolgreiche Feed-/Injektor-Kombinationen je (Programm/Usecase) persistieren
  und wiederverwenden (Warmstart statt Neulernen).
- **Cross-Session LESSONS-LEARNED (lokale Memory):** übergreifende Lernlektionen über ALLE Sessions
  hinweg persistent ablegen — z. B. „in App Z ist UIA-Invoke unzuverlässig, nutze Pixel-Klick".
  Speist Default-Gewichte + Profile.

## OS-Varianten je Feed (feed-varianten-OS-sensitive)

| Feed | Windows | Linux | macOS |
|---|---|---|---|
| Screenshot | mss / DXGI Desktop Duplication | mss / X11 / Wayland-Portal | mss / Quartz CGWindow |
| **Element-Baum (Accessibility)** | **UI Automation (UIA)** | **AT-SPI2 / ATK (D-Bus)** | **AX / NSAccessibility (AXUIElement)** |
| OCR | Windows.Media.Ocr / Tesseract | Tesseract | Vision.framework / Tesseract |
| Input | SendInput | X11 XTEST / uinput / ydotool (Wayland) | CGEvent |
| **Invoke (klick-frei)** | UIA InvokePattern | AT-SPI Actions | AX AXPress |
| Vision-Modell | lokal (Ollama/ONNX) | lokal | lokal (auch CoreML) |
| **Directory-Watch (FS-Events)** | ReadDirectoryChangesW | inotify | FSEvents — *cross-platform-Wrapper: `watchdog` (MIT)* |

**Permissions/Caveats:** macOS verlangt Accessibility- + Screen-Recording-Freigabe (TCC).
Linux/Wayland schränkt globalen Input/Capture ein (Portals/uinput statt freier X11-Zugriff).
Nicht jede App ist gut „verkabelt" (custom-gezeichnete UIs, manche Electron/Games) → dort
fällt der Element-Baum dünn aus und Pixel/OCR/Vision tragen.

## Fallback-Routing

- Fällt ein Pfad weg, übernehmen andere Feeds → System bleibt funktionsfähig.
- **OS-sensitive Varianten/Fallbacks** sind den generischen vorzuziehen (höhere Stabilität).
- Es kann auch einen **OS-exklusiven Pfad** geben, der nur dort existiert und dort an die
  Stelle tritt (z. B. UIA-Invoke unter Windows, AX-Press unter macOS).

## Konsequenz für die Implementierung

- Gemeinsame Abstraktion:
  - `PerceptionFeed`-Protokoll → liefert normalisierte Beobachtungen (Pixel/Text/Baum/Beschreibung).
  - `Targeter` → Name/Beschreibung („Button Einfügen") → Aktion (Klick-Koordinate ODER direktes Invoke).
- Pro Feed ein **OS-Adapter**; eine **Capability-Registry** wählt zur Laufzeit die verfügbaren
  Adapter (Capability-Detection statt Annahme).
- Erster konkreter Baustein: bestehender Windows-`LocalExecutor` (Pixel+Input) + validierter
  **UIA-Element-Feed** → daraus den ersten echten Multi-Feed-Pfad bauen (Phase 2).

---

# Agent-Brain-Backends & Subagent-Treiber-Modus

> **Status dieses Abschnitts: KONZEPT (Design, nicht implementiert).** Stand 2026-06-20.
> Bereits **vorhanden** (echt, getestet): die `ComputerBackend`-Abstraktion mit den Backends
> `mock` / `claude` / `openai` (`open_compute/backends/`), der Push-Layer `FeedManager` +
> `InjectorSink` (`feed_manager.py`) und die Lernschicht `LearningManager` (`learning.py`).
> **Noch NICHT vorhanden:** ein lokales-LLM- bzw. Subagent-Reasoning-Backend und ein
> langlebiger 24h-Erfahrungs-Agent. Dieser Abschnitt entwirft beides und dockt es an die
> bestehenden Nähte an.

## Begriffsklärung (wichtig)

In diesem Code ist **„Treiber" zweideutig** — daher präzise:
- `open_compute/drivers/` = **Executors** („Hände": Screenshot + Input ausführen,
  z. B. `LocalExecutor`, `MockExecutor`).
- `open_compute/backends/` = **Reasoning-„Gehirne"** (entscheiden die nächste Aktion;
  implementieren das `ComputerBackend`-Protokoll, `backends/base.py`).

Der „Subagent-Treiber-Modus" ist ein neues **Backend** (ein `ComputerBackend`), **kein**
Executor/Driver im `drivers/`-Sinn. Der Agenten-Loop (`loop.py`) bleibt unverändert: er
fragt `backend.start()/step()`, gated über die Safety-Policy, führt über den Executor aus.

## Was bereits trägt (vorhanden)

Das Reasoning ist schon modell-agnostisch verkabelt:
- `ComputerBackend`-Protokoll (`backends/base.py`): `start(goal, observation) -> BackendResult`
  und `step(observation) -> BackendResult`; `BackendResult` = `actions: list[Action]`,
  `done: bool`, `message`, `raw`.
- `get_backend(name, w, h, **kwargs)` (`backends/factory.py`) dispatcht namentlich;
  Anbieter-SDKs werden **lazy** importiert — kein Anbieter ist fest verdrahtet.
- Der Loop besitzt Koordinaten-Denormalisierung + Safety-Gate; Backends liefern nur
  **kanonische** `Action`s. Ein neues Backend muss also nur Beobachtung→Aktionen leisten.

## (a) Backend „API-Key" — bestehend

`ClaudeComputerBackend` / `OpenAIComputerBackend`: rufen die jeweilige Anbieter-API mit dem
Computer-Tool auf, der Host führt die zurückgegebenen Tool-Calls aus. Brauchen Key + SDK-Extra.

## (b) Backend „Host-LLM-Subagent" / lokales LLM — KONZEPT

**Idee:** Statt einer Anbieter-API bekommt ein **Subagent** (oder lokales Modell) die Aufgabe
und entscheidet die Aktionen — wirkt „wie API", ohne eigenen API-Key. Reasoner-Kandidaten:
Claude-Code-`Task`-Subagent, `agy` (Gemini), `codex` (GPT), `kimi`, oder ein **lokales Ollama**-Modell.

**Neues Modul (geplant):** `open_compute/backends/subagent.py` mit `SubagentBackend(ComputerBackend)`.

**Schnittstelle — wie der Subagent angesteuert wird (Driver-Abstraktion im Backend):**

```text
SubagentDriver (Protokoll)              # NICHT drivers/ — gehört logisch zum Backend
  ask(prompt: str, image_path: str|None) -> str   # ein Reasoning-Turn, Text rein/raus

  Implementierungen (geplant):
   - ClaudeCodeTaskDriver   → Claude-Code-Subagent via Task/SendMessage
   - CliSubprocessDriver     → agy / codex / kimi headless (Datei-Order rein, Datei-Antwort raus;
                               vgl. ~/CLAUDE.md: agy/codex „Antwort als Datei", nicht stdout)
   - OllamaHttpDriver        → POST http://<host>:11434/api/chat (lokal, kein Key)
```

**Feeds rein → Aktionen raus (der Kontrakt):**
1. `SubagentBackend.start(goal, observation)` baut einen **Text-Prompt**: Ziel + kanonisches
   Aktions-Schema (erlaubte `ActionType`s + JSON-Form) + die **Feeds** als Text/Refs:
   Screenshot als Datei-Pfad (Bild-fähige Reasoner) ODER Element-Liste (UIA-`observe()`) /
   OCR-Karte / Action-Chain-Zeile — exakt die Feeds, die der `FeedManager` ohnehin erhebt.
2. Der `SubagentDriver` übergibt den Prompt (+ optional Bildpfad) an den Subagenten und
   liefert dessen Text zurück.
3. Ein **Parser** extrahiert aus der Antwort JSON-Aktionen (ein Objekt oder Array, gleiches
   Format wie `oc do`) und übersetzt sie in kanonische `Action`s → `BackendResult.actions`.
   `done` = der Subagent meldet Abschluss (z. B. `{"type":"done"}` oder leere Aktionsliste +
   Abschluss-Marker).
4. `step(observation)` wiederholt mit der neuen Beobachtung (re-perceive); der Loop bleibt gleich.

**Warum das sauber andockt:** identisch zum bestehenden Backend-Vertrag (Beobachtung→`Action`s);
Safety-Gate + Executor + Koordinaten bleiben unverändert; nur die Reasoning-Quelle wechselt.
Wiederverwendung der Feed-Serialisierung aus `feed_manager.py` (Dosierung: was/wie viel in den
Prompt) statt einer zweiten Sicht.

## Persistenter 24h-Erfahrungs-Agent — KONZEPT

**Idee:** Ein **langlebiger** Subagent läuft dauerhaft, nimmt wiederholt Aufträge entgegen und
**akkumuliert Erfahrung**, die in spätere Läufe zurückfließt → wird mit der Zeit besser.

**Lifecycle:**
- **Start/Attach:** ein langlebiger Subagent-Handle (Claude-Code-Subagent via `SendMessage`,
  oder ein dauerhafter Ollama-Server / CLI-Session). Wiederverwenden statt pro Auftrag neu starten.
- **Auftrags-Queue:** Jobs (Ziel + Ziel-App/Fenster) werden nacheinander an denselben Subagenten
  übergeben. Pro Job ein voller `AgentLoop.run()` mit `SubagentBackend` als Brain.
- **Idle:** wartet auf den nächsten Auftrag; keine Anbieter-Kosten, da keine API.

**Erfahrungs-Persistenz (über `learning.py`, vorhanden):**
- Nach jeder Aktion `LearningManager.log_outcome(feed, app, action_type, success)` →
  `BetaPrior`-Gewicht je (App × Feed × Aktionstyp), persistiert in `_state/`.
- Übergreifende Lektionen via `add_lesson(text, tags)` → `_state/lessons.jsonl`
  (z. B. „App Z: UIA-Invoke unzuverlässig → Pixel-Klick").
- Erfolgreiche Feed-/Dosierungs-Kombis je (Programm, Usecase) via `save_profile()`;
  Warmstart beim nächsten Lauf via `apply_profile_to_manager()`.

**Dosierter Push der Erfahrung in den nächsten Lauf (vorhanden: FeedManager/InjectorSink):**
- Beim `start()` injiziert `SubagentBackend` die **relevanten** Lessons/Profile in den Prompt —
  gefiltert (`get_lessons(tag=app)`), **dosiert** (nur Top-N / passende Tags), damit der Kontext
  klein bleibt (gleiches Dosierungs-Prinzip wie die Feeds: `full`/`delta`/`notify`).
- So nutzt jeder neue Auftrag die kumulierte Erfahrung, ohne den Prompt zu überladen.

**Rotation / Reset:**
- **Rotation bei Domänenwechsel oder Kontext-Größe** (vgl. TICKET-MASTER-Companion-Muster):
  neuen Subagenten starten, wenn die Ziel-App wechselt oder der Kontext zu groß wird.
- **Reset** = Subagent verwerfen; die Erfahrung bleibt in `_state/` (persistenter Speicher,
  vom flüchtigen Subagent-Kontext getrennt) → ein frischer Subagent startet sofort warm.

**Sicherheit / Grenzen:**
- **Safety-Gate gilt unverändert** — jede vom Subagenten vorgeschlagene Aktion läuft durch
  `SafetyPolicy` (Default `confirm`); ein autonomer 24h-Agent erhöht das Risiko, daher
  Empfehlung: isolierte VM/Container, restriktive Allow/Deny-Liste, Bildschirminhalt als
  nicht vertrauenswürdig behandeln (Prompt-Injection).
- **Drift/Halluzination:** Subagent-Antworten sind frei — der Aktions-Parser muss strikt
  validieren (nur bekannte `ActionType`s, Koordinaten in [0,1]) und Unbekanntes verwerfen.
- **Loop-Schutz:** `max_steps` (vorhanden) + Timeout pro Subagent-Turn; bei wiederholtem
  Fehlschlag eskalieren statt blind weiterzufahren.
- **Faktentreue der Erfahrung:** Lessons sind empirisch (aus `log_outcome`), keine erfundenen
  Zeit-/Erfolgsangaben.

## Abgrenzung KONZEPT vs. vorhanden (Zusammenfassung)

| Baustein | Status |
|---|---|
| `ComputerBackend`-Protokoll + `mock`/`claude`/`openai` | **vorhanden** (`backends/`) |
| `FeedManager` / `InjectorSink` / dosierter Push | **vorhanden** (`feed_manager.py`) |
| `LearningManager` (BetaPrior, Profile, Lessons) | **vorhanden** (`learning.py`) |
| `SubagentBackend` + `SubagentDriver` (Claude-Code/agy/codex/kimi/Ollama) | **KONZEPT** |
| Persistenter 24h-Erfahrungs-Agent (Queue, Rotation, Warmstart) | **KONZEPT** |

> Implementierung erst nach Konzept-Abnahme (siehe `TODO.md` → Roadmap).
