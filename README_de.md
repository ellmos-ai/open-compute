# open-compute

<img src="assets/banner.svg" width="100%" alt="open-compute Banner"/>

[EN](README.md) | **DE**

[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange)](CHANGELOG.md)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![Lizenz: MIT](https://img.shields.io/badge/lizenz-MIT-green)](LICENSE)

**Ein modellagnostischer Computer-Use-Kern: ein Agenten-Loop, jedes Reasoning-Modell hinter einer einzigen Schnittstelle.**

open-compute ist ein kleiner, abhängigkeitsarmer Python-Kern zum Bau von
Computer-Use-Agenten (LLM-gesteuerte GUI-/Desktop-/Browser-Automatisierung). Er
realisiert den Loop **Wahrnehmung → Modell-Tool-Call → Aktion → Rückkopplung**
und hält das Modell hinter einer einzigen `ComputerBackend`-Schnittstelle
austauschbar. **Kein Anbieter ist bevorzugt**: Anthropic Claude und OpenAI CUA
sind zwei gleichrangige API-Backends, und das Offline-`mock`-Backend ist der
Standard. Ein **schlüsselloser** Pfad existiert zudem heute über Modus A, in dem
das Host-Modell selbst das Reasoning übernimmt — und es kann diese Schleife
entweder inline oder in einem selbst gespawnten Subagenten zur Kontext-Ökonomie
ausführen (siehe [Nutzungsmuster](#nutzungsmuster--inline-a-vs-selbst-subagent-b)).
Der Kern hat **keine Laufzeit-Abhängigkeiten**; die Anbieter-SDKs (`anthropic`,
`openai`) sind **optionale, lazy importierte** Extras — `import open_compute`
funktioniert ohne jedes davon, und die Standard-Mock-Verdrahtung läuft
vollständig offline.

---

## Warum

Jedes Computer-Use-Modell — Anthropics Claude-`computer`-Tool und OpenAIs
Computer-Use-Tool — teilt dieselbe *Form* des Agenten-Loops, unterscheidet sich
aber in Transport, Koordinatenrahmen und Aktionsnamen. open-compute zieht die
gemeinsamen Teile heraus, sodass der Loop nur einmal geschrieben wird und das
Reasoning-Modell frei hinter einer `ComputerBackend`-Schnittstelle austauschbar
bleibt:

- Ein **einheitliches Aktions-Schema** mit je einem Mapper pro Backend.
- **Normierte (0..1)-Koordinaten** intern, pro Backend / Auflösung / DPI in
  einer getesteten Utility denormalisiert — das DPI-Problem zentral gelöst.
- Ein **zentrales Safety-Gate** („vor riskanten Aktionen bestätigen"), das vor
  jeder Aktion ausgewertet wird.
- Eine **Hybrid-Wahrnehmung** (Screenshot + Set-of-Marks / Accessibility / DOM),
  damit man später von reiner Pixel-Vision auf semantisches Targeting wechseln
  kann.

---

## Architektur

```
                        +-----------------------------------------+
                        |        AGENTEN-LOOP / ORCHESTRATOR      |
                        |  Ziel -> wahrnehmen -> Backend ->       |
                        |  Safety -> ausführen -> neu wahrnehmen  |
                        +-------------------+---------------------+
                                            |
        +-----------------------------------+-----------------------------------+
        |                                   |                                   |
+-------v---------+              +----------v-----------+            +----------v----------+
| WAHRNEHMUNG     |              | KANONISCHE AKTIONEN  |            | SAFETY / POLICY     |
| - Screenshot    |              | click/type/key/      |            | - confirm-at-action |
| - Set-of-Marks  |              | scroll/drag/wait/    |            | - allow/deny-Liste  |
|   (OmniParser)* |              | screenshot + OS-Ext  |            | - read-only-Modus   |
| - Accessibility*|              | (launch/activate)    |            | - Audit-Log         |
+-------+---------+              +----------+-----------+            +----------+----------+
        |                                   |                                   |
        +-----------------+-----------------+----------------------------------+
                          |
              +-----------v------------+   KOORDINATEN-/DPI-NORMALISIERUNG
              | BACKEND-ABSTRAKTION    |   - intern: normiert (0..1)
              | (ComputerBackend)      |   - pro Backend denormalisieren:
              +-----+--------+---------+     * Claude: globale px (display_w x display_h)
                    |        |    |          * OpenAI: px (computer_call)
        +-----------+        |    +-----------+   * Mock: synthetisch
        |                    |                |
+-------v-------+   +--------v-------+  +-----v---------+
| Claude        |   | OpenAI CUA     |  | Mock-Backend  |
| computer_2025 |   | computer-use-  |  | (kein SDK,    |
| 1124 + Beta   |   | preview [?]    |  |  offline)     |
| (Host führt   |   | (Host führt    |  |               |
|  aus)         |   |  aus)          |  |               |
+---------------+   +----------------+  +---------------+

  * = Stub / Interface in dieser Version (siehe Status)
```

---

## Installation

```bash
pip install open-compute               # nur Kern, keine Laufzeit-Abhängigkeiten
pip install open-compute[claude]       # + anthropic-SDK
pip install open-compute[openai]       # + openai-SDK
pip install open-compute[local]        # + mss (echter Windows-Screenshot + Input)
pip install open-compute[compose]      # + Pillow (Vorher|Nachher-Composite + annotierter Shot)
pip install open-compute[watch]        # + watchdog (native FS-Events für Directory-Watch-Feed)
pip install open-compute[local,claude] # lokaler Executor + Claude-Backend
pip install open-compute[all]          # + anthropic, openai, playwright, mss, Pillow, watchdog
```

Python 3.10+.

---

## Schnellstart

### Modus A — Ohne API-Key: Session-Agent als Reasoner (Chat-Skill)

`oc capture` / `oc do` werden manuell aus einer Claude-Code-Session aufgerufen.
Das Session-Modell sieht die PNG über das Read-Tool und entscheidet die nächste
Aktion:

```bash
# 1. Lokales Extra installieren (Windows; Screenshot + Input)
pip install open-compute[local]

# 2. Screenshot aufnehmen — landet automatisch in _session/ (nie lose auf dem Desktop)
oc capture
# -> {"path": ".../_session/0001_20260620_143200.png", "width": 1920, "height": 1080}

# 3a. Einzelne Aktion ausführen (Safety-Gate: confirm als Default)
oc do '{"type":"mouse_move","x":0.5,"y":0.5}' --mode allow_all
oc do '{"type":"left_click","x":0.25,"y":0.1}' --yes   # --yes = Agent hat entschieden

# 3b. Aktion mit automatischem Vorher|Nachher-Composite
oc do '{"type":"left_click","x":0.5,"y":0.3}' --label "klick_ok" --yes
# -> {"result":"executed","action":"left_click","composite":"_session/0002_klick_ok.png"}

# 3c. Batch/Makro: mehrere Aktionen in einem Aufruf (JSON-Array)
oc do '[{"type":"mouse_move","x":0.5,"y":0.5},{"type":"left_click","x":0.5,"y":0.3}]' --yes
# -> {"result":"batch","count":2,"width":1920,"height":1080}

# 3d. Fenster-Vordergrund vor der Aktion sicherstellen
oc do '{"type":"left_click","x":0.5,"y":0.3}' --ensure-foreground "Word" --yes

# 3e. Voll-Res-After-Shot + annotierter Klick-Marker (v0.5, Pillow optional)
oc do '{"type":"left_click","x":0.5,"y":0.3}' --yes --fullres
# -> {"result":"executed",...,"fullres_annotated":"_session/...fullres.png"}

# 3f. Nur Fenster-Bereich capturen (v0.5, Windows)
oc capture --window "Word"
# -> {"path":"...","width":800,"height":600,"window":"Word","region":{...}}

# 3g. Verzeichnis auf Änderungen überwachen (v0.5)
oc watch-dir ~/Downloads --for 5       # 5 Sekunden Events sammeln, JSON ausgeben
oc watch-dir ~/Downloads --once        # einmaliger Snapshot-Diff

# 4. Neuen Screenshot aufnehmen, wiederholen bis fertig.
#    Alternativ: After-Shot direkt aus dem Composite lesen → ein Roundtrip weniger.
```

Vollständiges Loop-Protokoll, Aktions-Schema, Koordinaten-Leitfaden und
Umgebungsvariablen: `SKILL.md`.

### Modus B — Autonomer Loop mit einem API-Backend

Das Backend wird über den Namen gewählt; `claude` und `openai` sind
gleichrangig unterstützt (jedes braucht eigenen Key + Extra). Für einen
**schlüssellosen** Pfad Modus A oben nutzen — das Host-Modell macht das
Reasoning selbst, optional in einem selbst gespawnten Subagenten (siehe
[Nutzungsmuster](#nutzungsmuster--inline-a-vs-selbst-subagent-b)).

```bash
# Claude (benötigt ANTHROPIC_API_KEY + open-compute[local,claude]):
oc run "Finde die neueste Rechnung im Downloads-Ordner" --backend claude --max-steps 15

# OpenAI (benötigt OPENAI_API_KEY + open-compute[local,openai]):
oc run "Finde die neueste Rechnung im Downloads-Ordner" --backend openai --max-steps 15
```

Oder in Python — `get_backend(name, ...)` baut das benannte Backend:

```python
from open_compute import AgentLoop, Config, get_backend
from open_compute.drivers.local import LocalExecutor   # Windows; benötigt mss
from open_compute.safety import SafetyPolicy

executor = LocalExecutor()
config = Config(backend="claude", scope="os",
                display_width=executor.width, display_height=executor.height)
backend = get_backend("claude", executor.width, executor.height, model="claude-opus-4-8")

loop = AgentLoop(
    config,
    backend=backend,
    executor=executor,
    policy=SafetyPolicy(mode="confirm",
                        confirm_callback=lambda a: input(f"{a.type.value} ausführen? [j/N] ") == "j"),
)
loop.run("Finde die neueste Rechnung im Downloads-Ordner")
```

### Offline-Trockenlauf (kein API-Key, kein Display, nur Mock)

```python
from open_compute import AgentLoop, Config

loop = AgentLoop(Config(backend="mock", safety_mode="allow_all"))
result = loop.run("Öffne die Einstellungen und aktiviere den Dunkelmodus")
print(result.done, result.steps)
```

---

## Backend-Matrix

| Backend | SDK | Tool / Modell | Koordinaten | Status |
|---|---|---|---|---|
| `mock` | keins | skriptbasiert, offline | synthetisch | Voll implementiert (**Standard-Backend**) |
| `claude` | `anthropic` (lazy) | `computer`-Tool `computer_20251124`, Beta-Header `computer-use-2025-11-24`, Standardmodell `claude-opus-4-8` | globale Pixel; Host führt aus | Implementiert; mit injiziertem Client getestet |
| `openai` | `openai` (lazy) | computer-use, Modell `computer-use-preview` *(konfigurierbar, `[UNSICHER]`)* | Pixel; Host führt aus | Implementiert; Modellname / Request-Form nicht voll verifiziert |
| `local` (fremder Reasoner) | keins | ein *anderes* Modell als Reasoner — lokales Ollama oder agy / codex / kimi CLIs | Host führt aus | **Separate, niedrig-priorisierte, optionale Idee** — wäre ein echtes neues Backend mit möglichen Fähigkeitsunterschieden. Nicht eingeplant. |

Der schlüssellose / API-freie Pfad ist **kein** Backend-Eintrag — er ist Modus A,
in dem das **Host-Modell selbst** das Reasoning macht (inline oder in einem
selbst gespawnten Subagenten zur Kontext-Ökonomie; siehe
[Nutzungsmuster](#nutzungsmuster--inline-a-vs-selbst-subagent-b)).

Die implementierten Backends (`mock` / `claude` / `openai`) teilen ein
`ComputerBackend`-Protokoll und werden namentlich aus `get_backend()`
(`open_compute/backends/factory.py`) dispatcht — kein Anbieter ist fest in den
Loop verdrahtet. Das Paar Claude-Tool-Typ / Beta-Header ist am Backend
konfigurierbar
(`tool_type=`, `beta_header=`), um auf älteren Modellen das Paar
`computer_20250124` / `computer-use-2025-01-24` anzusprechen.

---

## Status — was ist echt, was ist Stub

**Voll implementiert und getestet**

- Kanonisches Aktions-Schema + `to_claude` / `to_openai`-Mapper.
- Koordinaten normalize / denormalize / rescale.
- Safety-Policy-Gate (`confirm` / `allow_all` / `read_only`, Deny-Liste,
  Bestätigungs-Callback, Audit-Log).
- `Config`-Dataclass + JSON-Loader.
- Agenten-Loop-Orchestrator (Trockenlauf über Mocks).
- Backend-Dispatch über Factory + `MockBackend`; Claude-Backend mit injiziertem
  Fake-Client getestet.
- **`LocalExecutor`** (Windows, `open-compute[local]`): echter Screenshot via
  mss, echte Maus/Tastatur via ctypes SendInput mit VIRTUALDESK + DPI-Awareness.
  Alle Action-Typen implementiert. Live-getestet: `oc capture` → PNG 368 KB
  (1920×1080); `oc do mouse_move` → Cursor bewegt.
- **`oc` CLI** (`oc capture` / `oc do` / `oc run`): Modus A (kein Key, Skill)
  und Modus B (autonomer AgentLoop mit API-Backend) end-to-end verdrahtet.
- **`SKILL.md`**: Loop-Protokoll für den Session-Agenten (Modus A).
- **Multi-Feed-Abstraktion** (v0.4, `open_compute/feeds/`): `PerceptionFeed`- +
  `Targeter`-Protokolle, `ScreenshotFeed` (Pixel) und eine Laufzeit-Feed-Registry
  (`available_feeds()`) mit grazile Capability-Erkennung.
- **`UiaWindowsFeed`** (v0.4, Windows, `open-compute[uia]`): UIA-Elementbaum-
  Wahrnehmung + semantisches Targeting. `observe()` läuft den ControlView-Baum
  ab; `resolve()` macht exakt > Präfix > enthält-Disambiguierung; `invoke()`
  aktiviert klick-frei via InvokePattern → Toggle → SelectionItem →
  LegacyIAccessible-Fallback. `center_norm` ist das exakte Inverse der
  Virtual-Desktop-Abbildung von `LocalExecutor` (Round-Trip durch Tests gedeckt,
  inkl. negativem Multi-Monitor-Ursprung). Die gesamte Invoke-/Resolve-/
  Koordinaten-Logik ist mit **gemocktem** `uiautomation` unit-getestet; reale
  OS-Smoke-Tests (`oc tree`, `oc click-name --mode confirm`,
  `oc invoke --mode confirm`) liefen auf Windows 11 — siehe `CHANGELOG.md`.
- **`oc` CLI** (v0.4): `oc tree`, `oc click-name`, `oc invoke` — alle laufen
  durch das Safety-Gate.
- **`DirwatchFeed`** (v0.5, `open_compute/feeds/dirwatch.py`): Directory-Watch-
  Event-Feed. Überwacht konfigurierte Pfade auf Dateisystem-Änderungen (created /
  modified / deleted / moved) in einem rollenden Puffer (neueste zuerst). Zwei
  Backends: watchdog (MIT, native OS-Events — `open-compute[watch]`) oder
  stdlib-Polling (immer verfügbar). `available()` gibt immer `True` zurück.
  CLI: `oc watch-dir <pfad> [--for SEK] [--once]`.
- **Voll-Res / annotierter Verifikations-Shot** (v0.5): `oc do --fullres` und
  `oc click-name --fullres` speichern einen zusätzlichen vollen After-Shot.
  Pillow (optional) zeichnet einen roten Kreis + Fadenkreuz am Klickpunkt.
  JSON-Schlüssel: `"fullres"` / `"fullres_annotated"`.
- **`oc capture --window SUBSTR`** (v0.5, Windows): capturt nur das Bounding-Rect
  des benannten Fensters via Win32 `GetWindowRect`. Case-insensitiv,
  Whitespace-normalisiert (gleiche Konvention wie `UiaWindowsFeed`).

- **`FeedManager`** (v0.6, `open_compute/feed_manager.py`): dosierte Push-Auto-Injektion.
  Sammelt verfügbare Feeds, wendet Change-Detection je Zyklus an (State-Feeds: SHA-256-Hash;
  Event-Feeds: Rolling-Window), leitet an `InjectorSink` weiter. Dosierungsmodi pro Feed:
  `full` | `delta` | `notify` | `off`; zur Laufzeit via `set_dosage()` anpassbar.
  `LocalFileInjector` (funktionierend, schreibt nach `_state/inject_queue/`).
  `BachInjectorAdapter` (dokumentierter Stub). CLI: `oc push --status` / `oc push --once`.
- **`LearningManager`** (v0.6, `open_compute/learning.py`): Bandit/Bayes-Gewichtung
  (`BetaPrior`), Use-Case-Profile (JSON, Warmstart via `apply_profile_to_manager()`),
  Cross-Session-LESSONS-LEARNED (JSONL). Zustand in gitignoriertem `_state/`.

**Interface / Stub (ehrlich gekennzeichnet)**

- Browser-Treiber und OS-Treiber sind **nur Interfaces** (noch keine
  Playwright-/CDP-/Host-Implementierung).
- Wahrnehmungs-Provider außer `ScreenshotPerception` und dem v0.4-UIA-Feed
  (Set-of-Marks, OCR, Vision-Overlays, DOM) sind **noch nicht implementiert**.
- `BachInjectorAdapter` ist ein dokumentierter Stub; `LocalFileInjector` ist der funktionierende Standard-Sink.
- Always-on Push-Daemon (permanente Hintergrundschleife) ist **noch nicht implementiert**.
- Der UIA-Feed ist **Windows-only**; Linux (AT-SPI) und macOS (AXUIElement)
  Accessibility-Feeds sind **offen / geplant**.
- Modellname und exakte Responses-API-Request-Form des OpenAI-Backends sind
  **nicht voll verifiziert** — vor Produktiveinsatz gegen die aktuelle
  OpenAI-Doku prüfen.
- Der **Selbst-Subagent-Modus (b)** ist ein **Nutzungsmuster** (Doku), kein neuer
  Reasoning-Code — siehe unten. Ein **fremder/lokaler Reasoner** (Ollama/agy/codex/
  kimi) ist eine **separate, nachrangige, optionale** Idee, nicht implementiert.

Details siehe `TODO.md`.

---

## Nutzungsmuster — Inline (a) vs. Selbst-Subagent (b)

> **Muster, kein neues Backend.** Dasselbe Host-Modell, kein API-Key — nur das
> **Kontext-Budget** unterscheidet sich. Vollständiges Design in `ARCHITECTURE.md`
> („Host-Modell-Kontext: Inline (a) vs. Selbst-Subagent (b)").

Wenn das Host-Modell (z. B. Claude Code im Abo) die schlüssellose Modus-A-Schleife
fährt, kann es seinen Kontext auf zwei Arten verbrauchen — **gleiches Modell,
gleiche Vision, gleiches Reasoning**:

- **(a) Inline (heutige Lösung).** Das Host-Modell fährt
  `capture → entscheiden → do → recapture` **im eigenen Kontext**. Am besten für
  **kurze / einfache** Aufgaben (wenige Schritte).
- **(b) Selbst-Subagent (Konzept).** Das Host-Modell spawnt einen Subagenten
  **von sich selbst** (z. B. via `Task`), der die ganze Schleife im **Kontext des
  Subagenten** abarbeitet und nur das **destillierte Ergebnis** zurückgibt
  („Rechnung gefunden: …"). Der **Hauptkontext bleibt sauber**; es „wirkt wie
  API", ist aber **dasselbe Modell** — der Gewinn ist **Kontext-Ökonomie, kein
  Reasoning-/Vision-Verlust**. Am besten für **lange / wiederholte / kontextlastige**
  Aufgaben.

**Das Modell entscheidet pro Aufgabe selbst**, genau wie bei normaler
Subagent-Delegation. Faustregel: kurz → inline (a); lang / wiederholt /
kontextlastig → Subagent spawnen (b).

Ein **persistenter 24h-Erfahrungs-Subagent** ist eine optionale Variante von (b):
ein langlebiger Selbst-Subagent, der wiederholte Aufträge annimmt und
akkumulierte Erfahrung via der vorhandenen `learning.py` (`BetaPrior` /
Use-Case-Profile / LESSONS-LEARNED in `_state/`) wiederverwendet. Die Erfahrung
lebt in `_state/` (persistent), nicht im flüchtigen Subagenten-Kontext. (Lessons
sollten Verfall / Confidence tragen, um falsche Lehren zu vermeiden — eine
kleine, additive Änderung, noch nicht implementiert.)

> Ein *anderes* Modell als Reasoner (lokales Ollama oder agy / codex / kimi CLIs)
> ist eine **separate, niedrig-priorisierte, optionale** Idee — das wäre ein
> echtes neues `ComputerBackend` mit möglichen Fähigkeitsunterschieden und ist
> **nicht** Modus (b).

---

## Sicherheit

Computer-Use ist mächtig. Der Standard-Modus der `SafetyPolicy` ist `confirm`:
Klicks, Tippen, Tasten, Drags und App-Starts werden blockiert, sofern kein
Bestätigungs-Callback zustimmt. Empfehlung (entspricht den Hinweisen beider
Anbieter):

- Echte Backends in einer **isolierten VM/Container** ausführen, nie auf dem
  Hauptsystem.
- **Mensch im Loop** behalten.
- **Bildschirminhalte als nicht vertrauenswürdig** behandeln
  (Prompt-Injection-Risiko).

Siehe `SECURITY.md`.

---

## Tests ausführen

```bash
python -X utf8 -m pytest -q
```

Tests sind reine Mock-Tests und brauchen kein SDK.
`pip install open-compute[dev]` installiert pytest.

---

## Lizenz

MIT — siehe [LICENSE](LICENSE).
