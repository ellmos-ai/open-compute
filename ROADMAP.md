# open-compute — ROADMAP

> Strategische Übersicht geplanter Arbeit. Operative Checklisten in `TODO.md`,
> abgeschlossene Punkte wandern in `CHANGELOG.md`.

## clirec — Aufnahmekanal

Lose gekoppeltes Subpaket `open_compute/clirec/`: nimmt Maus/Tastatur-Demonstrationen als
menschenlesbare `.clirec`-Textdatei (+ Screenshot-Sidecar) auf und spielt sie adaptiv ab,
um bestehende Skills/Workflows bei schweren/langsamen GUI-Abläufen zu beschleunigen.
Spec: `_reports/CLIREC_RECORDER_DESIGN_2026-06-28.md`. Plan: `_reports/CLIREC_RECORDER_PLAN_2026-06-28.md`.

### Stand 2026-06-28 (erledigt, Branch master, Suite 396/1 grün)

- ✅ `.clirec`-Textformat (read/write/validate/apply_params) + Sidecar-Konvention
- ✅ Capture-Schnittstelle + Mock; WinAPI-Backend (Default, zero-dep) + pynput-Backend (Extra `[record]`)
- ✅ Segmentierung Events→Schritte inkl. Passwort-Maskierung (Live-Pfad via UIA-Probe)
- ✅ Pull-basierter Recorder (manuelle Aufnahme + Ringpuffer-**Engine** `cut_last`/`_prune`)
- ✅ Adaptiver Replay (stumpf → `locate`-Fallback → Frame-Beleg), Parameter-Einsetzung
- ✅ Config-Sektion `clirec`; `oc rec validate|list|replay|start`
- ✅ Teilskill `skills/clirec/SKILL.md` (Aufnahme-/Verweis-/Selbstverifikations-Leitlinien)

### Phase 1 — Ringpuffer wirklich nutzbar machen (höchste Priorität)

Begründung: Lukas' lokale Konfiguration setzt `ringbuffer_enabled: true`. Die Engine ist
gebaut & getestet, aber ohne dauerhaft laufenden Prozess gibt es keinen Puffer zum Schneiden.

1. **Ringpuffer-Daemon** — `oc rec daemon`: Hintergrundprozess, der den In-Memory-Ringpuffer
   über die `pump()`-Schleife kontinuierlich füllt und zeitfenster-prunt. `oc rec buffer --last Nm
   <name>` signalisiert dem Daemon den retroaktiven Schnitt (Signal/Datei-IPC), der die Recording
   schreibt. Ersetzt den aktuellen `_rec_live`-Stub für `stop`/`buffer`.
2. **Globaler Pause-Hotkey** — WinAPI `RegisterHotKey` (oder LL-Hook) toggelt `set_paused`;
   verdrahtet den reservierten Config-Key `clirec.pause_hotkey`. Schließt das Datenschutz-Versprechen
   („Strg+Alt+P stoppt sofort"), das die SKILL.md bis dahin bewusst NICHT verspricht.
3. **Frame-Capture im Live-Recorder** — `frame_grabber` in `_rec_live` injizieren
   (LocalExecutor `screenshot()`/mss), `Step.frame` verlinken. Format unterstützt es bereits.

### Phase 2 — Robustheit & Portierung

- WinAPI: `SetWindowsHookExW`-NULL-Rückgabe prüfen → bei Hook-Fehler (Rechte) warnen statt still.
- pynput-Backend `stop()`: Listener-Threads joinen (garantierter Clean-Stop).
- `oc rec replay --param` ohne Wert: warnen statt still überspringen.
- Tests: dangling `mouse_down` ohne `mouse_up`; Step-Index-Sequenz; CLI-Fehlerpfade.
- Live-Smoke: echte Aufnahme→Replay auf Windows-GUI manuell durchspielen (empirisch verifizieren).
- macOS/Linux: pynput-Backend live testen (derzeit nur Smoke + Top-Level-Importsicherheit).

### Bewusst entkoppelt / nur bei Bedarf (Spec §2, §4)

- Selbst-Verifikation bleibt **Text** im SKILL.md (kein Mechanismus) — so gewollt.
- **Keine** `learning.py`-Anbindung: clirec ist ein getrenntes, standalone anzapfbares System.
  Falls je ein Korrektur-Rückfluss gewünscht ist, nur als externer, entkoppelter Helfer.
- Kein automatischer Skill-/Command-Export: die Verknüpfung lebt als Verweis im Skript-Text.
