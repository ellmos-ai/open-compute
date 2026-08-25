# open-compute Skill — Mode A: Session-Agent als Reasoner

**Skill-ID:** `open-compute`
**Version:** 0.9.0
**Modus:** A — OHNE API-Key, Session-Modell als Reasoner, manuelles Stepping
**Voraussetzungen:** Extra `[local]` installiert (mss; siehe Installation unten — das Paket kommt aus dem Git-Repo, nicht von PyPI); Windows-Host; `oc` CLI aufrufbar via `python -m open_compute.cli`

---

## Wann diesen Skill nutzen

Wenn ein Computer-Use-Agent die lokale Windows-Oberfläche steuern soll, **ohne** dass ein API-Key für einen autonomen Backend-Loop vorhanden ist. Das Session-Modell (dieser Agent) übernimmt das Reasoning: es sieht den Bildschirm via Read-Tool (PNG) und entscheidet die nächste Aktion.

Für den autonomen Modus mit API-Key: `oc run "<ziel>" --backend claude|openai` (Modus B — kein Skill nötig).

---

## Kontext-Ökonomie: inline (a) vs. eigenen Subagenten spawnen (b)

Die Schleife unten läuft **schlüssellos** im Kontext **dieses** Host-Modells. Du hast zwei Wege — **gleiches Modell, gleiche Vision, gleiches Reasoning**, nur unterschiedlicher Kontext-Verbrauch:

- **(a) Inline:** Du fährst `oc capture` / `oc do` direkt in deinem eigenen Kontext. Jeder Screenshot landet in deinem Hauptkontext. **Für kurze / einfache Aufgaben** (wenige Schritte).
- **(b) Eigenen Subagenten spawnen:** Du spawnst einen Subagenten **von dir selbst** (z. B. via `Task`), der die komplette Schleife in **dessen** Kontext abarbeitet und dir nur das **destillierte Ergebnis** zurückgibt. Dein Hauptkontext bleibt sauber; es „wirkt wie API", ist aber dasselbe Modell — **kein Reasoning-/Vision-Verlust, nur Kontext-Ökonomie**. **Für lange / wiederholte / kontextlastige Aufgaben** (viele Screenshots/Schritte).

