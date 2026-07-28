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

Ausdrücklich nicht aktiviert oder getestet sind Live-Human-Monitoring,
Maus-/Tastaturinjektion, Fenster-/Fokusoperationen, sichtbares Overlay,
Live-Capture, Multi-Monitor/DPI, virtuelle Displays/VM/RDP, Audio/Voice sowie
GUI/MCP/Launcher-Akzeptanz. Diese Punkte bleiben getrennte, nutzerbestätigte
Live-Gates; der Headless-Slice genehmigt keinen produktiven Control-Modus.
