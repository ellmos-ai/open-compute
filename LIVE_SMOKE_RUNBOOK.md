# open-compute — Live-Smoke Runbook (vom Nutzer auszuführen)

> Stand 2026-06-27. Der autonome Teil ist fertig: Mock-Suite **360 grün (+1 skip)**, das
> OpenAI-Backend ist gegen die aktuellen Docs verifiziert (Modell `gpt-5.5`, Tool-Type `computer`),
> Claude-Backend per injected-client getestet. **Was hier offen bleibt, braucht echte API-Keys +
> dein Auge** — daher dieses Runbook statt eines automatisierten Laufs. Nach erfolgreichem Lauf:
> Haken in `RELEASE_GATE.md` setzen.

## 0. Voraussetzungen

- Windows 11 (LocalExecutor ist Windows-verifiziert), Python ≥ 3.10.
- Isolierte/unkritische Sitzung (Computer-Use steuert echte Maus/Tastatur). Idealerweise eine VM
  oder ein leerer Desktop ohne sensible Fenster.
- Installation mit dem passenden Extra:
  ```powershell
  pip install -e "C:\Users\User\OneDrive\.TOPICS\.AI\.MODULES\open-compute[local,claude]"   # Claude
  pip install -e "C:\Users\User\OneDrive\.TOPICS\.AI\.MODULES\open-compute[local,openai]"   # OpenAI
  ```

## 1. Keyless-Smoke zuerst (kein Key, bestätigt die Mechanik)

```powershell
oc capture --window "Editor"          # erwartet: PNG geschrieben, Pfad ausgegeben
oc do mouse_move --x 0.5 --y 0.5      # erwartet: Cursor bewegt sich zur Bildschirmmitte
oc tree --window "Editor"             # erwartet: UIA-Baum nur für das benannte Fenster (v0.4.1-Fix)
oc do --fullres                       # erwartet: Full-Res-Annotation (v0.5)
```
Erfolg = Screenshots/Tree plausibel, Safety-Gate fragt vor riskanten Aktionen. Das deckt UIA-Window-
Scoping + Fullres ab (die noch offenen „Live-Verify"-Punkte).

## 2. Claude-Backend (echter Anthropic-Key)

```powershell
$env:ANTHROPIC_API_KEY = "<dein-key>"
oc run "Öffne den Editor und tippe 'hallo'" --backend claude
```
Erwartet: perception → model-tool-call → action → perception-Schleife läuft; Editor öffnet, Text
erscheint; Lauf endet sauber (kein Endlos-Loop, Safety-Gate aktiv).

## 3. OpenAI-Backend (echter OpenAI-Key) — verifiziert gegen Docs 2026-06-27

```powershell
$env:OPENAI_API_KEY = "<dein-key>"
oc run "Öffne den Editor und tippe 'hallo'" --backend openai           # nutzt Default-Modell gpt-5.5, Tool-Type "computer"
# Fallback auf Legacy-Surface, falls dein Account noch die alte Preview nutzt:
# (Modell/Tool-Type sind konstruktorseitig konfigurierbar — bei Bedarf im Code/CLI überschreiben)
```
Erwartet: gleiche Schleife wie Claude. Falls ein 400/Modell-Fehler kommt: prüfe, ob dein Account
`gpt-5.5`/`gpt-5.4` für das computer-tool freigeschaltet hat; sonst `tool_type="computer_use_preview"`
+ Legacy-Modell setzen (Backend unterstützt beide Pfade).

## 4. Sign-off-Checkliste (nach den Läufen)

- [ ] Keyless (capture/do/tree/fullres/window) auf echtem Windows ok
- [ ] Claude end-to-end ok
- [ ] OpenAI end-to-end ok (oder Modellfreigabe-Status notiert)
- [ ] Keine verwaisten Prozesse, Safety-Gate hat vor riskanten Aktionen gefragt
- [ ] Ergebnis in `RELEASE_GATE.md` / `TODO.md` (STATUS) eingetragen → dann ist open-compute „fertig"

> Ohne diesen vom Nutzer ausgeführten Lauf bleibt open-compute beim Stand „autonom fertig, Live-Verify
> ausstehend" — das ist die bewusste, dokumentierte Grenze, nicht eine offene Baustelle im Code.
