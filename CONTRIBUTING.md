# Contributing to open-compute / Mitwirken an open-compute

[English](#english) | [Deutsch](#deutsch)

---

<a id="english"></a>
## English

Thank you for your interest in contributing to **open-compute** (`ellmos-ai/open-compute`), a model-agnostic, dependency-light computer-use core for autonomous AI agents and desktop automation across Anthropic Claude, OpenAI CUA, and deterministic offline mock backends.

### 1. Architectural Principles & 10 Governance Invariants

All contributions must strictly adhere to our 10 core architectural and runtime invariants:

1. **Model-Agnostic Core Loop (`INV-MOD-01`)**: One unified loop orchestration interface (`open_compute.backends.base.ComputerBackend`) supporting Claude (Messages API), OpenAI CUA, and deterministic offline mock execution without vendor lock-in.
2. **Zero Mandatory Runtime Dependencies (`INV-DEP-02`)**: The core agent loop, canonical action schema, coordinate normalization, and safety policy require exclusively the Python standard library (`dependencies = []`). Vendor SDKs (`anthropic`, `openai`, `mss`, `playwright`, `Pillow`, `uiautomation`, `windows-capture`, `mcp`) are optional, lazy-loaded extras.
3. **Normalized Coordinates 0..1 (`INV-CRD-03`)**: All model coordinate perceptions and actions operate within the invariant float range `[0.0, 1.0]`, with centralized DPI and multi-monitor rescaling in `open_compute.coordinates`.
4. **MCP Pre-Action Grace Window (`INV-GRC-04`)**: Covered MCP calls use an operator-configured wait (4 seconds and a 120-second activity cooldown by default); canonical configuration or `OC_SIGNAL_GRACE_SECONDS=0` can disable it. Direct CLI commands and SDK `AgentLoop` runs do not use this MCP wait.
5. **Fail-Closed Central Safety Gate (`INV-SAF-05`)**: Three-tier safety policy (`confirm`, `allow_all`, `read_only`) intercepting every GUI action (click, type, drag, hotkey, shell execution) before OS dispatch.
6. **Unprivileged Non-Elevation Mode (`INV-USR-06`)**: Full operational capability in unprivileged user mode (`RunAsInvoker`). Zero administrator credentials, UAC elevation, or background OS daemon services are permitted or required.
7. **Default Mock-Mode Egress (`INV-EGR-07`)**: The default mock loop runs offline. Configured provider backends and Cowork’s Codex worker may use external services; MCP hosts receive observation content and may pass it to their own models.
8. **API-Key and Artifact Handling (`INV-SEC-08`)**: Provider clients accept an explicit key or SDK environment configuration. The package does not intentionally persist provider API keys; captures and action content may contain sensitive data and are not universally redacted.
9. **Profile-Filtered Perception (`INV-SCP-09`)**: Filtered MCP calls apply the selected profile’s budgets and configured exclusions before returning results. Pixels are captured locally before configured window intersections are blanked; ordinary `capture` is unfiltered.
10. **48h Security Response SLA (`INV-SLA-10`)**: Binding commitment to acknowledge security notices within 48 hours and deliver initial triage within 5 business days via `security@ellmos.ai`, `security@open-bricks.org`, and `support@lukasgeiger.com`.

### 2. Plan D Local Development Workflow

In accordance with our cross-system architecture (Plan D), the local git repository at `C:\_Local_DEV\repos\open-compute` serves as the authoritative **Source of Truth**. Development, testing, and commits must take place exclusively in the canonical local clone. Cloud mirrors serve solely as gitless read projections.

```bash
# Navigate to the canonical local clone
cd C:\_Local_DEV\repos\open-compute

# Verify git status and branch
git status
git branch --show-current

# Install editable package and development dependencies
pip install -e ".[dev]"

# Run full test suite with isolated basetemp
pytest

# Verify code formatting and linting
ruff check .
```

### 3. Version Freeze Discipline (`T-20260920-167562623`)

open-compute operates under strict version-freeze discipline. The version identifier (`0.9.1` in `pyproject.toml`, `open_compute/__init__.py`, `ellmos-module.v2.json`, and manifests) must not be arbitrarily incremented. All improvements, bug fixes, and hygiene adjustments are documented under `## [Unreleased]` in `CHANGELOG.md`.

### 4. Quality Gates

Before submitting a pull request or pushing commits, verify all local quality gates:

1. `pytest`: 100% green test execution across all unit, integration, provider, and metadata contract test suites (832+ passed tests).
2. `ruff check .`: Zero lint errors.
3. `python -m compileall -q open_compute tests`: Zero bytecode compilation errors.
4. `git diff --check`: Zero whitespace or line-ending anomalies.
5. `git diff -G"version = "`: Zero unauthorized version modifications.

### 5. Statutory Notice (§ 521 BGB) & Zero-Copyleft on User Data

This software is provided free of charge under the permissive MIT License. In accordance with German statutory law (**§ 521 BGB** — *Haftung des Schenkers*), liability for defects in quality and title is strictly limited to intentional misconduct (*Vorsatz*) and gross negligence (*grobe Fahrlässigkeit*).

**Zero-Copyleft scope:** The MIT project license and recorded dependency licenses do not establish a blanket guarantee for future or transitive dependencies. Consult the applicable package license files and the dependency inventory.

---

<a id="deutsch"></a>
## Deutsch

Vielen Dank für Ihr Interesse an einer Mitarbeit an **open-compute** (`ellmos-ai/open-compute`), einem modell-agnostischen, abhängigkeitsarmen Computer-Use-Kern für autonome KI-Agenten und Desktop-Automatisierung über Anthropic Claude, OpenAI CUA und deterministische Offline-Mock-Backends.

### 1. Architektur-Prinzipien & 10 Governance-Invarianten

Alle Beiträge müssen unsere 10 verbindlichen Architektur- und Laufzeitinvarianten strikt einhalten:

1. **Modell-Agnostischer Kern-Loop (`INV-MOD-01`)**: Einheitliche Loop-Orchestrierung (`open_compute.backends.base.ComputerBackend`) für Claude (Messages API), OpenAI CUA und deterministische Offline-Mock-Ausführung ohne Anbieterabhängigkeit.
2. **Keine obligatorischen Laufzeitabhängigkeiten (`INV-DEP-02`)**: Der Kern-Loop, das kanonische Aktionsschema, die Koordinatennormalisierung und das Sicherheits-Gate basieren ausschließlich auf der Python-Standardbibliothek (`dependencies = []`). Externe Vendor-SDKs (`anthropic`, `openai`, `mss`, `playwright`, `Pillow`, `uiautomation`, `windows-capture`, `mcp`) sind optionale, spät geladene Extras.
3. **Normalisierte Koordinaten 0..1 (`INV-CRD-03`)**: Alle visuellen Koordinatenwahrnehmungen und Aktionen arbeiten im invarianten Float-Bereich `[0.0, 1.0]`, mit zentraler DPI- und Multi-Monitor-Umrechnung in `open_compute.coordinates`.
4. **MCP-Karenzfenster (`INV-GRC-04`)**: Abgedeckte MCP-Aufrufe verwenden eine vom Betreiber konfigurierte Wartezeit (standardmäßig 4 Sekunden mit 120 Sekunden Aktivitäts-Cooldown). Kanonische Konfiguration oder `OC_SIGNAL_GRACE_SECONDS=0` können sie deaktivieren. Direkte CLI-Befehle und SDK-`AgentLoop`-Läufe verwenden diesen MCP-Wartepfad nicht.
5. **Fail-Closed Sicherheits-Gate (`INV-SAF-05`)**: Dreistufige Richtlinie (`confirm`, `allow_all`, `read_only`), die jede GUI-Aktion (Klick, Tastendruck, Drag, Hotkey, Shell-Befehl) vor der Betriebssystemausführung abfängt.
6. **Unprivilegierter Modus ohne Elevation (`INV-USR-06`)**: Vollständige Funktionalität im unprivilegierten `RunAsInvoker`-Benutzermodus. Keine Administratorrechte, UAC-Elevation oder Hintergrunddienste erforderlich.
7. **Egress im Mock-Standard (`INV-EGR-07`)**: Der standardmäßige Mock-Loop bleibt offline. Konfigurierte Provider und der Codex-Worker von Cowork können externe Dienste nutzen; MCP-Hosts empfangen Beobachtungsinhalte und können sie an eigene Modelle weitergeben.
8. **API-Schlüssel und Artefakte (`INV-SEC-08`)**: Provider-Clients akzeptieren einen expliziten Schlüssel oder die SDK-Umgebungskonfiguration. Das Paket speichert Provider-API-Schlüssel nicht absichtlich; Aufnahmen und Aktionsinhalte können sensible Daten enthalten und werden nicht allgemein automatisch geschwärzt.
9. **Profilgefilterte Wahrnehmung (`INV-SCP-09`)**: Gefilterte MCP-Aufrufe wenden Budgets und konfigurierte Ausschlüsse des gewählten Profils vor der Rückgabe an. Die Pixel werden lokal aufgenommen, bevor konfigurierte Fensterüberschneidungen ausgeblendet werden; normales `capture` bleibt ungefiltert.
10. **48h Sicherheits-SLA (`INV-SLA-10`)**: Verbindliche Zusage zur Bestätigung von Sicherheitsmeldungen innerhalb von 48 Stunden und Erstbewertung innerhalb von 5 Werktagen via `security@ellmos.ai`, `security@open-bricks.org` und `support@lukasgeiger.com`.

### 2. Plan D Lokaler Entwicklungs-Workflow

Gemäß unserer systemweiten Architektur (Plan D) ist das lokale Git-Repository unter `C:\_Local_DEV\repos\open-compute` die verbindliche **Source of Truth**. Entwicklung, Tests und Commits finden ausschließlich im kanonischen lokalen Klon statt. Cloud-Spiegel dienen rein als gitlose Leseprojektionen.

```bash
# In den kanonischen lokalen Klon wechseln
cd C:\_Local_DEV\repos\open-compute

# Git-Status und Branch prüfen
git status
git branch --show-current

# Editierbares Paket und Entwicklerabhängigkeiten installieren
pip install -e ".[dev]"

# Vollständige Testsuite mit isoliertem basetemp ausführen
pytest

# Code-Formatierung und Linter prüfen
ruff check .
```

### 3. Version-Freeze-Disziplin (`T-20260920-167562623`)

open-compute unterliegt einer strikten Version-Freeze-Disziplin. Die Versionsnummer (`0.9.1` in `pyproject.toml`, `open_compute/__init__.py`, `ellmos-module.v2.json` und Manifesten) darf nicht willkürlich erhöht werden. Sämtliche Verbesserungen, Fehlerbehebungen und Hygieneanpassungen werden unter `## [Unreleased]` in `CHANGELOG.md` dokumentiert.

### 4. Quality Gates

Vor dem Einreichen eines Pull Requests oder dem Pushen von Commits müssen alle lokalen Quality Gates verifiziert werden:

1. `pytest`: 100% grüne Ausführung über alle Unit-, Integrations-, Provider- und Vertragstest-Suiten (832+ bestandene Tests).
2. `ruff check .`: Null Linter-Fehler.
3. `python -m compileall -q open_compute tests`: Null Bytecode-Kompilierungsfehler.
4. `git diff --check`: Null Whitespace- oder Zeilenumbruchfehler.
5. `git diff -G"version = "`: Null unautorisierte Versionsmodifikationen.

### 5. Gesetzlicher Haftungsausschluss (§ 521 BGB) & Zero-Copyleft auf Nutzerdaten

Diese Software wird unentgeltlich unter der permissiven MIT-Lizenz bereitgestellt. Gemäß **§ 521 BGB** (*Haftung des Schenkers*) ist die Haftung für Sach- und Rechtsmängel auf Vorsatz und grobe Fahrlässigkeit beschränkt.

**Geltungsbereich von Zero-Copyleft:** Aus der MIT-Projektlizenz und den erfassten Abhängigkeitslizenzen folgt keine pauschale Garantie für künftige oder transitive Abhängigkeiten. Maßgeblich sind die jeweiligen Lizenzdateien und die Abhängigkeitsinventur.
