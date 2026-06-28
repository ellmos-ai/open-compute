---
name: clirec
description: Aufnahmekanal für open-compute. Nutze diesen Teilskill, wenn ein Skill/Workflow durch eine konkrete Maus/Tastatur-Demonstration beschleunigt oder ein in der Vergangenheit schwerer Ablauf zuverlässig vorgemacht werden soll. Nimmt eine .clirec-Datei auf und spielt sie adaptiv ab.
---

# clirec — Aufnahmekanal

Eine Aufnahme (`.clirec`) ist ein **angeheftetes Demonstrations-Artefakt** zu einem
bestehenden Skill/Workflow — kein eigener Workflow. Sie ist eine **Zusatzfunktion**:
Wenn ein Skill ohne Aufnahme funktioniert, ist das besser. Aufnahme nur nutzen, wenn
ein Ablauf schwer oder oft fehlschlug.

## Wann aufnehmen
- Ein Ziel ist verbal beschrieben (Skill/Workflow existiert), aber das Modell scheitert
  an der GUI-Ausführung oder es ist langsam/fehleranfällig.
- Dann: hier lesen → aufnehmen → aus dem Ziel-Skill auf die `.clirec` verweisen.

## Aufnehmen
1. `oc rec start <name>` — Tätigkeit ausführen — `Strg+C` beendet und speichert.
2. Ringpuffer (lokal, falls aktiviert): Tätigkeit machen, dann nachträglich schneiden.
3. Ergebnis: `<recordings_dir>/<name>.clirec` (+ `<name>.clirec.frames/` als Beleg).

## Referenzieren (Verweis-Konvention)
Im Ziel-Skill den **relativen Pfad** zur `.clirec` nennen und beschreiben, **wann** sie
gilt, z. B.: „Wenn der Login-Dialog erscheint, nutze `login.clirec`." Aufnahme entweder
unter `recordings/` im Modul oder direkt **neben** der referenzierenden `SKILL.md`.

## Abspielen
- `oc rec replay <name>.clirec [--param k=v]` — spielt zuerst stumpf (Koordinaten),
  bei Abweichung agentengestützte Re-Lokalisierung; Frame-PNG dient als Beleg.

## Selbst-Verifikation (Pflicht beim Ausführen)
Nach einem Ablauf/Replay **immer selbst prüfen**: „Habe ich das Ziel erreicht?" Bei
Zweifel den Nutzer zuschauen lassen und Korrektionen aufnehmen lassen — nicht raten.

## Kontext-Referenzierung statt Auto-Export
Wird ein Skill ausgeführt, **immer überlegen, in welchem Kontext er aufgerufen wurde**,
und die passende `.clirec` referenzieren, **wenn sie funktioniert**. Kein automatischer
Export nötig — die Verknüpfung lebt im Skript-Text.

## Datenschutz
Systemweiter Mitschnitt ist sensibel. Passwortfelder werden maskiert (`***`); der
Pause-Hotkey (Default `Strg+Alt+P`) stoppt den Mitschnitt sofort. In der öffentlichen
Version ist der Ringpuffer standardmäßig **aus**.
