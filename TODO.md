# TODO

## Feature-Backlog (auch für code-naive Umsetzer geeignet)

- [ ] **Safety-Coverage-Test.** Ein Test, der beweist, dass JEDER
  zustandsverändernde MCP-Tool-Handler das Safety-Gate durchläuft (schützt vor
  Gate-Umgehungen wie dem historischen `oc rec replay`-Befund). Wo:
  `tests/test_mcp_server.py` (Muster vorhanden: `_tool_names()` listet alle
  Tools). Wie: aus `mcp_server.py` die Handler-Ebene prüfen — z. B. Quelltext-
  Analyse der registrierten Tool-Funktionen (jede muss `_gate(...)` oder
  `policy.evaluate(...)` aufrufen) oder Laufzeit-Test mit Spy-Policy.
  Abnahme: Test schlägt fehl, sobald ein neuer mutierender Tool-Handler ohne
  Gate-Aufruf registriert wird; bestehende Liste bleibt grün.
- [ ] **Panic-Hotkey mit Prozess-Teardown.** Globaler Kill-Schalter
  (z. B. Strg+Alt+K), der sofort alle Eingaben freigibt (`release_all`), das
  Signal-Overlay ausblendet und den Server kontrolliert beendet — ergänzt den
  Abort-Hotkey (der einen Grund ans Modell gibt; der Panic-Hotkey stoppt ohne
  Rückfrage). Wo: `open_compute/mcp_server.py` (Server-Lifetime,
  `_release_held_input` existiert bereits als Vorlage) und ggf. Wiederverwendung
  von `parse_hotkey` aus `open_compute/indicator.py`. Abnahme: Unit-Test mit
  injiziertem Hotkey-Listener zeigt release→hide→stop-Reihenfolge;
  Doku-Eintrag in README (EN+DE).
- [ ] **OCR-Feed via Windows.Media.Ocr.** OCR-Wahrnehmung ohne neue
  Dependency — Windows-eigene OCR-API per PowerShell aufrufen (entspricht dem
  langjährigen Hinweis „Windows.Media.Ocr ist der dependency-freie Weg" in
  `ARCHITECTURE.md`). Wo: neuer Feed `open_compute/feeds/ocr_windows.py` nach
  dem Muster von `feeds/uia_windows.py` (Protocol `PerceptionFeed` in
  `feeds/base.py`). Output: Text + Bounding-Boxen → Grundlage fürs
  „Text→Pixel-Mapping" (siehe bestehender TODO-Punkt „Automatisches
  OCR-Text→Pixel/Ort-Mapping"). Abnahme: Unit-Tests mit injizierter
  PowerShell-Antwort (kein Live-Call in CI); `available()` False außerhalb
  Windows; Live-Smoke dokumentiert.
- [ ] **`capture_when_stable`-Settle.** Hilfsfunktion, die Screenshots pollt,
  bis das Bild ~250 ms unverändert ist (kleiner Fingerprint +
  Mean-Diff-Schwelle), statt fester Sleeps nach Aktionen. Wo:
  `open_compute/adaptive_capture.py` erweitern (bestehende
  `capture_until_stable` nutzt exakte Frame-Gleichheit — das Settle ergänzt
  eine tolerante Mean-Diff-Variante). Abnahme: Unit-Tests mit Frame-Generator
  (ändert sich → wird stabil → Timeout); keine neuen Dependencies.
- [ ] **Gap-basierte Latenz-Timing-Messung.** Pro Tool-/CLI-Aufruf
  Phasen-Timings (Capture, UIA-Baum, Gate, Aktion) als JSONL loggen +
  Auswerteskript — deckt den TODO-Punkt „Timing- und Diagnose-Feld pro Aufruf"
  (2026-07-25) ab. Wo: optional schaltbar (Env `OC_TIMING=1`), Log nach
  `_state/timing.jsonl`; Auswertung als `oc timing-report` oder kleines
  Skript. Abnahme: bei aktiviertem Flag entstehen JSONL-Zeilen mit
  Phasen-Feldern; ohne Flag null Overhead; Unit-Test prüft Log-Schema.
- [ ] **Zielbezogene Capture→Identität→Klick→Diff-Transaktion.** Die
  Pre-Click-Seite ist seit 2026-08-20 fail-closed (`WindowFromPoint`,
  Child→`GA_ROOT`, HWND/PID/Titel, expliziter Capture-Rahmen). Offen bleibt
  eine belastbare Erfolgsbewertung *nach* dem Klick. Ein SHA-256-Vergleich des
  Vollbilds ist ungeeignet: Uhr, Animationen und fremde Fenster erzeugen
  Änderungen ohne Zielerfolg; ein unverändertes Bild kann umgekehrt bei
  unsichtbarer Zustandsänderung auftreten. Vor Umsetzung zuerst die bereits
  dokumentierte gesplittete GDI→WGC-Fensteraufnahme aus CLI/MCP als eine
  robuste Capture-by-HWND-Funktion in `drivers` zentralisieren und danach eine
  Settle-/Diff-Policy (Zielrechteck, Toleranz, Timeout) definieren. Abnahme:
  gemockte Transaktion protokolliert Vorher-/Nachher-Hash des identischen HWND,
  klassifiziert `changed`/`unchanged`/`unverifiable` und behauptet bei
  `unverifiable` keinen Erfolg.

## Spezifikation: getrennter sichtbarer LLM-Zeiger (T-20260827-586759665)

