# Contributing to open-compute / Mitwirken an open-compute

[English](#english) | [Deutsch](#deutsch)

---

<a id="english"></a>
## English

Thank you for your interest in contributing to **open-compute** (`ellmos-ai/open-compute`), a model-agnostic, dependency-light computer-use core for autonomous AI agents and desktop automation across Anthropic Claude, OpenAI CUA, and deterministic offline mock backends.

### 1. Architectural Principles & 10 Governance Invariants

All contributions must strictly adhere to our 10 core architectural and runtime invariants:

1. **Model-Agnostic Core Loop (`INV-MOD-01`)**: One unified loop orchestration interface (`open_compute.base.ComputerBackend`) supporting Claude (Messages API), OpenAI CUA, and deterministic offline mock execution without vendor lock-in.
2. **Zero Mandatory Runtime Dependencies (`INV-DEP-02`)**: The core agent loop, canonical action schema, coordinate normalization, and safety policy require exclusively the Python standard library (`dependencies = []`). Vendor SDKs (`anthropic`, `openai`, `mss`, `playwright`, `Pillow`, `uiautomation`, `windows-capture`, `mcp`) are optional, lazy-loaded extras.
3. **Normalized Coordinates 0..1 (`INV-CRD-03`)**: All model coordinate perceptions and actions operate within the invariant float range `[0.0, 1.0]`, with centralized DPI and multi-monitor rescaling in `open_compute.coordinates`.
4. **Mandatory Pre-Action Grace Window (`INV-GRC-04`)**: Unconditional 4-second delay before the first mutating GUI action, empowering operators with an emergency-abort oversight window.
5. **Fail-Closed Central Safety Gate (`INV-SAF-05`)**: Three-tier safety policy (`confirm`, `allow_all`, `read_only`) intercepting every GUI action (click, type, drag, hotkey, shell execution) before OS dispatch.
6. **Unprivileged Non-Elevation Mode (`INV-USR-06`)**: Full operational capability in unprivileged user mode (`RunAsInvoker`). Zero administrator credentials, UAC elevation, or background OS daemon services are permitted or required.
7. **Local-First & Zero-Egress by Default (`INV-EGR-07`)**: The default mock backend and offline test harness emit zero telemetry, network sockets, analytics, or mandatory cloud requests.
8. **Zero Secret Persistence (`INV-SEC-08`)**: Cloud API keys (`anthropic`, `openai`) are held strictly in ephemeral runtime memory or environment variables and are never persisted to disk, state caches, or logs.
9. **Profile-Filtered Perception & Window Scoping (`INV-SCP-09`)**: Strict token-budgeted GUI window scoping and background blanking prevent model context bloat and ensure background windows remain uncaptured.
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

**Zero-Copyleft Guarantee:** Utilizing open-compute to automate desktops, orchestrate workflows, or capture UI perceptions does **not** subject user codebases, model weights, prompts, or generated artifacts to any viral copyleft licensing obligations.

---

<a id="deutsch"></a>
## Deutsch

Vielen Dank für Ihr Interesse an einer Mitarbeit an **open-compute** (`ellmos-ai/open-compute`), einem modell-agnostischen, abhängigkeitsarmen Computer-Use-Kern für autonome KI-Agenten und Desktop-Automatisierung über Anthropic Claude, OpenAI CUA und deterministische Offline-Mock-Backends.

### 1. Architektur-Prinzipien & 10 Governance-Invarianten

Alle Beiträge müssen unsere 10 verbindlichen Architektur- und Laufzeitinvarianten strikt einhalten:

1. **Modell-Agnostischer Kern-Loop (`INV-MOD-01`)**: Einheitliche Loop-Orchestrierung (`open_compute.base.ComputerBackend`) für Claude (Messages API), OpenAI CUA und deterministische Offline-Mock-Ausführung ohne Anbieterabhängigkeit.
2. **Keine obligatorischen Laufzeitabhängigkeiten (`INV-DEP-02`)**: Der Kern-Loop, das kanonische Aktionsschema, die Koordinatennormalisierung und das Sicherheits-Gate basieren ausschließlich auf der Python-Standardbibliothek (`dependencies = []`). Externe Vendor-SDKs (`anthropic`, `openai`, `mss`, `playwright`, `Pillow`, `uiautomation`, `windows-capture`, `mcp`) sind optionale, spät geladene Extras.
3. **Normalisierte Koordinaten 0..1 (`INV-CRD-03`)**: Alle visuellen Koordinatenwahrnehmungen und Aktionen arbeiten im invarianten Float-Bereich `[0.0, 1.0]`, mit zentraler DPI- und Multi-Monitor-Umrechnung in `open_compute.coordinates`.
4. **Verbindliches Pre-Action-Grace-Window (`INV-GRC-04`)**: Bedingungslose 4-Sekunden-Verzögerung vor der ersten mutierenden GUI-Aktion für Notfallabbruch und Bedieneraufsicht.
5. **Fail-Closed Sicherheits-Gate (`INV-SAF-05`)**: Dreistufige Richtlinie (`confirm`, `allow_all`, `read_only`), die jede GUI-Aktion (Klick, Tastendruck, Drag, Hotkey, Shell-Befehl) vor der Betriebssystemausführung abfängt.
6. **Unprivilegierter Modus ohne Elevation (`INV-USR-06`)**: Vollständige Funktionalität im unprivilegierten `RunAsInvoker`-Benutzermodus. Keine Administratorrechte, UAC-Elevation oder Hintergrunddienste erforderlich.
7. **Local-First & Zero-Egress standardmäßig (`INV-EGR-07`)**: Das standardmäßige Mock-Backend und das Offline-Test-Setup übertragen null Telemetrie, Netzwerk-Sockets, Analysen oder Cloud-Anfragen.
8. **Keine Geheimnis-Persistierung (`INV-SEC-08`)**: Cloud-API-Schlüssel (`anthropic`, `openai`) verbleiben flüchtig im Arbeitsspeicher oder Umgebungsvariablen und werden niemals auf Festplatte, Zustands-Caches oder Logs geschrieben.
9. **Profilgefilterte Wahrnehmung & Fenster-Scoping (`INV-SCP-09`)**: Strikte token-budgetierte GUI-Fensterfokussierung verhindert Kontextüberlauf und stellt sicher, dass Hintergrundfenster unberührt bleiben.
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

**Zero-Copyleft-Garantie:** Die Verwendung von open-compute zur Desktop-Automatisierung oder Orchestrierung unterwirft Nutzerquelltexte, Modellgewichte, Prompts oder generierte Artefakte **keinen** viralen Copyleft-Lizenzpflichten.
