# open-compute — ROADMAP

> Strategische Übersicht geplanter Arbeit. Operative Checklisten in `TODO.md`,
> abgeschlossene Punkte wandern in `CHANGELOG.md`.

## clirec — externer Aufnahmekanal

`clirec` wurde aus `open_compute/clirec/` in ein eigenes Repository ausgelagert:
https://github.com/ellmos-ai/clirec

`open-compute` behält nur einen lazy geladenen `oc rec`-Kompatibilitäts-Shim und
die alte `open_compute.clirec.*`-Import-Namespace als Wrapper. Operative Arbeit
am Recorder, Ringpuffer-Daemon, Pause-Hotkey, Frame-Capture und Portierung lebt
ab jetzt im `clirec`-Repo.

## Kooperative Steuerung — aktueller Headless-Stand

Der Pure-Core-Slice für T-20260728-11 ist implementiert und ausschließlich
statisch/mockgetestet:

- inhaltfreie Human-Activity-Abstraktion mit injizierbarem
  `GetLastInputInfo`-Single-Shot-Adapter;
- `perceive -> stabilize -> act -> verify` mit Fake-Ports, Scope-Lease,
  Human-Interrupt, Not-Aus, Idempotenz und begrenztem Retry;
- fail-closed Screen-Prompt-Injection-Gate;
- hash-verkettetes, inhaltssanitisiertes Audit;
- explizite Retention-/Lösch- und Crash-Cleanup-Verträge;
- Overlay-/Ownership- und Not-Aus-Schnittstellen ohne Renderer oder Hotkey.

Ausdrücklich nicht aktiviert oder live getestet sind Live-Human-Monitoring,
Maus-/Tastaturinjektion, Fenster-/Fokusoperationen, die native visuelle
Overlay-Abnahme, Live-Capture, Multi-Monitor/DPI-Hardware, virtuelle
Displays/VM/RDP, Audio/Voice sowie GUI/MCP/Launcher-Akzeptanz. Diese Punkte
bleiben getrennte, nutzerbestätigte Live-Gates; der Headless-Slice genehmigt
keinen produktiven Control-Modus.

## Getrennter sichtbarer LLM-Zeiger — Spezifikationsstand 2026-08-29

T-20260827-586759665 ist als Architektur- und Sicherheitsleiter in `TODO.md`
materialisiert. Leitentscheidung: Open Compute führt den LLM-Zeiger als eigene
virtuelle Eingabequelle mit eigener Position, Button-/Pause-/Abbruchzustand und
lückenloser Herkunft. Der physische Nutzerzeiger bleibt bei Overlay-, Browser-
und Accessibility-Pfaden unverändert. Der bestehende Signalring bleibt ein
Eigentumsindikator am physischen Zeiger und wird nicht umgedeutet.

Die Umsetzung erfolgt strikt gestuft:

1. virtueller Pointer-Kern und Receipts (`T-20260829-925104843`, headless
   implementiert und getestet),
2. fokusfreier Overlay-/Capture-Vertrag mit Modellprojektion und DPI-/
   Mehrmonitor-Gates (`T-20260829-833036972`, inklusive separatem konkretem
   Windows-Popuphost headless über Fake-/statische Verträge implementiert und
   getestet; native Windows-Sichtprüfung bleibt USER-/Hardware-Gate),
3. semantische Browser-/Desktop-Zielaktivierung ohne OS-Zeigerbewegung
   (`T-20260829-714301166`, Browser-first/UIA-second mit konkretem
   `BrowserDriverSemanticAdapter` und UIA-Adapter, One-shot-Bindung und
   Receipts headless implementiert; der abwärtskompatible
   `SemanticBrowserDriver` ist die Hostgrenze für eine Playwright-/WebDriver-/
   CDP-Engine, reale Browser-/UIA-Abnahme bleibt im USER-/Hardware-Gate),
4. separat autorisierter, standardmäßig deaktivierter OS-Input-Fallback
   (`T-20260829-108744993`, pointer-only Core und konkreter Windows-
   `SendInput`-Hostadapter headless implementiert; alle Gates und der native
   Rand sind über Fakes/Monkeypatches geprüft, kein Live-Aufruf),
5. nutzergeführte Hardware-/Browser-/Desktop-Abnahme
   (`T-20260829-337036402`).

Produktive Mausautomation ist durch diese Spezifikation **nicht** freigegeben.
OS-Input-Injektion ist nur dann fachlich begründbar, wenn ein konkretes Ziel
reale Pointer-Ereignisse verlangt und weder ein gebundener Browserpfad noch ein
äquivalentes Accessibility-Pattern verfügbar ist. Sie bleibt hinter eigener
Produktentscheidung, Control-Lease, Nutzerpriorität, Pause/Not-Aus,
Fokus-/Fenster-/Capture-Prüfung, Pre-/Post-Verifikation und Live-Abnahme.