**Entscheidung dieses Schnitts:** Noch keine produktive Mausautomation. Der
LLM-Zeiger wird als eigene virtuelle Eingabequelle spezifiziert. Seine Position
und sein Zustand sind unabhängig vom physischen Nutzerzeiger; ein bloßes
Bewegen des LLM-Zeigers ist nur eine Overlay-/Planungsoperation. Der bestehende
`ScreenSignalIndicator` ist kein solcher Zeiger: Sein Ring folgt derzeit per
`GetCursorPos` dem physischen OS-Zeiger und zeigt Eigentum/Modus an.

### Empirische Anschlussverträge

- `actions.py` speichert Punkte normalisiert; `LocalExecutor` transformiert sie
  mit Per-Monitor-v2-DPI und `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`
  in `SendInput`. Dieser bestehende Pfad bewegt den OS-Zeiger und darf nicht als
  virtuelle Bewegung wiederverwendet werden.
- `session.py` liefert explizite, räumlich und zeitlich begrenzte
  `ControlLease`s; menschliche Aktivität entzieht die Lease und pausiert.
  `human_activity.py` erfasst nur Zeitstempel und unterscheidet menschliche,
  eigene und unbekannte Aktivität ohne Hooks oder Rohdaten.
- `interaction.py` stellt prozessgebundene Fensterdeskriptoren sowie einmalige
  Observation-/Window-Token bereit und prüft Fokus vor jedem Textsegment.
  `preclick.py` bindet koordinatenbasierte Mausaktionen unmittelbar vor dem
  Dispatch an HWND, PID, Titel und den physischen Capture-Rahmen.
- `cooperative.py` verlangt `perceive → stabilize → act → verify`, verbietet
  Screen-/Unknown-Instruktionen als Kontrollautorität, wiederholt keine
  ungewisse Aktion und schreibt ein inhaltssanitisiertes SHA-256-Kettenaudit.
- `feeds/uia_windows.py` kann eindeutige Controls über Invoke-, Toggle-,
  SelectionItem- oder LegacyIAccessible-Pattern semantisch aktivieren, ohne den
  physischen Zeiger zu bewegen. `BrowserDriver` ist derzeit nur eine
  Schnittstelle; eine Browserumsetzung ist ein eigener Folgeschritt.

### Normatives Zustands- und Herkunftsmodell

