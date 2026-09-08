# Security Policy / Sicherheitsrichtlinie

[English](#english) | [Deutsch](#deutsch)

---

## English

### Supported Versions

| Version | Supported | Status |
|:---|:---:|:---|
| `0.9.x` | ✅ | Active Maintenance (Current Release) |
| `< 0.9.0` | ❌ | End of Life / Upgrade Recommended |

### Reporting a Vulnerability

If you discover a security vulnerability in `open-compute`, please report it responsibly:

1. **Do not open a public issue.**
2. **Use GitHub Private Vulnerability Reporting (Preferred):**
   Submit a draft advisory via [GitHub Security Advisories](https://github.com/ellmos-ai/open-compute/security/advisories).
3. **Direct Email Contact:**
   Send an encrypted or detailed report to the security maintainers:
   - `security@ellmos.ai`
   - `security@open-bricks.org`
   - `support@lukasgeiger.com`
   - `lukas@open-bricks.org`
4. **Include actionable details:**
   - Detailed description of the vulnerability and reproduction steps.
   - Proof of concept (PoC) or execution trace.
   - Potential impact and affected backend / driver configurations.

### Response Time & SLA

- **Initial Acknowledgment:** Within **48 hours**.
- **Triage & Remediation Plan:** Within **5 business days**.
- **Coordinated Disclosure:** Security patches will be merged and released prior to public disclosure.

### Scope and Threat Model

`open-compute` orchestrates automated mouse, keyboard, and application interactions driven by LLM reasoning models. Please observe the core security invariants:

- **Run Real Backends in Isolation:** Always run untrusted agent loops within dedicated virtual machines (VMs), sandbox containers, or disposable test environments.
- **Keep a Human in the Loop:** The default `SafetyPolicy` mode is `confirm`, requiring explicit operator confirmation before executing potentially destructive actions (clicks, typing, key sequences, process launching). Never run in `allow_all` on production desktops.
- **Mandatory Pre-Action Grace Window:** Every gate-relevant tool call (`do`, `click_name`, `invoke`, `rec_replay`, `capture`) unconditionally enforces a mandatory grace period (default 4 seconds, 120-second cooldown) before executing actions in a session. This cannot be bypassed by model-side instructions or custom tool arguments.
- **Untrusted Visual & DOM Input:** On-screen contents, web pages, and application UIs are treated as untrusted input prone to prompt-injection attacks. Central safety gating and human confirmation callbacks remain the primary defense.
- **Zero Secret Persistence:** Vendor API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are read strictly from environment variables or secure keychains. Session artifacts, screenshots, and action logs never store or serialize credentials.
- **Profile-Filtered Perception:** MCP perception filters enforce strict token budgets, visual lens boundaries, and window allowlists, preventing accidental capture of sensitive background windows.

---

## Deutsch

### Unterstützte Versionen

| Version | Unterstützt | Status |
|:---|:---:|:---|
| `0.9.x` | ✅ | Aktive Pflege (Aktuelle Version) |
| `< 0.9.0` | ❌ | End of Life / Upgrade empfohlen |

### Schwachstelle melden

Falls Sie eine Sicherheitslücke in `open-compute` entdecken, melden Sie diese bitte verantwortungsvoll:

1. **Kein öffentliches GitHub-Issue eröffnen.**
2. **GitHub Private Vulnerability Reporting nutzen (bevorzugt):**
   Reichen Sie einen Entwurf über [GitHub Security Advisories](https://github.com/ellmos-ai/open-compute/security/advisories) ein.
3. **Direkter E-Mail-Kontakt:**
   Senden Sie einen vertraulichen Bericht an das Sicherheitsteam:
   - `security@ellmos.ai`
   - `security@open-bricks.org`
   - `support@lukasgeiger.com`
   - `lukas@open-bricks.org`
4. **Erforderliche Angaben:**
   - Detaillierte Beschreibung der Schwachstelle und Reproduktionsschritte.
   - Proof of Concept (PoC) oder Test-Trace.
   - Potenzielle Auswirkung und betroffene Backends / Treiber.

### Reaktionszeit & SLA

- **Erstrückmeldung:** Innerhalb von **48 Stunden**.
- **Triage & Behebungsplan:** Innerhalb von **5 Werktagen**.
- **Koordinierte Offenlegung:** Sicherheitskorrekturen werden vor der öffentlichen Bekanntgabe bereitgestellt.

### Sicherheitsmodell & Invarianten

- **Isolierte Ausführungsumgebung:** Führen Sie autonome Agenten-Loops stets in dedizierten VMs, Sandboxes oder Test-Containern aus.
- **Menschliche Freigabe (Human-in-the-Loop):** Die standardmäßige `SafetyPolicy` arbeitet im Modus `confirm` und verlangt vor zustandsverändernden Aktionen die explizite Bestätigung durch den Bediener.
- **Verbindliches Pre-Action Grace Window:** Vor der ersten Aktion erzwingt das System ein unumgehbares Karenzfenster (Standard: 4 Sekunden, 120s Cooldown), um Not-Aus-Eingaben jederzeit zu ermöglichen.
- **Schutz vor Prompt-Injections:** Visuelle Bildschirminhalte und fremde UIs gelten als nicht vertrauenswürdige Eingaben.
- **Null Geheimnis-Persistierung:** API-Schlüssel werden ausschließlich aus Umgebungsvariablen geladen und niemals in Screenshots, Logs oder Sessions gespeichert.
- **Gefilterte Wahrnehmung:** Profile-Filter beschränken die visuelle und semantische Erfassung strikt auf definierte Zielfenster.
