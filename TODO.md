# TODO

## Vergleich mit AB498/computer-control-mcp (2026-07-12)

Fremdserver (153 Sterne, PyAutoGUI + RapidOCR) gegen unseren Kern verglichen.
Architektonisch bleibt unser Weg (semantisches UIA-Zielen, Safety-Gate,
normierte Koordinaten) vorn; drei Dinge konnten sie besser und sind übernommen:

- [x] **WGC im Fenster-Capture verdrahtet.** Ein GDI-Grab eines
      hardware-komponierten Fensters (Roblox Studio, Blender, GPU-Browser)
      scheitert nicht — es liefert still ein schwarzes Rechteck. `capture(window=…)`
      prüft den Frame jetzt und holt ihn bei Leere über Windows.Graphics.Capture.
      Dabei fiel ein Aufhänger im bestehenden WGC-Backend auf (Watchdog ohne
      Handle → blockierte unbegrenzt), ebenfalls gefixt (`start_free_threaded`).
- [x] **`list_windows` + `get_screen_size` als MCP-Tools** — der Reasoner musste
      Fenstertitel bisher raten.
- [x] **Halte-Primitive** `mouse_down`/`mouse_up`/`key_down`/`key_up` inkl.
      Held-State-Tracking und `release_all()` (Server gibt beim Beenden frei).
- [ ] **(bewusst NICHT übernommen) OCR-Feed.** Sie liefern RapidOCR + ONNXRuntime
      (~70 MB Erstdownload) mit. Der UIA-Elementbaum löst native Apps besser; falls
      OCR nötig wird, ist `Windows.Media.Ocr` der dependency-freie Weg (so auch in
      `ARCHITECTURE.md` Feed ③ vorgesehen).
- [ ] **(offen, mittel)** Vollbild-`capture()` erkennt kein Schwarzbild: ist nur ein
      *Teil* des Desktops ein GPU-Fenster, bleibt der Rest sichtbar und die
      Blank-Heuristik greift nicht. Bis dahin gilt für solche Fenster
      `capture(window=…)`. Denkbar: `OC_CAPTURE_BACKEND=wgc` als Zwang.
- [ ] **(offen, niedrig)** Cross-Platform-Ausführung: sie decken über PyAutoGUI auch
      macOS/Linux ab, unser `LocalExecutor` ist Windows-only.

## Review 2026-07-04 (Modul-Review-Loop Lauf 6, frischer Subagent — HOCH-Funde gefixt)

- [x] **(hoch)** `oc rec replay` umging das Safety-Gate komplett (roher
      LocalExecutor, kein Confirm/Deny/Audit) → `_GatedExecutor` +
      `--mode`/`--yes` für `oc rec`, Default confirm.
- [x] **(hoch)** `--ensure-foreground` im Batch-/Label-Pfad lief VOR der
      Policy-Auswertung (realer Fokus-Wechsel trotz read_only) → deferred bis
      erste Aktion das Gate passiert hat. Beide: `tests/test_safety_gating_fixes.py`.
- [x] Test-Collection brach ohne optionales clirec-Paket (harter Import nach
      der Extraktion) → importorskip + Sibling-Checkout-Pfad; mss-Test ebenso.
- [x] **(Folge, mittel)** `_state/outcomes.jsonl` + `lessons.jsonl` wachsen
      nicht mehr unbegrenzt. Erledigt 2026-07-13: beide JSONL-Logs werden nach
      dem Append auf eine begrenzte Tail-Historie gekappt; `weights.json` und
      `profiles.json` schreiben dabei ebenfalls atomar.
- [x] **(Folge, mittel)** `oc watch-dir --once`: Snapshot-Store für
      `_session/dirwatch_snapshot.json` schreibt jetzt gelockt und atomar.
      Erledigt 2026-07-13: bestehende Path-Set-Schlüssel bleiben erhalten,
      Snapshot-Datei wird per Temp-Datei + `os.replace` aktualisiert.
- [ ] **(Folge, niedrig)** Deny-Liste kennt nur ActionType — keine
      Ziel-String-Regeln (z. B. `TYPE`-Inhalte, `LAUNCH_APP`-Namen).

## STATUS

| Category | Status | Notes |
|---|---|---|
| Tests | PASS | `python -X utf8 -m pytest -q` green from the module root — 360 pass, 1 skipped (after the 2026-06-27 OpenAI-backend verification + 3 new tests); GitHub Actions now runs the mock-only suite on push/PR. |
| Import check | PASS | `python -c "import open_compute; import open_compute.feed_manager; import open_compute.learning"` — OK, zero extras. |
| Documentation | READY | README (EN + DE), llms.txt, CHANGELOG, SECURITY, ARCHITECTURE present. |
| Integration | DEVELOPMENT | Fits `.MODULES` as a standalone module. Contains marked stubs/interfaces (see below). |

