# open-compute Skill — Mode A: Session-Agent als Reasoner

**Skill-ID:** `open-compute`
**Version:** 0.5.0
**Modus:** A — OHNE API-Key, Session-Modell als Reasoner, manuelles Stepping
**Voraussetzungen:** `pip install open-compute[local]` (mss muss installiert sein); Windows-Host; `oc` CLI aufrufbar via `python -m open_compute.cli`

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

**Koordinaten** `x`, `y`, `end_x`, `end_y`: **normalisiert 0..1** (0,0 = oben-links, 1,1 = unten-rechts des virtuellen Desktops). NICHT Pixel.

### Beispiele

```json
{"type": "left_click", "x": 0.5, "y": 0.25}
{"type": "type", "text": "Hallo Welt"}
{"type": "key", "text": "ctrl+s"}
{"type": "scroll", "x": 0.5, "y": 0.5, "scroll_direction": "down", "scroll_amount": 3}
{"type": "left_click_drag", "x": 0.1, "y": 0.1, "end_x": 0.9, "end_y": 0.9}
{"type": "mouse_move", "x": 0.5, "y": 0.5}
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

   **Einzelne Aktion (Legacy, kein Label):**
   ```
   python -m open_compute.cli do '<action-json>' [--yes]
   ```
   Antwort: `{"result": "executed", "action": "...", "width": W, "height": H}`

   **Einzelne Aktion mit Before|After-Composite (`--label`):**
   ```
   python -m open_compute.cli do '<action-json>' --label "click_save" --yes
   ```
   Antwort: `{"result": "executed", ..., "composite": "_session/0001_click_save.png"}`
   (oder `"before"` / `"after"` wenn Pillow nicht installiert ist — graceful degrade)

   **Batch/Makro (JSON-Array):**
   ```
   python -m open_compute.cli do '[{"type":"mouse_move","x":0.5,"y":0.5},
     {"type":"left_click","x":0.5,"y":0.3}]' --yes
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

5. **RECAPTURE** — Zurück zu Schritt 1.
   - Alternativ: After-Shot aus Composite direkt lesen (`"composite"` oder `"after"` im Ergebnis-JSON) → ein Roundtrip gespart.

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

## CLI-Kurzreferenz (v0.5)

```bash
# Screenshot (in _session/, kein loser Desktop-Screenshot)
python -m open_compute.cli capture
python -m open_compute.cli capture --out pfad/screenshot.png   # explizit
python -m open_compute.cli capture --monitor 1                  # Diagnose-Modus
python -m open_compute.cli capture --window "Word"              # nur Fenster-Rect (v0.5)

# Einzelne Aktion (Legacy — kein --label)
python -m open_compute.cli do '{"type":"mouse_move","x":0.5,"y":0.5}'
python -m open_compute.cli do '{"type":"left_click","x":0.5,"y":0.5}' --yes
python -m open_compute.cli do '{"type":"type","text":"hello"}' --mode allow_all

# Einzelne Aktion mit Before|After-Composite
python -m open_compute.cli do '{"type":"left_click","x":0.5,"y":0.3}' --label "click_ok" --yes

# Batch/Makro (Array von Aktionen)
python -m open_compute.cli do '[{"type":"mouse_move","x":0.5,"y":0.5},
  {"type":"left_click","x":0.5,"y":0.3}]' --yes
python -m open_compute.cli do '[...]' --label "my_macro" --yes
python -m open_compute.cli do '[...]' --shots each --label "my_macro" --yes

# Fenster-Vordergrund-Check
python -m open_compute.cli do '{"type":"left_click","x":0.5,"y":0.3}' \
  --ensure-foreground "Word" --yes
python -m open_compute.cli run "Ziel" --backend claude --ensure-foreground "Word"

# UIA-Feed: Elementbaum (v0.4, Windows — pip install open-compute[uia])
python -m open_compute.cli tree
python -m open_compute.cli tree --window "Datei-Explorer"   # nach Fenstertitel filtern
python -m open_compute.cli tree --max 50 --depth 8          # Element-/Tiefenlimit
# Ausgabe: JSON-Array mit name, role, rect_px, center_norm, invokable

# UIA-Feed: Klick per Name (v0.4)
python -m open_compute.cli click-name "Schliessen"
python -m open_compute.cli click-name "Einfuegen" --window "Word" --mode confirm
python -m open_compute.cli click-name "Datei:MenuItem" --yes  # Rolle-Filter via "name:Role"

# UIA-Feed: Click-freies Invoke (v0.4)
python -m open_compute.cli invoke "OK"
python -m open_compute.cli invoke "Uebernehmen" --window "Einstellungen" --yes
# Fallback-Kette: InvokePattern -> TogglePattern -> SelectionItemPattern -> LegacyIAccessible

# Voll-Res-After-Shot + Annotierter Verifikations-Shot (v0.5)
python -m open_compute.cli do '{"type":"left_click","x":0.5,"y":0.3}' --yes --fullres
# Antwort: {"result":"executed",...,"fullres":"_session/...fullres.png"}
# Mit Pillow: {"fullres_annotated":"..."} (roter Kreis + Fadenkreuz am Klickpunkt)
python -m open_compute.cli click-name "OK" --yes --fullres
# Fenster-Rect-Capture (v0.5, Windows)
python -m open_compute.cli capture --window "Chrome"
# Antwort: {"path":"...","width":W,"height":H,"window":"Chrome","region":{...}}

# Directory-Watch-Feed (v0.5)
python -m open_compute.cli watch-dir /tmp/downloads --for 5    # 5 Sekunden sammeln
python -m open_compute.cli watch-dir /tmp/downloads --once     # einmaliger Snapshot-Diff
# Antwort: JSON-Array von Events [{name, role, src, dst}, ...]
# Ohne --for/--once: läuft bis Ctrl-C

# Autonomer Loop (Modus B — braucht API-Key)
python -m open_compute.cli run "Oeffne die Einstellungen" --backend claude --max-steps 10
```