Die Implementierung führt pro virtueller Eingabequelle genau einen
`PointerSourceState`. Das Modell übernimmt die sinnvollen Begriffe des
[W3C-WebDriver-Actions-Modells](https://www.w3.org/TR/webdriver2/#actions),
ersetzt aber keinen bestehenden Open-Compute-Safetyvertrag:

- Identität: stabile `source_id`, `pointer_id`, `session_id`, `lease_id` und
  monoton steigende `sequence`; keine Vermischung mit Geräte-/OS-Pointer-IDs.
- Zustand: `phase` (`idle`, `preview`, `armed`, `pressed`, `paused`, `aborted`,
  `uncertain`), Position, gedrückte virtuelle Buttons und letzter verifizierter
  Zielzustand. Bei Pause/Abbruch wird der virtuelle Hold-Zustand geleert.
- Koordinatenrahmen: explizites `frame_kind` (`virtual_desktop_physical_px`,
  `window_client_physical_px`, `browser_viewport_css_px`), Ursprung, Ausdehnung,
  DPI-/Scale-Angabe, Monitor-/Viewport-Generation und gebundene Fenster- oder
  Browsing-Context-Identität. Eine Position ohne aktuellen Rahmen ist ungültig.
- Herkunft pro Transition: `actor`, `origin`, `channel`, `instruction_id`,
  `action_id`, `observation_id`, `window_token`, `target_id`, Zeitstempel und
  `dispatch_kind`. `origin=screen|unknown` darf niemals `armed` oder einen
  Dispatch erzeugen.
- Reihenfolge: veraltete, doppelte und lückenhafte Sequenzen werden
  fail-closed abgewiesen; nach `uncertain` ist keine Wiederholung derselben
  `action_id` erlaubt. Recovery beginnt mit neuer Observation, neuem Token und
  neuer Aktion.

Die [W3C Pointer Events](https://www.w3.org/TR/pointerevents3/) liefern
etablierte Begriffe wie eindeutige Pointer-ID, Pointer-Typ, Primärstatus,
Buttons und Pointer Capture. Open Compute verwendet diese nur als
Interoperabilitätsvokabular. Ein Overlay-Pointer ist kein Browser-Pointerevent,
kein Hardwaregerät und kein Beleg für eine native oder vertrauenswürdige
Eingabe.

### Dispatch-Grenze: virtuell gegenüber OS-Injektion

| `dispatch_kind` | Wirkung | Physischer Nutzerzeiger | Zulässiger Einsatz |
|---|---|---|---|
| `overlay_preview` | LLM-Pointer bewegen/zeichnen; kein Target-Event | unverändert | Beobachten, Planen, Zielvorschau |
| `browser_semantic` | DOM-/WebDriver-/CDP-Aktion im gebundenen Browsing Context | unverändert | eindeutiges Browserziel, frischer Context-/Observation-Token, verifizierbarer Zustand |
| `accessibility_semantic` | UIA-/später AX-/AT-SPI-Control-Pattern am gebundenen Element | unverändert | eindeutige unterstützte Semantik; auf Windows zuerst `Invoke`/`Toggle`/`SelectionItem` |
| `os_input_injection` | Systemweiter Input-Stream, z. B. Windows `SendInput` | kann bewegt/geändert werden | nur explizit autorisierter Fallback, wenn reale Pointer-Ereignisse für das Zielverhalten unvermeidbar sind |

Virtuelle Zielaktivierung darf in Receipts nie als „physischer Klick“ bezeichnet
werden. Echte OS-Input-Injektion ist erst unvermeidbar, wenn das konkrete Ziel
weder eine äquivalente Browser-/Anwendungs-API noch ein Accessibility-Pattern
anbietet und sein Verhalten reale Hover-, Down-/Move-/Up-, Drag-, Canvas-,
Game- oder andere systemnahe Pointer-Ereignisse verlangt. Microsoft beschreibt
[`SendInput`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
als Einfügen synthetischer Maus-/Tastaturereignisse in den System-Input-Stream;
der Aufruf unterliegt unter anderem UIPI. Dieser Pfad bleibt standardmäßig aus.
[Microsoft UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpatternsoverview)
ist dagegen der bevorzugte semantische Desktoppfad, wenn ein passendes
Control-Pattern angeboten wird.

### Safety-, Fokus-, Capture- und Recovery-Gates

1. **Nutzerpriorität:** Jede sicher als menschlich klassifizierte Aktivität
   pausiert sofort, invalidiert Lease und ausstehende Pointer-Sequenz und löscht
   das Overlay. `unknown` erlaubt keinen neuen Dispatch. Eine erneute Freigabe
   ist immer explizit; es gibt keine zeitbasierte Auto-Wiederaufnahme.
2. **Pause und Not-Aus:** Pause verhindert neue Transitionen. Not-Aus stoppt vor
   jedem Dispatch und während langer Sequenzen, löst virtuelle und bei einem
   späteren Injection-Pfad reale Holds, leert das Overlay und protokolliert den
   Abbruch. Der offene Panic-Hotkey mit Prozess-Teardown bleibt ein separates
   Vorab-Gate für produktive Injektion.
3. **Fokus/Fenster:** Vor semantischer Aktivierung und unmittelbar vor einer
   Injektion müssen Window-/Context-Token, HWND/PID/Titel, Prozess, Fokus,
   Zielidentität und Observation frisch und eindeutig sein. Fokus wird nicht
   stillschweigend gestohlen. Wechsel, Mehrdeutigkeit oder Neustart erzwingen
   Neu-Beobachtung.
4. **DPI/Mehrmonitor:** Transformationen erfolgen nur zwischen benannten
   Rahmen. Negative virtuelle Ursprünge, gemischte DPI, Monitorwechsel,
   Hotplug, Primärmonitorwechsel und Fensterbewegung erhöhen die
   Frame-Generation und invalidieren alte Positionen. Kein Clamp auf einen
   anderen Monitor oder ein anderes Fenster.
5. **Capture-Darstellung:** Ein `raw_frame` enthält die unveränderte
   Capturequelle und ein Feld, ob Eigentums-/LLM-Overlay darin technisch
   enthalten war. Ein `model_frame` darf den LLM-Pointer deterministisch als
   separate Ebene projizieren und trägt `pointer_projection=true`, `source_id`,
   Position und Frame-Generation. Projektion wird nie zurück in die Wahrnehmung
   als Zielinhalt oder Klickbeleg gespeist. Fenster-Capture bleibt Default;
   weitere Monitore/Nebenfenster erfordern expliziten Scope.
6. **Abbruch/Recovery:** `applied=None`, Fokusverlust, Tokenwechsel,
   Targetwechsel, Prozessende oder fehlende Post-Verifikation führen zu
   `uncertain` und keinem Retry. Cleanup ist idempotent. Recovery startet erst
   nach Nutzerfreigabe mit frischem Lease, Capture, Ziel und `action_id`.

### Receipt und Audit

Jede Bewegung, Buttontransition und Zielaktivierung erzeugt ein Receipt — auch
`overlay_preview` und abgewiesene Versuche. Pflichtfelder sind:

`receipt_id`, `source_id`, `session_id`, `lease_id`, `sequence`, `actor`,
`origin`, `channel`, `instruction_id`, `action_id`, `dispatch_kind`,
`requested_transition`, `position_before`, `position_after`, `frame`,
`window_token`, `observation_id`, `target_id`, `focus_before`, `focus_after`,
`applied` (`true|false|null`), `verified`, `safe_to_retry`, `reason`,
`user_interrupt`, `cleanup_result`, Zeitstempel sowie vorheriger und eigener
Audit-Hash. Rohtext, Screenshots, Tastendrücke und private Fensterinhalte
gehören nicht in das Audit. `position_after` ist beim virtuellen Pfad die
LLM-Pointerposition, beim Injection-Pfad zusätzlich getrennt von einer
bestätigten OS-Zeigerposition auszuweisen.

### Phasen und Folgetickets

- [x] **Phase 1 — virtueller Pointer-Kern:**
  `T-20260829-925104843` — `open_compute/virtual_pointer.py` implementiert die
  systemagnostische Zustandsmaschine, validierte Frame-/Move-/Press-/Release-
  Transitionen, eindeutige Provenienz, Replay-/Sequenzschutz, ungewisse
  Ergebnisse, Cleanup und Nutzerpause hinter expliziten Session-, Ownership-
  und Audit-Ports. `tests/test_virtual_pointer.py` prüft den Headless-Vertrag;
  der Kern importiert weder lokale Treiber noch OS-Input-, Capture-, MCP- oder
  Rendererpfade. Keine Injektion und keine produktive Freigabe.
- [x] **Phase 2 — Overlay-/Capture-Vertrag und Windows-Hostadapter:**
  `T-20260829-833036972` — `open_compute/virtual_pointer_overlay.py` ergänzt
  eine eigene Renderer-Schnittstelle und einen systemagnostischen
  Callback-Adapter. `open_compute/virtual_pointer_windows.py` ergänzt eine
  eigene konkrete LLM-Pointer-Fensterinstanz nach der bestehenden Windows-
  Overlay-Policy: Layered/Topmost/ToolWindow, `WS_EX_TRANSPARENT` plus
  `HTTRANSPARENT`, `WS_EX_NOACTIVATE`, `SW_SHOWNA`, PMv2-Threadkontext sowie
  kontrastreiche Kreuz-/Rautenform mit sichtbarem `LLM | actor`-Text. Der
  Controller verlangt click-through, no-activate, Per-Monitor-v2 sowie Form,
  Text und Farbe; veraltete Frame-/Topologie-Generationen scheitern geschlossen.
  Pause, Abbruch, Cleanup, Lease-Ende, Fehler und Hotplug bauen das eigene
  Fenster ab. Die PNG-Projektion trennt unverändertes
  `raw_frame`, modellseitige Pointer-Kopie und inhaltsfreies Hash-/Metadaten-
  `audit_frame`; negative Monitorursprünge und Scale bleiben im expliziten
  physischen Rahmen. `ScreenSignalIndicator`/`GetCursorPos` wurden nicht
  verändert oder wiederverwendet. Der Hostadapter liest oder bewegt den
  physischen Cursor nicht und enthält keine Input-Injektion. Offen bleibt
  ausschließlich die native visuelle USER-/Hardware-Abnahme auf realen
  Misch-DPI-/Mehrmonitor-Desktops; keine produktive Freigabe.
- [x] **Phase 3 — semantische Browser-/Desktop-Aktion (headless):**
  `T-20260829-714301166` — `open_compute/virtual_target.py` implementiert eine
  Browser-first-/UIA-second-Portkette ohne OS-Fallback. Ein semantischer
  Dispatch verbraucht die gebundene Observation-/Window-Token-Kombination
  einmalig und verlangt exakte Target-, Context-, Frame-, HWND-, PID-,
  Prozessstart- und Fokusidentität. Der bisherige `BrowserDriver` bot nur
  `goto`/`execute(Action)`/`close`, und `DomSnapshotProvider` war ein leerer
  Stub. Deshalb ergänzt `drivers/base.py` den kleinsten abwärtskompatiblen
  `SemanticBrowserDriver(BrowserDriver)`-Vertrag; der konkrete
  `BrowserDriverSemanticAdapter` bindet dessen Context-, Exact-Target-,
  engine-seitige Activate- und Post-State-Methoden und ruft das generische,
  potenziell koordinatenbasierte `execute(Action)` nie auf. Browserziele sind
  ausschließlich im expliziten CSS-/DOM-Kontext zulässig; UIA ausschließlich
  im Desktop-Rahmen über `resolve_detailed(exact=True, min_score=1.0)` und
  `invoke_target`/Control-Patterns. Fehlende Post-State-Verifikation,
  Fokuswechsel oder ein ungewisser Adapterausgang werden `uncertain` und sind
  nie retrybar. Das Receipt nennt die Operation `semantic_activate`, die
  tatsächliche semantische Route, Quelle, Zielidentität, Binding, Frame,
  Pre-/Post-State sowie `requested/applied/verified/uncertain/safe_to_retry`;
  es behauptet keinen physischen Mausklick. Alle konkreten Adaptertests laufen
  gegen Fake-Treiber; keine reale Browser-/Desktopaktion oder produktive
  Freigabe. Das Repo liefert weiterhin keinen Playwright-/WebDriver-/CDP-
  Engine-Treiber; ein solcher Host implementiert nun den belegten semantischen
  Vertrag statt auf Phase 5 oder Koordinateninput auszuweichen.
- [x] **Phase 4 — gegateter OS-Injektionsfallback (headless):**
  `T-20260829-108744993` — `open_compute/virtual_injection.py` ergänzt einen
  pointer-only Fallback, der in Core und konkretem
  `WindowsSendInputHostAdapter` standardmäßig deaktiviert ist. Erst ein
  aktives exaktes Lease, Mensch-vor-Agent-Prüfung, ungelöster Not-Aus,
  one-shot Observation, unveränderte HWND-/PID-/Prozess-/Fokusbindung,
  Per-Monitor-v2-/Frame-Generation, Normal-Desktop-/UIPI-Attestierung und die
  letzte Preclick-Prüfung erzeugen ein aktionsgebundenes one-shot Permit. Der
  Host prüft dieses Permit erneut und erreicht ausschließlich über eine
  isolierte, in Tests vollständig ersetzte Funktion den bestehenden
  `SendInput`-Rand. Das Permit bindet über einen deterministischen Request-
  Fingerprint auch Position, Transition/Button, vollständige Provenienz und
  Target sowie HWND/PID/Prozess-/Titel-/Context- und alle Framefelder; eine
  Mutation vor dem ersten Hostaufruf wird abgewiesen. Zero/partial Return,
  Adapterausnahme, Secure Desktop, UIPI-
  Ablehnung, Fokuswechsel und fehlende Post-Verifikation scheitern
  geschlossen; Ausnahmen nach dem nativen Rand werden `uncertain`, lösen
  Holds und sind nie retrybar. Der Adapter merkt nur selbst
  erfolgreich gedrückte Buttons und löst sie bei Abbruch/Fehler, ohne den
  physischen Zeiger zu lesen. Receipt/Audit führen virtuelle Overlayposition
  und optional verifizierte OS-Position getrennt. 20 fokussierte Tests plus
  Pointer-/Overlay-/Target-Regression laufen rein headless; keine native
  Eingabe wurde ausgeführt. Produktive Aktivierung und visuelle Windows-
  Hardwareabnahme bleiben Phase 5 bzw. eine separate Freigabe.
- [ ] **Phase 5 — nutzergeführte Live-Abnahme:**
  `T-20260829-337036402` (Hardware-/Browser-/Desktop-Matrix; keine autonome
  Abnahme und keine Release-Autorisierung).

### Verbindliche Testmatrix

| Achse | Mindestszenarien | Muss-Beleg |
|---|---|---|
| Kernzustand | Move, Down/Up, Pause, Abort, Cleanup, Replay, Out-of-order, `uncertain` | deterministische Transition und vollständiges Receipt |
| Herkunft | user, agent, screen, unknown; falsche/stale IDs | eindeutige Quelle; screen/unknown fail-closed |
| Fokus/Fenster | Fokuswechsel, HWND-/PID-/Titelwechsel, Restart, Z-Order, Mehrdeutigkeit | kein Dispatch mit altem Token |
| Capture | raw/model, Overlay ein/aus, Fenster/Vollbild, private Nebenfenster | Projektion deklariert; kein Scope-Leak; kein Overlay als Zielbeleg |
| DPI/Monitore | 100/125/150/200 %, negative Ursprünge, Monitorwechsel, Hotplug | Frame-Generation invalidiert alte Position; kein Clamp |
| Browser | Context-/Tab-/Viewportwechsel, DOM-Reflow, eindeutiges/mehrdeutiges Ziel | physischer Zeiger unverändert; Post-State verifiziert |
| Desktop | UIA Invoke/Toggle/Select, fehlendes Pattern, Element-/Prozesswechsel | semantischer Pfad oder explizite Ablehnung; physischer Zeiger unverändert |
| Nutzerpriorität | Nutzer bewegt/klickt vor Gate, zwischen Gate/Act und während Sequenz | Pause/Lease-Entzug; kein stilles Resume |
| Injection-Fallback | UIPI/Secure Desktop, Preclick-Mismatch, partial/zero return, Hover/Drag | fail-closed, Holds gelöst, Herkunft und Grund sichtbar |
| Recovery/Audit | Crash, Lease-Ablauf, Not-Aus, Hash-Manipulation, Retention | idempotentes Cleanup; neue IDs; Kettenprüfung |

**Noch offene Produktentscheidungen (nicht vorwegnehmen):** genaue visuelle
Form/Größe des LLM-Pointers; ob `model_frame` standardmäßig mit oder ohne
Pointer-Projektion an das Modell geht; Browseradapter (WebDriver, Playwright
oder CDP) und plattformübergreifende Accessibility-Reihenfolge; welche
konkreten Zielklassen einen Injection-Fallback rechtfertigen; separate
Nutzerfreigabe, Aufsicht und Aufbewahrungsdauer für Live-Receipts. Keine dieser
Entscheidungen blockiert Phase 1; alle blockieren eine produktive Freigabe.

## Headless Cooperative-Core-Slice 2026-07-28 [U]

- [x] Mockbare, inhaltfreie Human-Activity-Klassifikation mit bounded
  Own-Input-Provenienz. `GetLastInputInfo` nur als expliziter Single-Shot-
  Adapter; Unit-Tests injizieren Zeitquellen und aktivieren keine Win32-API.
- [x] Backendunabhängiger `perceive -> stabilize -> act -> verify`-Orchestrator
  mit Fake-Ports, Scope-Lease, Human-/Not-Aus-Pause, Idempotenz,
  Drei-Versuche-Hardlimit und Verifikationsgate.
- [x] Kein Retry nach unsicherem oder nachweislich angewendetem Aktionsergebnis;
  Retry nur nach beweisbar nicht angewendetem, explizit retrybarem Fehler.
- [x] Screen-Prompt-Injection fail-closed, SHA-256-verkettetes sanitisiertes
  Audit, begrenzte Retention mit explizitem Löschadapter sowie Crash-Cleanup.
- [x] Ownership-/Overlay- und Not-Aus-Verträge als nicht-rendernde Pure-Core-
  Schnittstellen.
- [ ] **BEWUSST ÜBERSPRUNGEN / LIVE-GATES:** echte Maus-/Tastaturinjektion oder
  Human-Monitoring; Fokus-/Fensteroperationen; sichtbares Overlay/globaler
  Not-Aus-Hotkey; Screen-Capture-Livebenchmark; Multi-Monitor-/DPI-Smoke;
  virtueller Display-/VM-/RDP-Start; Mikrofon/TTS/STT; interaktive GUI-/MCP-/
  Launcher-Akzeptanz. Neuer ausdrücklicher Nutzerauftrag erforderlich.

## Companion-/Handoff-Slice 2026-07-28

- [x] Fail-closed Session-Modi `OBSERVE`, `COMPANION`, `ASSIST`, `HANDOFF`,
  `CONTROL`, `PAUSED` mit zeitlich und räumlich begrenzter Lease.
- [x] Menschliche Aktivität, Lease-Ablauf und Pause entziehen Kontrolle sofort;
  Audit enthält nur Übergangsmetadaten, keine Screenshots oder rohen Eingaben.
- [x] Eindeutige Fensterauflösung per Titel/PID/HWND und mockbare Operationen
  activate/minimize/maximize/restore/move/resize; Mutation braucht Lease und
  SafetyPolicy.
- [x] Begrenzte, SHA-256-deduplizierte Capture-Serie; Fenster-Scope als Default,
  Vollbild nur mit explizitem Opt-in.
- [ ] Sichtbarer Windows-Live-Smoke für Fensteroperationen, Human-Interrupt und
  Capture-Timing; Voice, Overlay und virtueller Monitor bleiben außerhalb
  dieses engen CLI/Core-Slices.

## Befunde der Pflegerunde 2026-07-26 (surface-after-care)

- [ ] **Distributionsname klären — `open-compute` ist auf PyPI fremdbelegt.** Unter
  https://pypi.org/project/open-compute/ liegt seit Version 0.1.9 ein *anderes* Projekt
  („Open Compute — multi-agent systems for healthtech"). Bis 2026-07-26 wiesen README (EN/DE),
  `llms.txt` und `SKILL.md` mit `pip install open-compute` also auf ein fremdes Paket. Die
  Anleitungen zeigen jetzt auf `git+https://github.com/ellmos-ai/open-compute.git` — das ist
  die ehrliche Zwischenlösung, aber keine Entscheidung. Zu entscheiden: entweder einen freien
  Distributionsnamen wählen (z. B. `open-compute-core`, `ellmos-open-compute`) und
  veröffentlichen, oder bewusst git-only bleiben und das im README als Dauerzustand benennen.
  Der Import-Name `open_compute` und die Console-Scripts `oc` / `open-compute-mcp` sind davon
  unberührt.
- [ ] **`mss.mss()` ist deprecated.** Der Testlauf meldet
  `DeprecationWarning: mss.mss is deprecated ... use mss.MSS instead`
  (`open_compute/drivers/local.py:417`). Vor dem Umstellen prüfen, ab welcher mss-Version
  `mss.MSS` existiert, und `mss>=…` in `pyproject.toml` entsprechend anheben — sonst bricht
  die untere Grenze `mss>=9.0`.
- [x] **Skill-/Paketversion synchronisiert.** Seit v0.8.0 zieht `SKILL.md` mit
  `pyproject.toml` und `open_compute.__version__` mit; die frühere Abweichung ist
  damit ausdrücklich beendet.

## User-Auftrag 2026-07-31 — Bildschirm-Signalisierung (Compute-Mode-Anzeige)

Befund: Die Anzeige der Bildschirmnutzung war **nicht integriert** —
`cooperative.OwnershipIndicator` existierte nur als Protocol plus
`NullOwnershipIndicator` („Interface only. This release intentionally ships no
renderer."). Dieser Lauf liefert den Renderer.

- [x] **Aktiv-Signalisierung (Kern, umgesetzt):** `open_compute/indicator.py`.
  Leuchtende Bildschirm-Umrandung (Windows, click-through Overlay) mit
  Modus-Farben: **rot = CONTROL** (Modell steuert), **blau = OBSERVE** (Modell
  schaut nur zu / gemeinsam etwas anschauen), grün = COMPANION, orange =
  HANDOFF, grau = PAUSED. Neon-Ring um den Mauszeiger, Statuszeile mit
  Agent·Modus·Scope. `ScreenSignalIndicator` ist ein Drop-in für
  `CooperativeOrchestrator(indicator=…)`. CLI: `oc signal on --mode …`.
  **Live-Verify 2026-07-31 bestanden:** roter Rahmen + Cursor-Ring + Status-
  streifen auf echtem Desktop sichtbar (Screenshot `_session/signal_smoke.png`);
  ctypes-argtypes für 64-bit-Handles nachgezogen.
- [x] **Abbruch-Kanal mit Grundeingabe (umgesetzt):** `AbortChannel`-Protocol;
  `ConsoleAbortChannel` (stdin) und `TkAbortChannel` (topmost Eingabe-Overlay).
  `oc signal abort` sammelt die Kurznachricht des Menschen ein und gibt sie als
  JSON aus — der aufrufende Agent reicht sie ans Modell weiter.
- [x] **Signalisierung konfigurierbar (umgesetzt 2026-07-31):** `SignalConfig`
  (JSON, atomare Writes, `OC_SIGNAL_CONFIG` oder `_state/signal-config.json`).
  Pro Modus einzeln schaltbar: `enabled`, `color`, `label`, `border` (Rahmen),
  `cursor` (Mauszeiger-Ring) — Rahmen und Maus sind unabhängig. Usecase
  „Maus-Hervorhebung nur bei aktiver Steuerung, kein Rahmen" =
  `control: {border: false, cursor: true}` — **live verifiziert**
  (Screenshot `_session/signal_usecase.png`: Ring ja, Rahmen nein). Ohne
  Datei gelten exakt die eingebauten Defaults/Farben. CLI:
  `oc signal config --init|--show`, `oc signal on --config PATH`,
  Overrides `--no-border`/`--no-cursor`.
- [x] **Vorlauf als eigene, zugängliche Signalphase (umgesetzt 2026-08-21,
  T-20260821-676249921):** Die reale `pre_action_grace_seconds`-Konfiguration
  speist nun den sichtbaren sekündlichen Text `Start in N Sekunden`. Die
  Vorlaufphase besitzt mit `pre_action_grace_color` eine eigene statische Farbe
  und wechselt bei null genau einmal zur Modusfarbe. Die Zeichenroutine nutzt
  jetzt den laufenden Präsentationszustand statt des früher eingefrorenen
  Ausgangstextes. Fenstertitel und Accessibility-Name-Change-Ereignisse machen
  den Zustand für Screenreader lesbar; Farbe, Blinken oder Animation sind keine
  Voraussetzung. Abbruch, Neustart, Nullsekundenfall und fehlgeschlagener Start
  räumen den Countdown deterministisch auf.
- [x] **Chat-Anbindung über Bildschirminhalt (v0, umgesetzt 2026-07-31):**
  `oc chat [--channel console|tk] [--shot]` — Kurznachricht des Menschen zum
  Bildschirminhalt, optional mit Vollbild-Screenshot aus `_session/`, als
  JSON-Zeile an den aufrufenden Agenten. Bewusst kein Modell-Aufruf im
  Kommando: der Agent antwortet in seinem eigenen Kanal. Offen bleibt der
  Ausbau zur laufenden Dialog-Schleife mit `ellmos-chat`/`companion-for-agy`.
- [x] **Audio / Live-Sprache — Push-to-Talk v0 (umgesetzt 2026-07-31):**
  `open_compute/talk.py` + `oc talk --key F9` — Taste halten → sprechen →
  loslassen, WAV in `_session/` (winmm MCI, zero-dependency). Pfad geht als
  JSON an den Agenten. **Offen:** STT (Sprache→Text) und TTS (Antwort als
  Sprache) bleiben modellseitig; Hotkey ist ein Polling-Key (GetAsyncKeyState),
  kein globaler Hook.
- [x] **Abort-Overlay mit ESC-Hotkey (umgesetzt 2026-07-31):**
  `oc signal on --abort-hotkey [COMBO]` (Default `ctrl+alt+esc`) registriert
  einen globalen Hotkey im Overlay-Thread (MOD_NOREPEAT); bei Druck öffnet
  sich die topmost Tk-Grundeingabe und die Nachricht geht als JSON-Zeile an
  den Agenten. Eingabefeld ist ein Tk-Widget, nicht wörtlich in die Rahmen-
  Fenster gezeichnet — das bleibt als kosmetischer Ausbau offen.
  **Live-Verify des Hotkey-Drucks: OFFEN** (Headless getestet: Parsing,
  Callback-Verdrahtung, CLI-Übergabe).

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
| Tests | PASS | Vollständiger Lauf aus dem Modulroot — 525 passed (2026-08-01); GitHub Actions führt die Mock-Suite auf Push/PR aus. |
| Import check | PASS | `python -c "import open_compute; import open_compute.feed_manager; import open_compute.learning"` — OK, zero extras. |
| Documentation | READY | README (EN + DE), llms.txt, CHANGELOG, SECURITY, ARCHITECTURE present. |
| Integration | DEVELOPMENT | Usable as a standalone module. Contains marked stubs/interfaces (see below). |
| Distribution | OPEN | Not published on PyPI. The name `open-compute` there belongs to an unrelated project — see the open point below. |

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
  deferred to the user** (see `LIVE_SMOKE_RUNBOOK.md` / STATUS).
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

### User-Auftrag 2026-07-25 — „läuft noch nicht flüssig" (dieser Strang ist wieder aktiv)

Der Strang ist erneut aufgemacht worden: open-compute soll **schneller reagieren**, **schneller
zeigen wo ein Problem sitzt**, und **Teilprozesse modulintern automatisieren**. Das ist inhaltlich
derselbe Befund wie 2026-06-20 („schleppend") — deshalb hier fortgeschrieben statt neu aufgemacht.
**Vor jedem Umbau messen, nicht raten:** erst belegen, wohin die Zeit tatsächlich geht
(Prozessstart, Screenshot-Erzeugung/-Transport, UIA-Baumaufbau, MCP-Overhead), dann optimieren.

1. **Schnellere Reaktion** → höchste Priorität hat der bereits erfasste Punkt
   **„Prozess-Persistenz"** weiter unten (Python-Neustart pro `oc do`), danach der
   **„Live-Bild-Modus `oc watch`"**. Beide sind offen und adressieren genau diese Klage.
   Zusätzlich launcher-seitig: der npm-Launcher zieht den Server per `uvx` bei jedem Start von
   GitHub (Kaltstart) — das gehört in den Launcher (`ellmos-ai/open-compute-mcp`), nicht hierher.
2. **Schnellere Problemlokalisierung** → NEU, bisher nirgends erfasst (siehe eigener Punkt unten).
3. **Automatisierung von Teilprozessen im Modul** → deckt sich mit **„Semantisches Zielen"** und
   der Makro-/Batch-Linie; offen ist die Bündelung wiederkehrender Schrittfolgen (siehe unten).

- [ ] **NEU (aus Auftrag 2026-07-25): Timing- und Diagnose-Feld pro Aufruf.** Heute ist bei einer
  zähen oder fehlschlagenden Aktion nicht schnell erkennbar, WO es klemmt. Jeder Tool-/CLI-Aufruf
  soll optional Phasen-Timings und einen sprechenden Fehlerpfad zurückgeben, sodass
  „Server-/Prozessstart langsam" vs. „Capture langsam" vs. „UIA-Baum langsam" vs. „Element nicht
  gefunden" vs. „Fenster nicht im Vordergrund" vs. „Safety-Gate hat blockiert" ohne Nachfragen
  unterscheidbar sind. Schaltbar (Env/Flag), damit der Normalbetrieb schlank bleibt.
  Doppelnutzen: dieselben Zahlen sind die Messgrundlage für Punkt 1.
- [ ] **NEU (aus Auftrag 2026-07-25): wiederkehrende Schrittfolgen modulintern bündeln.** Was der
  Client heute Aufruf für Aufruf steuern muss, als ein Modul-Schritt anbieten — Kandidaten:
  `list_windows` → Fenster fokussieren → `tree` → Ziel treffen; „warte bis Element existiert";
  „Retry mit erneutem Rescan des Baums". Senkt Roundtrips zusätzlich zum Batch-Modus (v0.3.0),
  der nur Aktionen bündelt, aber keine Wahrnehmungs-/Warte-Schritte.

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
    EasyOCR (Apache-2.0), Windows.Media.Ocr via winrt.
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
- [x] Banner / logo asset for README. (2026-08-06: neues `assets/banner.png` — generiertes Glassmorphic-Motiv + Typo-Overlay; alte SVG-Banner entfernt)
- [ ] OpenAI backend: add injected-client test + verify Responses-API shape.
- [ ] macOS / Linux executor: port `LocalExecutor` to Quartz / X11 / xdotool.

## clirec — externer Aufnahmekanal

`clirec` ist jetzt ein eigenes Repo/Paket: https://github.com/ellmos-ai/clirec.
`open-compute` behält nur den lazy geladenen `oc rec`-Shim und alte Import-Wrapper.
Neue Recorder-/Ringpuffer-/Pause-Hotkey-/Replay-Arbeit gehört in das `clirec`-Repo.

- [x] Signal-Overlay automatisch verbergen, wenn kein Lauf mehr steuert (Auto-Hide).
  Kontext (2026-08-06, CALL-E-Session): Nach zwei per MCP gefahrenen Worker-Läufen blieb
  das rote CONTROL-Overlay aktiv, bis der Operator manuell signal_hide rief — der Nutzer
  erwartet, dass das von allein passiert. OC_SIGNAL_AUTO=control zeigt das Overlay beim
  Steuern, räumt es aber nicht zuverlässig ab (MCP-Server lebt sessionlang weiter).
  Erledigt 2026-08-06: Idle-Timeout `OC_SIGNAL_IDLE_HIDE` (Sekunden, Default 60; `0`/leer/
  `off` = aus). Jeder zustandsändernde Tool-Aufruf armiert den Timer neu, beim Ablauf wird
  nur ein per `OC_SIGNAL_AUTO` gezeigtes Overlay verborgen — ein manuelles `signal_show`
  bleibt unangetastet. Dokumentiert in README.md/README_de.md, `signal_status` meldet
  `auto_shown`/`idle_hide_armed`. Wirksam erst nach Neustart des MCP-Serverprozesses.
  Erweitert 2026-08-21 (v0.8): harte Signal-Lease/TTL mit Owner, Session und
  Ablaufzeit; Aktions-Turn-Ende, Fehler, Abbruch und Serverende räumen über den
  idempotenten `signal_hide`-Pfad auf. `keep_signal=true` ist der ausdrückliche
  Opt-in für eine sichtbare Lease über mehrere Calls.

- [ ] Tighten-only-Sicherheitsdeckel aus dem MCP-Adapter in den Kern verlagern.
  Kontext (2026-08-13, Launcher-Verifikation, Befund 2 in
  `.TOPICS/.AI/.MCP/_reports/OPEN-COMPUTE-LAUNCHER-VERIFIKATION_2026-08-13.md`):
  `_make_policy`/`_MODE_RANK` in `mcp_server.py` (Z. 136-152) stellen sicher, dass ein
  Aufrufer mit `mode="allow_all"` einen `read_only`-/`confirm`-Server nicht öffnen kann —
  diese Regel existiert NUR im Adapter. `safety.py` kennt keine Modus-Kombination, die CLI
  liest `OC_SAFETY_MODE` nur als argparse-Default. Ein zweiter Adapter müsste die Regel
  nachbauen (echte Duplikation). Nächster Schritt: Kombinationslogik nach `safety.py`
  (z. B. `SafetyPolicy.combine(cap, requested)`), Adapter + CLI darauf umstellen.

- [ ] GDI→WGC-Fallback-Kette der Fenster-Aufnahme in `drivers` hochziehen.
  Kontext (2026-08-13, Befund 3 ebd.): `_capture_window_png` im MCP-Server hat
  Schwarzbild-Erkennung (`wgc.is_blank_png`) + Zeitbudgets; der CLI-Pfad
  `_capture_window_bytes` (cli.py 632-646) ist reines mss ohne Fallback → CLI liefert bei
  hardware-komponierten Fenstern (Roblox Studio, Blender, GPU-Browser) Schwarzbilder, der
  MCP nicht. Nächster Schritt: Kette als `drivers.capture_window_robust` zentralisieren,
  beide Pfade darauf umstellen.

- [ ] Safety-Vokabular um Aufnahme/Dialoge erweitern — `talk`/`chat`/`signal_*` gaten.
  Kontext (2026-08-13, Befund 4 ebd.): `signal_show`/`signal_hide`, `chat` (Tk-Dialog +
  PNG) und `talk` (Mikrofonaufnahme + WAV) rufen weder `_make_policy` noch `_gate` —
  unter `OC_SAFETY_MODE=read_only` nimmt `talk` weiterhin Ton auf. Ursache: das
  `ActionType`-Vokabular der `SafetyPolicy` kennt nur Eingabesynthese. Nächster Schritt:
  ActionTypes `record_audio`, `show_dialog`, `overlay` ergänzen und die vier Tools gaten.