## Fully implemented + tested (v0.2.0)

- Canonical action schema + `to_claude` / `to_openai` mappers.
- Coordinate normalize / denormalize / rescale.
- Safety policy gate (confirm / allow_all / read_only, deny list, callback, audit).
- Config dataclass + JSON loader.
- Agent loop orchestrator (dry-run via mocks).
- Backend dispatch via factory; MockBackend; ClaudeComputerBackend (tested with
  an injected fake client).
- **`LocalExecutor`** (Windows) — real screenshot (mss) + real input (ctypes
  SendInput), VIRTUALDESK multi-monitor, DPI-aware, all action types dispatched.
  Live-tested: `oc capture` → 368 KB PNG; `oc do mouse_move` → cursor moved.
- **`oc` CLI** — `oc capture` / `oc do` / `oc run` with Safety gate wiring.
- **`SKILL.md`** — Mode A loop protocol for session-agents (no API key).
- **31 new tests** — coordinate math, Win32 dispatch (mocked), CLI parsing.

## Interface / stub (honest status)

- [x] **OpenAI backend** -- verified against the live OpenAI computer-use docs
  (2026-06-27): default model updated `computer-use-preview` → `gpt-5.5` (also
  `gpt-5.4`), tool type updated `computer_use_preview` → `computer` (now
  constructor-configurable; legacy shape with display dims kept for
  `tool_type="computer_use_preview"`), screenshot output now sends
  `detail: "original"`. Added injected-client tests (request shape + click
  parsing + legacy path). **Live end-to-end smoke with a real key remains
  deferred to the user** (see RELEASE_GATE.md / STATUS).
- [ ] **Browser driver** -- interface only. Implement a Playwright/CDP driver.
- [ ] **Set-of-Marks perception** -- stub. Wire in OmniParser V2 (note: icon_detect
  weight is AGPL; use as external service or choose pywinauto for accessibility).
- [x] **Accessibility perception (Windows UIA)** -- `feeds/uia_windows.py`
  (UiaWindowsFeed + UiaTargeter). `feeds/base.py` (PerceptionFeed + Targeter
  protocols). `feeds/registry.py` (capability detection). Implemented v0.4.0.
  - [ ] **BUG → Fix gebaut, Live-Verify offen (wartet auf User-bestätigten Smoke):**
    `--window "<name>"` grenzt die Suche NICHT auf das benannte Fenster ein → `oc tree
    --window "Schnitzeljagd"` lieferte die **Taskleiste** statt Word; `invoke "Start"` traf den
    Taskleisten-Startbutton; "Einfügen"/"Layout" nicht gefunden. UIA-Fähigkeit selbst OK.
    **FIX (v0.4.1, 2026-06-20):** `_get_root()` wirft `RuntimeError` wenn kein Top-Level-Fenster
    passt (kein stiller Desktop-Root-Fallback mehr). Matching case-insensitive +
    Whitespace-normalisiert (Doppel-Leerzeichen im Word-Titel). Default-Pfad via
    `GetForegroundWindow()` → `ControlFromHandle(hwnd)`. 26 neue Unit-Tests grün (keine Live-Calls).
    **Offen: echter Windows-Live-Smoke als Abnahmekriterium.**
- [ ] **DOM-snapshot perception** -- stub. Wire in Playwright accessibility snapshot.
- [ ] **`oc run` live-key test** -- not tested (no API key in build environment).
  The wiring is complete; LocalExecutor + ClaudeBackend connect end-to-end.

## Automation & UX Roadmap (v0.3) — aus Live-Test-Feedback 2026-06-20