Nach Installation als Paket steht `oc` als direkter Befehl zur Verfügung:
```bash
oc capture
oc capture --window "Word"                                   # Fenster-Rect (v0.5)
oc do '{"type":"mouse_move","x":0.5,"y":0.5}' --mode allow_all
oc do '[...]' --label "batch" --yes
oc do '{"type":"left_click","x":0.5,"y":0.3}' --yes --fullres   # Voll-Res (v0.5)
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

## Umgebungsvariablen (v0.5)

| Variable | Standard | Beschreibung |
|---|---|---|
| `OC_SESSION_DIR` | `<module-root>/_session/` | Screenshot-Ausgabeordner |
| `OC_SESSION_KEEP` | `20` | Anzahl der zu behaltenden Session-Dateien |
| `OC_SAFETY_MODE` | `confirm` | Safety-Modus für `oc do` |
| `OC_ALWAYS_FOREGROUND` | `""` (falsy) | Wenn `1`: immer `activate_window` vor Aktion |

---

## Modus B (mit API-Key)

`oc run "<ziel>" --backend claude [--max-steps N] [--model ID] [--ensure-foreground SUBSTR]`

- Braucht `ANTHROPIC_API_KEY` in der Umgebung + `pip install open-compute[claude]`.
- Loop läuft vollautonomen via `AgentLoop` + `ClaudeComputerBackend` + `LocalExecutor`.
- OpenAI-Backend (`--backend openai`) ist als `[UNSICHER]` markiert — Request-Shape nicht vollständig verifiziert.
- `--ensure-foreground SUBSTR`: einmaliger Pre-Loop-Check.

---

## Installation

```bash
# Kern (zero deps)
pip install open-compute

# Mit lokalem Executor (Screenshot + Maus/Tastatur)
pip install open-compute[local]

# Mit Pillow (Before|After Composite-Stitching + Annotierter After-Shot)
pip install open-compute[compose]

# Mit watchdog (native FS-Events für Directory-Watch-Feed)
pip install open-compute[watch]

# Mit Claude-Backend
pip install open-compute[local,claude]

# Alles
pip install open-compute[all]
```

---

## Verwandte Teilskills

> **Teilskill `clirec`** (`skills/clirec/SKILL.md`): Aufnahmekanal — Maus/Tastatur-Demos als `.clirec` aufnehmen und adaptiv abspielen, um schwere Abläufe vorzumachen.
