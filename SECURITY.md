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
- **MCP Pre-Action Grace Window:** Covered MCP calls use a configured wait (4 seconds and a 120-second activity cooldown by default), independently of visual overlay display. The operator can set the canonical duration to 0 or use `OC_SIGNAL_GRACE_SECONDS=0`; per-call model arguments cannot lower that floor. Direct CLI commands and SDK `AgentLoop` runs do not use this MCP mechanism.
- **Untrusted Visual & DOM Input:** On-screen contents, web pages, and application UIs are treated as untrusted input prone to prompt-injection attacks. Central safety gating and human confirmation callbacks remain the primary defense.
- **Credentials and Artifacts:** Provider clients accept explicit API keys or SDK environment configuration; the package does not intentionally write those keys to its own state. Captures and action content are not universally redacted. They may contain sensitive data; screenshots can be saved locally, returned to an MCP host, or sent to a configured provider.
- **Profile-Filtered Perception:** Explicit filtered MCP calls apply the selected profile’s budgets and exclusions to returned content. Capture occurs locally before configured window intersections are blanked. Ordinary `capture` does not apply these filters and may return the full virtual desktop.

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
- **MCP-Karenzfenster:** Abgedeckte MCP-Aufrufe verwenden eine konfigurierte Wartezeit (standardmäßig 4 Sekunden mit 120 Sekunden Aktivitäts-Cooldown), unabhängig von einer sichtbaren Einblendung. Der Betreiber kann den kanonischen Wert oder `OC_SIGNAL_GRACE_SECONDS` auf 0 setzen; Modellargumente einzelner Aufrufe können diesen Mindestwert nicht absenken. Direkte CLI-Befehle und SDK-`AgentLoop`-Läufe nutzen diesen MCP-Mechanismus nicht.
- **Schutz vor Prompt-Injections:** Visuelle Bildschirminhalte und fremde UIs gelten als nicht vertrauenswürdige Eingaben.
- **Zugangsdaten und Artefakte:** Provider-Clients akzeptieren explizite API-Schlüssel oder die SDK-Umgebungskonfiguration; das Paket schreibt diese Schlüssel nicht absichtlich in seinen eigenen Zustand. Aufnahmen und Aktionsinhalte werden nicht allgemein automatisch geschwärzt und können sensible Daten enthalten. Screenshots können lokal gespeichert, an einen MCP-Host zurückgegeben oder an einen konfigurierten Provider gesendet werden.
- **Gefilterte Wahrnehmung:** Explizite gefilterte MCP-Aufrufe wenden Budgets und Ausschlüsse des gewählten Profils auf zurückgegebene Inhalte an. Die lokale Aufnahme erfolgt vor dem Ausblenden konfigurierter Fensterüberschneidungen. Normales `capture` verwendet diese Filter nicht und kann den vollständigen virtuellen Desktop liefern.