Ziel: **weniger Modell-Mikromanagement**, kein manuelles Capture/Schätzen pro Schritt,
keine losen Screenshot-Dateien. (Live-Test Modus A funktionierte, war aber „schleppend".)

- [x] **Screenshots immer in Modul-`_session/`** (gitignored), nie lose im Desktop/CWD.
  Capture-Default-Out = `_session/` mit Zeitstempel/Sequenznummer; alte rotieren/aufräumen.
  → Implementiert v0.3.0: `_session_dir()`, `_next_session_path()`, `_rotate_session()`; `OC_SESSION_DIR`/`OC_SESSION_KEEP`.
- [ ] **Live-Bild-Modus** `oc watch` — Hintergrundprozess schreibt `_session/live.png`
  (immer gleicher Name) ~1×/Sekunde. Beim Abruf ist der Stand ~aktuell (Pull entfällt).
- [x] **Auto-Shot um jede Aktion** `oc do --label "<name>"` — Shot VOR der Aktion + Shot
  DANACH werden softwareseitig zu EINEM beschrifteten Bild zusammengesetzt
  (Vorher | Nachher), Dateiname = Aktions-Label. Ein Aufruf liefert die fertige Verifikation.
  → Implementiert v0.3.0: `_compose_before_after()`, Pillow lazy, graceful degrade ohne Pillow.
- [x] **Makro/Batch** — `oc do` akzeptiert eine Aktions-Sequenz (Liste/Skriptdatei) in EINEM
  Aufruf, optional ein Capture nur am Ende. Reduziert Roundtrips drastisch.
  → Implementiert v0.3.0: `_parse_actions()`, JSON-Array-Input, `--shots each`, Safety-Gate pro Aktion.
- [ ] **Semantisches Zielen** — Zielpunkt-Erkennung via Set-of-Marks/OCR/Accessibility:
  Modell nennt ein Ziel ("Button Einfügen") statt Pixelkoordinaten; System ermittelt
  Ausgangspunkt + Strecke und führt die Bewegung selbst aus (Bewegungen automatisiert).
- [ ] **Automatisches OCR-Text→Pixel/Ort-Mapping** (konkrete Umsetzung des semantischen
  Zielens): Beim Capture wird der Screen geOCRt und eine Text→Bounding-Box-Karte gebaut.
  Das Modell bekommt nur den Text (Liste erkannter UI-Texte) geliefert und sagt z. B.
  „klick auf Einfügen" → System schlägt „Einfügen" in der Karte nach, führt die Maus zur
  Box-Mitte und klickt. Modell muss KEINE Pixel mehr schätzen.
  - Mögliche OCR-Quellen (Lizenz prüfen): Tesseract via pytesseract (Apache-2.0),
    EasyOCR (Apache-2.0), Windows.Media.Ocr via winrt; ggf. Reuse aus
    `.SOFTWARE/ENTERTAINMENT/DEV_USBPodcastStudio/ocr/` (MIT).
  - Output idealerweise als annotierte Karte: `{text, box, center_norm}` je Treffer;
    Mehrdeutigkeit (mehrfach gleicher Text) → Disambiguierung über Region/Index.
  - Kombinierbar mit Accessibility (UIA-Elementnamen) als robusterem Zweitkanal.
- [x] **Fenster-Vordergrund-Check vor Aktion** — automatisch prüfen, ob das Zielfenster im
  Vordergrund ist (Live-Bild/Win32); wenn nötig vorher `activate_window` senden
  (Option `--ensure-foreground "<Fenster>"`, oder konfigurierbar „immer").
  → Implementiert v0.3.0: `_get_foreground_title()`, `_should_activate()`, `--ensure-foreground`,
  `OC_ALWAYS_FOREGROUND`, `Config.always_foreground`.
- [ ] **Prozess-Persistenz** — dauerhafter `oc`-Worker/Daemon statt Python-Neustart pro
  `oc do` (senkt Aktions-Latenz spürbar).
- [ ] **Annotierte After-Shots** — Klick-Koordinate als Marker ins Nachher-Bild zeichnen
  (Verifikation auf einen Blick).

### Retest-Befunde (Live, 2026-06-20) — Phase 1 empirisch geprüft

Word-Retest mit Batch + Composite + `--ensure-foreground`:
- [x] **Batch in EINEM Aufruf funktioniert** (wait+key+type, `count:3`) — deutlich weniger Roundtrips.
- [x] **Composite in `_session/` funktioniert** (Before|After, ein Bild, nicht lose).
- [x] **`--ensure-foreground` funktioniert** (vom User am Bildschirm bestätigt, Primärquelle):
  Word kam in den Vordergrund. Mein Erstverdacht „flaky/SetForegroundWindow-Lock" war FALSCH —
  der nachträgliche Screenshot wirkte nur irreführend, weil **Word als kleines, nicht maximiertes
  Fenster** im Vordergrund stand (Terminal dahinter sichtbar). Keine Foreground-Reparatur nötig.
- [x] **Composite-Auflösung zu niedrig zum Lesen:** IMPLEMENTIERT v0.5.0 — `oc do --fullres` /
  `oc click-name --fullres` speichern zusätzlich einen vollen Voll-Res-After-Shot in `_session/`.
  Pillow zeichnet Klick-Koordinaten-Marker (roter Kreis + Fadenkreuz) ein wenn verfügbar.
  JSON-Schlüssel: `"fullres"` (ohne Marker) oder `"fullres_annotated"` (mit Marker).
  **Live-Verify: OFFEN.**
- [x] **Kleine/nicht-maximierte Zielfenster** — `oc capture --window SUBSTR` IMPLEMENTIERT v0.5.0:
  capturt nur das Bounding-Rect des benannten Fensters (Win32 `GetWindowRect` via HWND).
  Fenster-Auflösung: `EnumWindows` + case-insensitiv + Whitespace-normalisiert.
  JSON-Response enthält `"window"`, `"region"`, `"width"`, `"height"`.
  **Live-Verify: OFFEN.**

### Neue Feeds & Bereitstellung — Reuse-Quellen (2026-06-20, aus Langtest-Brainstorm)

- [x] **Feed ⑥ Directory-/Filesystem-Watch** — IMPLEMENTIERT v0.5.0.
  `DirwatchFeed` in `feeds/dirwatch.py`: watchdog (MIT) als native Backend + stdlib-Polling-Fallback.
  `oc watch-dir <path> [--for SECS] [--once]` CLI. Immer `available()=True`.
  Move-Detection (unambiguous delete+create → "moved"). `snapshot_diff()` für --once.
  Offen: `oc watch-dir` ohne --for/--once (Ctrl-C-Modus) — Live-Verify ausstehend.
- [x] **Push statt Pull — Auto-Injektion der Feeds** — IMPLEMENTIERT v0.6.0.
  `feed_manager.py`: FeedManager + InjectorSink-Protokoll + LocalFileInjector (funktionierend)
  + BachInjectorAdapter (Stub, dokumentiert — BACH nicht sauber importierbar, Fallback auf Datei).
  Dosierung pro Feed (full/delta/notify/off), Change-Detection (Hash State-Feeds, Rolling-Window
  Event-Feeds), `set_dosage()` für LLM-Self-Tuning, `on_demand_full()` für Pull-on-Notify.
  `oc push --status` / `oc push --once` CLI. 71 neue Unit-Tests (alle grün).
- [ ] **Schriftzug via freies Zeichnen / Linien (offen, aus Langtest):** „Zeichnen"-Tab ist in diesem
  Word NICHT aktiviert; Linien/Freihand über Formen→Linien scheitern aktuell an dichter
  Dropdown-Navigation auf Composite-Auflösung. Braucht: Voll-Res/annotierte Shots ODER „Zeichnen"-Tab
  aktivieren ODER tieferes UIA. Vorteil Pen: bleibt nach Wahl aktiv → mehrere `left_click_drag`-Striche
  (z. B. ein „W" aus 4 Strichen) ohne Neuauswahl möglich.

### Erweiterte Perception & Auto-Erkennung (v0.3/v0.4 — Ideen 2026-06-20)

Leitidee: mehrere Wahrnehmungs-Kanäle (Pixel / OCR / lokales Vision-Modell / Accessibility-UIA)
speisen ein gemeinsames „Weltbild", das der Agent liest — statt reinem Pixel-Raten.

- [ ] **Direktes visuelles Zielen bleibt möglich** — für Nicht-Text-/Grafikelemente schaut das
  Modell weiterhin selbst aufs Bild und gibt Koordinaten. OCR/semantisches Zielen ist ein
  ZUSÄTZLICHER Kanal, kein Ersatz.
- [ ] **Kleines lokales Vision-Modell im Workflow** — schlankes OSS-Bildmodell, dessen einzige
  Aufgabe das laufende Auswerten der Live-Bilder ist und das beschreibt, was es sieht.
  Kandidaten prüfen (Größe/Latenz/Lizenz, lokal/Ollama): Moondream2, Florence-2, MiniCPM-V,
  kleine Qwen-VL. Liefert kontinuierliche Szenen-Beschreibung.
- [ ] **Live-Log mit Change-Detection** — solange sich das Bild nicht ändert, bleibt der
  Log-Eintrag gleich; bei Änderung (Hash/Diff) neuer Eintrag (z. B. „Fensterwechsel erkannt").
  Baut auf Live-Bild-Modus + Vision-Modell/OCR → günstiger „Was passiert gerade"-Feed.
- [ ] **Statische Elemente als System-Chrome erkennen** — was von Bild zu Bild gleich bleibt,
  als Systemelement identifizieren (Uhrzeit/Datum unten rechts, Taskleiste, Fensterrahmen) und
  vom eigentlichen Inhalt trennen.
- [ ] **Häufige Fenster-Buttons automatisch in die Pixel-Map** — Schließen / Minimieren /
  Maximieren / Wiederherstellen automatisch erkennen und als benannte Ziele anbieten.
- [ ] **App-Icon ↔ Programmname-Mapping** — sichtbare Icons (Taskleiste/Desktop) erkennen und
  gegen eine Icon→Name-Karte auflösen → „sichtbar: Word, Excel, …". Modell sagt „Word öffnen"
  → System führt Doppelklick auf das Word-Icon aus. (Robustere Alternative: Startmenü-Suche /
  `launch_app`.)
- [ ] **Fenster verschieben — sichere Greifstelle** — leere Stelle der Titelleiste/des Rahmens
  automatisch erkennen, an der man gefahrlos zum Drag-Move ansetzen kann; alternativ
  Tastenkombinationen (Win+Pfeil-Snap, Alt+Leertaste-Systemmenü).
- [x] **Windows-Barrierefreiheit anzapfen** — IMPLEMENTIERT v0.4.0 (Phase 2a).
  `UiaWindowsFeed` + `UiaTargeter` in `feeds/uia_windows.py` (uiautomation MIT).
  Validiert 2026-06-20 (Live-Probe): `oc tree --window "Datei-Explorer"` liefert
  Element-Baum mit center_norm; `oc click-name`/`oc invoke` Safety-Gate greift korrekt.
  Dokumenttext via TextPattern, Ribbon-Tab Name→Klick-Mitte, InvokePattern-Fallback-Kette.

## Usage pattern — host-model context delegation: inline (a) vs. self-subagent (b)

> **Pattern / doc, NOT a new reasoning backend.** Same host model (e.g. Claude Code on a
> subscription) — same vision, same reasoning, no API key — runs the computer-use loop either
> **inline** or in a **self-spawned subagent**. The only difference is **context economy**, not
> capability. Full design: `ARCHITECTURE.md` → "Host-Modell-Kontext: Inline (a) vs.
> Selbst-Subagent (b)". The model decides per task (decision = (a) with option on (b)).

- [x] **(a) Inline mode** — host model drives `oc capture` / `oc do` in its own context.
  **Already exists** (Mode A, `cli.py` + `SKILL.md`). Good for short/simple tasks.
- [ ] **(b) Self-subagent mode (CONCEPT / pattern)** — host model spawns a subagent *of itself*
  (e.g. via `Task`) that runs the full capture→do→recapture loop in the subagent's context
  (preprocessing) and returns only the distilled result → main context stays clean, "feels like
  API", **no reasoning/vision loss** (it is the same model). Documented as a usage pattern in
  README (EN/DE) + SKILL.md; heuristic: short→inline, long/repeated/context-heavy→subagent.
  No automatic switch in code; the model decides (like normal subagent delegation).
- [ ] **Persistent 24h experience-subagent (OPTION on b, CONCEPT)** — long-lived self-subagent +
  job queue; experience accumulated via the existing `learning.py`
  (`log_outcome`/`BetaPrior`/profiles/lessons in `_state/`) and dosed into later jobs. Experience
  lives in `_state/` (persistent), not in the volatile subagent context → rotation keeps warmstart.
  - [ ] **Lessons with decay/confidence** (against false lessons): add timestamp-decay +
    confidence (from `BetaPrior` sample count) to `Lesson`. Small additive change — NOT
    implemented yet (`Lesson` already has `ts`).
  - Safety: isolated VM, allow/deny list, `max_steps` + timeout, escalate on repeated failure.
- [ ] **Separate, low-priority, optional — foreign/local reasoner (NOT (b))** — a *different*
  model as reasoner (local Ollama, or agy/codex/kimi CLIs) would be a real new `ComputerBackend`
  (reasoning source changes, possible capability/vision difference), attachable via the existing
  `ComputerBackend` Protocol + `get_backend()` factory. Explicitly **not** the self-subagent mode.
  Recorded, not scheduled.

## Backlog

- [ ] Live smoke test against a real Claude key in an isolated VM.
- [x] GitHub Actions CI: run mock-only tests on push.
- [ ] Banner / logo asset for README.
- [ ] OpenAI backend: add injected-client test + verify Responses-API shape.
- [ ] macOS / Linux executor: port `LocalExecutor` to Quartz / X11 / xdotool.

## clirec — externer Aufnahmekanal

`clirec` ist jetzt ein eigenes Repo/Paket: https://github.com/ellmos-ai/clirec.
`open-compute` behält nur den lazy geladenen `oc rec`-Shim und alte Import-Wrapper.
Neue Recorder-/Ringpuffer-/Pause-Hotkey-/Replay-Arbeit gehört in das `clirec`-Repo.