**Faustregel (du entscheidest selbst, wie bei jeder Subagent-Delegation):** kurz/einfach → inline (a); lang/wiederholt/kontextlastig → Subagent spawnen (b). Eine optionale Erweiterung von (b) ist ein langlebiger Erfahrungs-Subagent (siehe ARCHITECTURE.md → „Host-Modell-Kontext"). Hinweis: Ein *anderes*/lokales Modell als Reasoner ist eine separate, nachrangige Idee, NICHT (b).

---

## Aktions-Schema (kanonisch)

Alle Aktionen als JSON, Felder laut `open_compute/actions.py`:

```json
{"type": "<ActionType>", ...felder}
```

### ActionType-Vokabular

| type | Pflichtfelder | Optionale Felder | Beschreibung |
|---|---|---|---|
| `screenshot` | — | — | Nur Screenshot, keine Aktion |
| `mouse_move` | `x`, `y` | — | Maus bewegen (kein Klick) |
| `left_click` | `x`, `y` | — | Linksklick |
| `right_click` | `x`, `y` | — | Rechtsklick |
| `middle_click` | `x`, `y` | — | Mittelklick |
| `double_click` | `x`, `y` | — | Doppelklick |
| `triple_click` | `x`, `y` | — | Dreifachklick (z. B. Zeile markieren) |
| `left_click_drag` | `x`, `y`, `end_x`, `end_y` | — | Ziehen von (x,y) nach (end_x,end_y) |
| `type` | `text` | — | Text tippen |
| `key` | `text` | — | Tastenkombination (z. B. `"ctrl+s"`, `"Return"`, `"escape"`) |
| `scroll` | `x`, `y` | `scroll_direction` (up/down/left/right), `scroll_amount` (int) | Scrollen |
| `wait` | — | `duration` (Sekunden, float) | Warten |
| `cursor_position` | — | — | Aktuelle Cursor-Position abfragen (read-only) |
| `launch_app` | `app_name` | — | App starten |
| `activate_window` | `app_name` | — | Fenster in Vordergrund bringen |
| `mouse_down` | — | `x`, `y`, `button` (left/right/middle) | Maustaste **drücken und gedrückt lassen** (ohne `x`/`y` an der aktuellen Cursor-Position) |
| `mouse_up` | — | `x`, `y`, `button` | Maustaste loslassen |
| `key_down` | `text` | — | Taste(n) drücken und **gedrückt halten** (z. B. `"shift"`, `"ctrl+shift"`) |
| `key_up` | `text` | — | Taste(n) loslassen |

**Koordinaten** `x`, `y`, `end_x`, `end_y`: **normalisiert 0..1** (0,0 = oben-links, 1,1 = unten-rechts des virtuellen Desktops). NICHT Pixel.

**Halte-Primitive** (`mouse_down`/`mouse_up`/`key_down`/`key_up`): Die zusammengesetzten Aktionen oben können keinen Zustand halten — `left_click_drag` zieht in einem Zug, und `key` drückt und löst sofort. Für Aufziehen einer Auswahlbox mit Zwischenschritten, modifikator-gehaltenes Klicken (Mehrfachauswahl) oder gedrückt-halten-Eingaben in Spielen braucht es die Hälften einzeln. Sie haben **kein** Pendant im Claude- oder OpenAI-Computer-Tool (die Mapper lehnen sie ab); der Host führt sie direkt aus. Der `LocalExecutor` führt Buch über alles Gedrückte und gibt es per `release_all()` wieder frei — der MCP-Server tut das beim Beenden automatisch, damit ein abgebrochener Zug keine gedrückte Taste hinterlässt.

### Beispiele

```json
{"type": "left_click", "x": 0.5, "y": 0.25}
{"type": "type", "text": "Hallo Welt"}
{"type": "key", "text": "ctrl+s"}
{"type": "scroll", "x": 0.5, "y": 0.5, "scroll_direction": "down", "scroll_amount": 3}
{"type": "left_click_drag", "x": 0.1, "y": 0.1, "end_x": 0.9, "end_y": 0.9}
{"type": "mouse_move", "x": 0.5, "y": 0.5}
```

Auswahlbox über mehrere Schritte aufziehen (als Batch):

```json
[{"type": "mouse_down", "x": 0.1, "y": 0.1},
 {"type": "mouse_move", "x": 0.5, "y": 0.4},
 {"type": "mouse_move", "x": 0.9, "y": 0.9},
 {"type": "mouse_up"}]
```

---

## Loop-Protokoll (Modus A, v0.3)

### Kompakter Flow (empfohlen)

```
CAPTURE → SEE → REASON → [BATCH-DO mit --label] → REPEAT
```

### Schritt-für-Schritt

1. **CAPTURE** — Screenshot aufnehmen:
   ```
   python -m open_compute.cli capture
   ```
   Gibt JSON zurück: `{"path": "...", "width": W, "height": H}`
   - Screenshot landet automatisch in `_session/` (Modul-Root, gitignored), nie lose im Desktop/CWD.
   - Sequenznummer + Zeitstempel im Dateinamen; alte Dateien rotieren (Standard: letzten 20 behalten).
   - Override: `--out pfad.png` oder `OC_SESSION_DIR=<verzeichnis>`.

2. **SEE** — PNG via Read-Tool lesen (als Bild — das ist der Wahrnehmungskanal).

3. **REASON** — Nächste Aktion(en) im open-compute-Schema entscheiden.
   - Ist das Ziel erreicht? → DONE, Loop beenden.
   - Sonst: Aktion(en) formulieren (JSON wie oben).
   - Safety-Default: `confirm` — bei unsicheren Aktionen (Klick, Tippen) erst nachfragen.

4. **SAFETY + EXECUTE** — Aktion ausführen:

   **Semantisches Ziel (Standard für Klicks):**
   ```
   python -m open_compute.cli click-name "Speichern" --window "Word" --yes
   ```
   `click-name` löst das Ziel über UIA auf und bindet seinen
   Koordinaten-Fallback automatisch an das Top-Level-Fenster. Wenn möglich,
   zuerst `invoke` nutzen; das aktiviert das Element ganz ohne Mausklick.

   **Roher Koordinatenklick (nur Fallback):**
   ```
   python -m open_compute.cli do '<action-json>' --yes \
     --expected-window '<hwnd-pid-title-json>' \
     --coordinate-frame '<left-top-width-height-json>'
   ```
   Beide JSON-Objekte sind Pflicht. Ohne sie lautet die strukturierte Antwort
   `preclick_verification_failed`, und es wird kein Klick gesendet.

   **Einzelne Aktion mit Before|After-Composite (`--label`):**
   ```
   python -m open_compute.cli do '<action-json>' --label "click_save" --yes
   ```
   Antwort: `{"result": "executed", ..., "composite": "_session/0001_click_save.png"}`
   (oder `"before"` / `"after"` wenn Pillow nicht installiert ist — graceful degrade)

   **Batch/Makro (JSON-Array):**
   ```
   python -m open_compute.cli do '[{"type":"mouse_move","x":0.5,"y":0.5},
     {"type":"key","text":"tab"}]' --yes
   ```
   Antwort: `{"result": "batch", "count": 2, "width": W, "height": H}`

   **Batch mit Final-Composite:**
   ```
   python -m open_compute.cli do '[...]' --label "macro_foo" --yes
   ```

   **Batch mit Per-Step-Composites:**
   ```
   python -m open_compute.cli do '[...]' --shots each --label "macro" --yes
   ```
   Antwort: `{"result": "batch", "count": N, "composites": [...]}`

   **Fenster-Vordergrund sicherstellen:**
   ```
   python -m open_compute.cli do '<json>' --ensure-foreground "Word" --yes
   ```
   Prüft vor der Aktion ob "Word" im Fenstertitel des Vordergrundfensters steht;
   wenn nicht, wird `activate_window("Word")` automatisch aufgerufen.

   **Safety-Ergebnisse:**
   - `{"result": "confirm", ...}` (Exit 1): Bestätigung nötig → mit `--yes` erneut ausführen.
   - `{"result": "deny"}` (Exit 1): Aktion verweigert → anderen Weg wählen oder User fragen.
   - Bei Batch: `"action_index"` und `"executed_before"` zeigen wo die Sequenz gestoppt hat.
   - Bei Koordinatenklicks: `"preclick_verification_failed"` mit Code
     (`expected_window_required`, `coordinate_frame_required`,
     `window_at_point_unresolvable` oder `window_identity_mismatch`) bedeutet:
     Fensteridentität unsicher, Backend wurde nullmal aufgerufen.

5. **RECAPTURE** — Zurück zu Schritt 1.
   - Alternativ: After-Shot aus Composite direkt lesen (`"composite"` oder `"after"` im Ergebnis-JSON) → ein Roundtrip gespart.

### Präzisionsvertrag für Klicks

1. **UIA vor Koordinaten:** `invoke` (klickfrei) → `click-name` (semantisch,
   verifizierter Koordinaten-Fallback) → rohes `do` nur, wenn UIA das Ziel nicht
   abbildet.
2. **Fensteridentität ist Pflicht:** Ein roher Klick braucht `hwnd`, `pid` und
   exakten Fenstertitel aus `list-windows` sowie den physischen Capture-Rahmen.
   `WindowFromPoint` prüft unmittelbar vor dem Backend, welches Top-Level-Fenster
   am Zielpunkt liegt; Child-/Overlay-Handles werden mit `GA_ROOT` aufgelöst.
3. **Capture-Rahmen nicht vermischen:** Die 0..1-Koordinaten aus
   `capture --window` sind fensterlokal. Die CLI-Antwort enthält deshalb
   `window_identity` und `coordinate_frame`; genau diese Werte an `oc do`
   weitergeben. Beim MCP liefert `capture` zusätzlich eine einmalige
   `observation_id`/`screenshot_id`. Ein roher Koordinaten-Call braucht diese ID
   und einen vollständigen Deskriptor oder `window_token` aus `list_windows`.
   Genau eine Aktion darf die Observation verbrauchen; danach kommt automatisch
   eine frische `post_action_observation` zurück.
4. **Mismatch heißt Stopp:** fehlende/mehrdeutige Identität, ein nicht
   auflösbarer Punkt oder Mismatch niemals mit einem zweiten Schätzklick
   umgehen. Neu capturen bzw. UIA-Ziel neu auflösen.

Die Prüfung verhindert einen Klick in ein anderes Top-Level-Fenster. Sie kann
keine Layoutänderung innerhalb desselben Fensters erkennen; deshalb bleibt
`click_name`/`invoke` der Standard vor Koordinaten.

### MCP-Interaktionsvertrag (v0.9)

- `click_name` und `invoke` wählen exakte Namen zuerst. Mehrdeutige oder zu
  schwache Treffer werden mit Kandidatenliste abgewiesen; `exact=true` erzwingt
  Namensgleichheit. Beide verlangen einen Fensterdeskriptor oder Token aus
  `list_windows`. Erfolgsresultate enthalten Match-Typ, Score und Alternativen.
- `type`, Tastaturaktionen und `activate_window` brauchen einen ausgegebenen
  Fensterdeskriptor oder Token. Vor jedem Segment wird der Vordergrund erneut
  verglichen. Texteingaben melden `requested_chars`, `sent_chars`,
  `complete`/`partial` und den Zielfokus, aber niemals den Klartext.
- Capture-/Tree-Daten gelten nur bis zur nächsten Aktion oder Zustandsänderung.
  Bei Fokus-, Layout-, Fenster- oder Modalwechsel neu beobachten; keine alte ID
  erneut verwenden.
- Signal-Overlays haben eine harte TTL. Aktions-Calls räumen sie bei Turn-Ende,
  Fehler und Abbruch auf. Nur `keep_signal=true` hält eine Lease bewusst über
  mehrere Calls; `signal_hide` bleibt idempotent.
- Ein manuelles `signal_show` startet die konfigurierte Vorlaufphase. Das
  Overlay zeigt textlich `Start in N Sekunden`, zählt sekündlich herunter und
  verwendet bis zum Start `pre_action_grace_color`; danach wechselt es einmalig
  zur Modusfarbe. `signal_status` meldet `phase`, `countdown_seconds`, aktuelle
  Farbe und einen Screenreader-Text. Bei `0` beginnt es sofort im aktiven Modus.
- Die Vorlaufanzeige blinkt und pulsiert nicht. Der Text bleibt deshalb auch bei
  deaktivierten Windows-Animationen verständlich; Farbe ist nur redundant.

### Stop-Bedingungen

- Ziel erkennbar erreicht → Loop manuell beenden.
- `max_steps` überschritten (empfohlen: 20 Schritte) → anhalten, Status melden.
- Drei aufeinanderfolgende `deny`-Ergebnisse → anhalten, User fragen.

### Safety-Empfehlung

Standard: `confirm`-Modus (Default). Für vollautonome Schritte (kein User im Loop):
```
python -m open_compute.cli do '<json>' --yes
```
Alternativ Umgebungsvariable: `OC_SAFETY_MODE=allow_all`

---

## CLI-Kurzreferenz

```bash
# Screenshot (in _session/, kein loser Desktop-Screenshot)
python -m open_compute.cli capture
python -m open_compute.cli capture --out pfad/screenshot.png   # explizit
python -m open_compute.cli capture --monitor 1                  # Diagnose-Modus
python -m open_compute.cli capture --window "Word"              # nur Fenster-Rect (v0.5)
python -m open_compute.cli capture-series --window "Word" --max-frames 8 --stable-frames 2

# Companion/Handoff: Fenster-Mutationen brauchen Lease + Safety-Bestätigung
python -m open_compute.cli session companion --owner local-user
python -m open_compute.cli session request-control --owner agent-a --scope window:42 --ttl 60
python -m open_compute.cli session grant --lease-id LEASE_ID
python -m open_compute.cli window minimize --hwnd 42 --yes

# Headless-Kooperationskern (API, kein Live-Hook/Renderer):
# cooperative.py + human_activity.py sind nur mit injizierten Ports zu verdrahten.
# Der GetLastInputInfo-Adapter ist Single-Shot; kein Monitoring automatisch starten.

# Einzelne Aktion (Legacy — kein --label)
python -m open_compute.cli do '{"type":"mouse_move","x":0.5,"y":0.5}'
python -m open_compute.cli do '{"type":"type","text":"hello"}' --mode allow_all
# Klick: semantisch statt rohe Koordinaten
python -m open_compute.cli click-name "Speichern" --window "Word" --yes

# Einzelne Aktion mit Before|After-Composite
python -m open_compute.cli do '{"type":"key","text":"ctrl+s"}' --label "save" --yes

# Batch/Makro (Array von Aktionen)
python -m open_compute.cli do '[{"type":"mouse_move","x":0.5,"y":0.5},
  {"type":"key","text":"tab"}]' --yes
python -m open_compute.cli do '[...]' --label "my_macro" --yes
python -m open_compute.cli do '[...]' --shots each --label "my_macro" --yes

# Fenster-Vordergrund-Check
python -m open_compute.cli do '{"type":"key","text":"ctrl+s"}' \
  --ensure-foreground "Word" --yes
python -m open_compute.cli run "Ziel" --backend claude --ensure-foreground "Word"

# UIA-Feed: Elementbaum (v0.4, Windows — Extra [uia])
python -m open_compute.cli tree
python -m open_compute.cli tree --window "Datei-Explorer"   # nach Fenstertitel filtern
python -m open_compute.cli tree --max 50 --depth 8          # Element-/Tiefenlimit
# Ausgabe: JSON-Array mit name, role, rect_px, center_norm, invokable

# UIA-Feed: Klick per Name (v0.4)
python -m open_compute.cli click-name "Schließen"
python -m open_compute.cli click-name "Einfügen" --window "Word" --mode confirm
python -m open_compute.cli click-name "Datei:MenuItem" --yes  # Rolle-Filter via "name:Role"

# UIA-Feed: Click-freies Invoke (v0.4)
python -m open_compute.cli invoke "OK"
python -m open_compute.cli invoke "Übernehmen" --window "Einstellungen" --yes
# Fallback-Kette: InvokePattern -> TogglePattern -> SelectionItemPattern -> LegacyIAccessible

# Voll-Res-After-Shot + Annotierter Verifikations-Shot (v0.5)
python -m open_compute.cli click-name "OK" --yes --fullres
# Fenster-Rect-Capture (v0.5, Windows)
python -m open_compute.cli capture --window "Chrome"
# Antwort enthält zusätzlich window_identity und coordinate_frame.

# Directory-Watch-Feed (v0.5)
python -m open_compute.cli watch-dir /tmp/downloads --for 5    # 5 Sekunden sammeln
python -m open_compute.cli watch-dir /tmp/downloads --once     # einmaliger Snapshot-Diff
# Antwort: JSON-Array von Events [{name, role, src, dst}, ...]
# Ohne --for/--once: läuft bis Ctrl-C

# Autonomer Loop (Modus B — braucht API-Key)
python -m open_compute.cli run "Öffne die Einstellungen" --backend claude --max-steps 10
```

Nach Installation als Paket steht `oc` als direkter Befehl zur Verfügung:
```bash
oc capture
oc capture --window "Word"                                   # Fenster-Rect (v0.5)
oc do '{"type":"mouse_move","x":0.5,"y":0.5}' --mode allow_all
oc do '[...]' --label "batch" --yes
oc click-name "OK" --yes --fullres                           # Voll-Res (v0.5)
oc run "Ziel" --backend claude --ensure-foreground "Word"

# UIA (v0.4)
oc tree --window "Chrome"
oc click-name "Drucken" --window "Word" --yes
oc click-name "OK" --yes --fullres                           # + Voll-Res (v0.5)
oc invoke "Abbrechen" --yes

# Directory-Watch (v0.5)
oc watch-dir /tmp/downloads --for 10
oc watch-dir /tmp/downloads --once
```

---

## Umgebungsvariablen (v0.9)

| Variable | Standard | Beschreibung |
|---|---|---|
| `OC_SESSION_DIR` | `<module-root>/_session/` | Screenshot-Ausgabeordner |
| `OC_SESSION_KEEP` | `20` | Anzahl der zu behaltenden Session-Dateien |
| `OC_SAFETY_MODE` | `confirm` | Safety-Modus für `oc do` |
| `OC_ALWAYS_FOREGROUND` | `""` (falsy) | Wenn `1`: immer `activate_window` vor Aktion |
| `OC_SIGNAL_AUTO` | `off` | Session-Modus für ein Signal vor freigegebener MCP-Aktion |
| `OC_SIGNAL_TTL` | `120` | Harte maximale Sichtbarkeit eines Signal-Overlays in Sekunden |
| `OC_SIGNAL_IDLE_HIDE` | `60` | Zusätzlicher Idle-Countdown für ausdrücklich beibehaltene Auto-Signale |
| `OC_SIGNAL_GRACE_SECONDS` | `20` | Vorlaufdauer vor der ersten Aktion; `0` startet sofort |
| `OC_SIGNAL_CONFIG` | `_state/signal-config.json` | JSON mit `pre_action_grace_color`, `pre_action_grace_label` und Modusfarben |

---

## Modus B (mit API-Key)

`oc run "<ziel>" --backend claude [--max-steps N] [--model ID] [--ensure-foreground SUBSTR]`

- Braucht `ANTHROPIC_API_KEY` in der Umgebung + das Extra `[claude]` (siehe Installation).
- Loop läuft vollautonomen via `AgentLoop` + `ClaudeComputerBackend` + `LocalExecutor`.
- OpenAI-Backend (`--backend openai`) ist als `[UNSICHER]` markiert — Request-Shape nicht vollständig verifiziert.
- `--ensure-foreground SUBSTR`: einmaliger Pre-Loop-Check.

---

## Installation

**Nicht auf PyPI.** Der Name `open-compute` ist dort von einem fremden Projekt belegt — immer aus dem Repository installieren:

```bash
# Kern (zero deps)
pip install "git+https://github.com/ellmos-ai/open-compute.git"

# Mit lokalem Executor (Screenshot + Maus/Tastatur)
pip install "open-compute[local] @ git+https://github.com/ellmos-ai/open-compute.git"

# Mit Pillow (Before|After Composite-Stitching + Annotierter After-Shot)
pip install "open-compute[compose] @ git+https://github.com/ellmos-ai/open-compute.git"

# Mit watchdog (native FS-Events für Directory-Watch-Feed)
pip install "open-compute[watch] @ git+https://github.com/ellmos-ai/open-compute.git"

# Mit Claude-Backend
pip install "open-compute[local,claude] @ git+https://github.com/ellmos-ai/open-compute.git"

# Alles
pip install "open-compute[all] @ git+https://github.com/ellmos-ai/open-compute.git"

# Aus einem Klon heraus
pip install -e ".[local]"
```

---

## Verwandte Teilskills

> **Mitgelieferter Skill `open-compute-clipboard-companion`**
> (`skills/open-compute-clipboard-companion/SKILL.md`): gemeinsamer Live-Modus,
> in dem der Mensch die Oberfläche bedient und der Agent unter blauem
> `OBSERVE`-Signal ausschließlich feldbezogene Texte oder Dateipfade in die
> Zwischenablage legt.

> **Mitgelieferter Skill `open-compute-work-together`**
> (`skills/open-compute-work-together/SKILL.md`, Ticket T-20260825-767105130):
> dreiteiliger, breiterer Zuschauer-Helfer-Modus — Sichtfenster
> (`note_observation`, rauschfreier Beobachtungskanal, das Gegenstück zu
> `chat`), die Zwischenablage-Hälfte von oben (referenziert) sowie eine eng
> begrenzte Mikro-Übernahme (genau ein `type`-Aufruf auf ein bereits vom
> Menschen fokussiertes Feld, sofortige Rückgabe).

> **Externer Skill `clirec`** (`https://github.com/ellmos-ai/clirec`): Aufnahmekanal — Maus/Tastatur-Demos als `.clirec` aufnehmen und adaptiv abspielen. In open-compute bleibt `oc rec` als lazy geladener Kompatibilitäts-Shim.
