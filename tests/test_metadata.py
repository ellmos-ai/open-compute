# -*- coding: utf-8 -*-
"""Contract tests for documentation, metadata, discoverability, and bilingual parity."""

import re
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
if not ROOT.exists():
    # Fallback to direct path
    ROOT = Path(r"C:\_Local_DEV\repos\open-compute")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class MetadataAndDiscoverabilityContractTests(unittest.TestCase):
    """Verifies that documentation, metadata, and design contracts are strictly fulfilled."""

    def test_readme_and_de_existence(self):
        readme_en = ROOT / "README.md"
        readme_de = ROOT / "README_de.md"
        self.assertTrue(readme_en.exists(), "README.md must exist")
        self.assertTrue(readme_de.exists(), "README_de.md must exist")
        self.assertGreater(readme_en.stat().st_size, 4000, "README.md must be comprehensive (>4000 bytes)")
        self.assertGreater(readme_de.stat().st_size, 4000, "README_de.md must be comprehensive (>4000 bytes)")

    def test_banner_and_status_badges(self):
        banner_png = ROOT / "assets" / "banner.png"
        self.assertTrue(banner_png.exists(), "assets/banner.png must exist")

        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        self.assertIn('src="assets/banner.png"', readme_en)
        self.assertIn('src="assets/banner.png"', readme_de)

        # Check for status, python, and tests badges
        self.assertIn("badge/status-", readme_en)
        self.assertIn("badge/python-", readme_en)
        self.assertIn("tests.yml/badge.svg", readme_en)
        self.assertIn("badge/license-MIT", readme_en)
        self.assertIn("badge/LLM--Ready-llms.txt", readme_en)

    def test_quick_navigation_anchors(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        self.assertIn("## Quick Navigation", readme_en)
        self.assertIn("## Schnellnavigation", readme_de)

        anchors_en = re.findall(r"\[([^\]]+)\]\(#([^\)]+)\)", readme_en)
        anchors_de = re.findall(r"\[([^\]]+)\]\(#([^\)]+)\)", readme_de)

        self.assertGreaterEqual(len(anchors_en), 15, "README.md must have >= 15 quick navigation anchor links")
        self.assertGreaterEqual(len(anchors_de), 15, "README_de.md must have >= 15 quick navigation anchor links")

        # Key anchor targets exist in English
        expected_anchors_en = [
            "highlights--core-philosophy",
            "system-architecture-flow",
            "agent-loop--safety-lifecycle",
            "governance--runtime-invariants",
            "sibling-ecosystem--partner-repositories",
            "why-open-compute",
            "mandatory-pre-action-grace-window",
            "profile-filtered-perception--window-scoping",
            "running-tests",
            "third-party-licenses--transparency",
            "security-policy--vulnerability-reporting",
            "license",
        ]
        for anchor in expected_anchors_en:
            self.assertIn(f"#{anchor}", readme_en, f"Anchor #{anchor} must be linked in README.md")

        # Key anchor targets exist in German
        expected_anchors_de = [
            "highlights--kernphilosophie",
            "systemarchitektur-ablauf",
            "agenten-loop--sicherheits-lebenszyklus",
            "governance--laufzeit-invarianten",
            "geschwisterwerkzeuge--partner-repositories",
            "warum-open-compute",
            "verbindliches-pre-action-grace-window",
            "profilgefilterte-wahrnehmung--fensterfokussierung",
            "tests-ausf\u00fchren",
            "drittanbieter-lizenzen--transparenz",
            "sicherheitsrichtlinie--meldung-von-schwachstellen",
            "lizenz",
        ]
        for anchor in expected_anchors_de:
            self.assertIn(f"#{anchor}", readme_de, f"Anchor #{anchor} must be linked in README_de.md")

    def test_bilingual_parity_code_blocks(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        code_blocks_en = re.findall(r"```([a-zA-Z0-9_\-]+)?\n", readme_en)
        code_blocks_de = re.findall(r"```([a-zA-Z0-9_\-]+)?\n", readme_de)

        self.assertEqual(
            len(code_blocks_en),
            len(code_blocks_de),
            f"Code block count must match: {len(code_blocks_en)} vs {len(code_blocks_de)}",
        )

    def test_mermaid_diagrams_syntax(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        self.assertIn("```mermaid\nflowchart TD", readme_en)
        self.assertIn("```mermaid\nsequenceDiagram", readme_en)

        self.assertIn("```mermaid\nflowchart TD", readme_de)
        self.assertIn("```mermaid\nsequenceDiagram", readme_de)

    def test_runtime_invariants_table(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        self.assertIn("## Governance & Runtime Invariants", readme_en)
        self.assertIn("## Governance & Laufzeit-Invarianten", readme_de)

        # Invariants IDs INV-MOD-01 to INV-SLA-10 in both READMEs
        for i in range(1, 11):
            inv_id = "INV-"
            if i == 1:
                inv_id += "MOD-01"
            elif i == 2:
                inv_id += "DEP-02"
            elif i == 3:
                inv_id += "CRD-03"
            elif i == 4:
                inv_id += "GRC-04"
            elif i == 5:
                inv_id += "SAF-05"
            elif i == 6:
                inv_id += "USR-06"
            elif i == 7:
                inv_id += "EGR-07"
            elif i == 8:
                inv_id += "SEC-08"
            elif i == 9:
                inv_id += "SCP-09"
            elif i == 10:
                inv_id += "SLA-10"

            self.assertIn(inv_id, readme_en, f"{inv_id} must be in English README invariants table")
            self.assertIn(inv_id, readme_de, f"{inv_id} must be in German README invariants table")

        # Invariants keywords
        self.assertIn("Model-Agnostic Core", readme_en)
        self.assertIn("Normalized Coordinates (0..1)", readme_en)
        self.assertIn("Mandatory Pre-Action Grace Window", readme_en)
        self.assertIn("Fail-Closed Safety Gate", readme_en)

        self.assertIn("Modellagnostischer Kern", readme_de)
        self.assertIn("Normierte Koordinaten (0..1)", readme_de)
        self.assertIn("Verbindliches Grace Window", readme_de)
        self.assertIn("Fail-Closed Safety Gate", readme_de)

    def test_sibling_ecosystem_matrix(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        self.assertIn("## Sibling Ecosystem & Partner Repositories", readme_en)
        self.assertIn("## Geschwisterwerkzeuge & Partner-Repositories", readme_de)

        partner_repos = [
            "ellmos-ai/bach",
            "ellmos-ai/usmc",
            "ellmos-ai/connectors",
            "ellmos-ai/clutch",
            "ellmos-ai/companion-for-agy",
            "ellmos-ai/system-auditor",
            "dev-bricks/lock-master",
            "dev-bricks/ticket-master",
            "dev-bricks/automation-master",
            "file-bricks/CloudLockFixer",
            "open-bricks/.github",
        ]
        for repo in partner_repos:
            self.assertIn(repo, readme_en, f"{repo} must be listed in English sibling ecosystem")
            self.assertIn(repo, readme_de, f"{repo} must be listed in German sibling ecosystem")

    def test_pyproject_pep621_metadata(self):
        pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("[project.urls]", pyproject_text)
        required_urls = [
            "Homepage",
            "Documentation",
            "Repository",
            "Issues",
            "Changelog",
            "Security",
            "Third-Party Licenses",
            "Marketing Log",
            "LLM Ready",
            "Parent Organization",
            "Umbrella Ecosystem",
        ]
        for url_key in required_urls:
            key_repr = f'"{url_key}" =' if " " in url_key else f"{url_key} ="
            self.assertIn(key_repr, pyproject_text, f"Missing URL key {url_key} in pyproject.toml")

        # Classifiers
        self.assertIn("Operating System :: Microsoft :: Windows", pyproject_text)
        self.assertIn("Operating System :: POSIX :: Linux", pyproject_text)
        self.assertIn("Operating System :: MacOS", pyproject_text)
        self.assertIn("Programming Language :: Python :: 3.13", pyproject_text)

    def test_security_policy_contract(self):
        security_path = ROOT / "SECURITY.md"
        self.assertTrue(security_path.exists(), "SECURITY.md must exist")

        sec_text = security_path.read_text(encoding="utf-8")
        self.assertIn("## English", sec_text)
        self.assertIn("## Deutsch", sec_text)
        self.assertIn("48 hours", sec_text)
        self.assertIn("48 Stunden", sec_text)
        self.assertIn("https://github.com/ellmos-ai/open-compute/security/advisories", sec_text)
        self.assertIn("security@ellmos.ai", sec_text)
        self.assertIn("security@open-bricks.org", sec_text)

    def test_llms_txt_presence_and_format(self):
        llms_path = ROOT / "llms.txt"
        self.assertTrue(llms_path.exists(), "llms.txt must exist")
        text = llms_path.read_text(encoding="utf-8")
        self.assertIn("# open-compute", text)
        self.assertIn("ComputerBackend", text)
        self.assertIn("coordinates", text)
        self.assertIn("safety", text)

    def test_ci_workflow_integrity(self):
        ci_path = ROOT / ".github" / "workflows" / "tests.yml"
        self.assertTrue(ci_path.exists(), "CI workflow tests.yml must exist")
        ci_text = ci_path.read_text(encoding="utf-8")
        self.assertIn("actions/checkout@v4", ci_text)
        self.assertIn("actions/setup-python@v5", ci_text)
        self.assertIn("concurrency:", ci_text)
        self.assertIn("ruff check .", ci_text)
        self.assertIn('"3.13"', ci_text)

    def test_gitignore_hygiene(self):
        gitignore_path = ROOT / ".gitignore"
        self.assertTrue(gitignore_path.exists(), ".gitignore must exist")
        gi_text = gitignore_path.read_text(encoding="utf-8")
        # Multi-agent locks
        self.assertIn("LOCK.*", gi_text)
        self.assertIn("*.lock", gi_text)
        self.assertIn("LOCK*.txt", gi_text)
        # Cloud sync conflicts
        self.assertIn("*.sync-conflict-*", gi_text)
        self.assertIn("*.conflict", gi_text)
        self.assertIn("*-CONFLIT-*", gi_text)
        self.assertIn("*-conflict-*", gi_text)
        self.assertIn("*.sync-temp-*", gi_text)
        # Caches & temps
        self.assertIn(".ruff_cache/", gi_text)
        self.assertIn(".coverage", gi_text)
        self.assertIn("*.tmp", gi_text)
        self.assertIn("*.bak", gi_text)

    def test_pyproject_pytest_configuration(self):
        pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.pytest.ini_options]", pyproject_text)
        self.assertIn('testpaths = ["tests"]', pyproject_text)
        self.assertIn('pythonpath = "."', pyproject_text)
        self.assertIn('addopts = "-ra -v"', pyproject_text)

    def test_security_policy_umbrella_contact_and_triage(self):
        sec_text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("security@open-bricks.org", sec_text)
        self.assertIn("security@ellmos.ai", sec_text)
        self.assertIn("5 business days", sec_text)
        self.assertIn("5 Werktagen", sec_text)
        self.assertIn("48 hours", sec_text)
        self.assertIn("48 Stunden", sec_text)

    def test_third_party_licenses_inventory(self):
        tpl_path = ROOT / "THIRD_PARTY_LICENSES.md"
        self.assertTrue(tpl_path.exists(), "THIRD_PARTY_LICENSES.md must exist in repository root")
        self.assertGreater(tpl_path.stat().st_size, 2000, "THIRD_PARTY_LICENSES.md must be comprehensive (>2000 bytes)")

        tpl_text = tpl_path.read_text(encoding="utf-8")
        # Ensure permissive licenses are documented
        self.assertIn("PSFL-2.0", tpl_text)
        self.assertIn("MIT", tpl_text)
        self.assertIn("Apache-2.0", tpl_text)
        self.assertIn("HPND", tpl_text)

        # Core runtime and optional dependencies
        deps = [
            "Python Standard Library",
            "anthropic",
            "openai",
            "playwright",
            "mss",
            "Pillow",
            "uiautomation",
            "windows-capture",
            "watchdog",
            "clirec",
            "mcp",
            "pytest",
            "ruff",
        ]
        for dep in deps:
            self.assertIn(dep, tpl_text, f"{dep} must be documented in THIRD_PARTY_LICENSES.md")

        # Zero-egress and user mode guarantees
        self.assertIn("Zero-Egress", tpl_text)
        self.assertIn("RunAsInvoker", tpl_text)

    def test_marketing_log_audit_and_personas(self):
        mkt_path = ROOT / "MARKETING-LOG.txt"
        self.assertTrue(mkt_path.exists(), "MARKETING-LOG.txt must exist in repository root")
        self.assertGreater(mkt_path.stat().st_size, 1500, "MARKETING-LOG.txt must be comprehensive (>1500 bytes)")

        mkt_text = mkt_path.read_text(encoding="utf-8")
        self.assertIn("2026-09-10", mkt_text)

        # 4 distinct personas
        self.assertIn("Persona 1:", mkt_text)
        self.assertIn("Persona 2:", mkt_text)
        self.assertIn("Persona 3:", mkt_text)
        self.assertIn("Persona 4:", mkt_text)

        # High-intent discovery keywords
        self.assertIn("High-Intent Suchbegriffe", mkt_text)
        self.assertIn("python computer use agent", mkt_text)
        self.assertIn("modellunabhaengige gui agenten", mkt_text)

        # Invariants and UVP
        self.assertIn("INV-MOD-01", mkt_text)
        self.assertIn("INV-SLA-10", mkt_text)
        self.assertIn("Alleinstellungsmerkmale", mkt_text)

    def test_readme_badges_and_parity_metrics(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        # Both must link to THIRD_PARTY_LICENSES.md and NOTICE
        self.assertIn("THIRD_PARTY_LICENSES.md", readme_en)
        self.assertIn("THIRD_PARTY_LICENSES.md", readme_de)
        self.assertIn("NOTICE", readme_en)
        self.assertIn("NOTICE", readme_de)

        # Both must have third-party badge
        self.assertIn("third--party-audited", readme_en)
        self.assertIn("drittanbieter-auditiert", readme_de)

        # Both must have 48h SLA and 5d triage badge
        self.assertIn("48h%20SLA%20%7C%205d%20triage", readme_en)
        self.assertIn("48h%20SLA%20%7C%205d%20triage", readme_de)

        # Both must report 823 tests passed
        self.assertIn("823%20passed", readme_en)
        self.assertIn("823%20bestanden", readme_de)

        # Both must have NOTICE attribution badge
        self.assertIn("attribution-NOTICE-informational", readme_en)
        self.assertIn("attribution-NOTICE-informational", readme_de)

    def test_ci_timeout_minutes_and_pytest_flags(self):
        ci_path = ROOT / ".github" / "workflows" / "tests.yml"
        self.assertTrue(ci_path.exists(), "tests.yml must exist")
        ci_text = ci_path.read_text(encoding="utf-8")
        self.assertIn("timeout-minutes: 15", ci_text)
        self.assertIn("-ra -v", ci_text)

    def test_pyproject_pep621_llm_ready_and_addopts(self):
        pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"LLM Ready" = "https://github.com/ellmos-ai/open-compute/blob/master/llms.txt"', pyproject_text)
        self.assertIn('addopts = "-ra -v"', pyproject_text)

    def test_lifecycle_workflows_present_and_configured(self):
        stale_path = ROOT / ".github" / "workflows" / "stale.yml"
        welcome_path = ROOT / ".github" / "workflows" / "welcome.yml"
        self.assertTrue(stale_path.exists(), "stale.yml must exist in .github/workflows")
        self.assertTrue(welcome_path.exists(), "welcome.yml must exist in .github/workflows")

        stale_text = stale_path.read_text(encoding="utf-8")
        self.assertIn("timeout-minutes: 10", stale_text)
        self.assertIn("issues: write", stale_text)
        self.assertIn("pull-requests: write", stale_text)
        self.assertIn("actions/stale@v9", stale_text)

        welcome_text = welcome_path.read_text(encoding="utf-8")
        self.assertIn("timeout-minutes: 5", welcome_text)
        self.assertIn("cancel-in-progress: true", welcome_text)
        self.assertIn("issues: write", welcome_text)
        self.assertIn("pull-requests: write", welcome_text)
        self.assertIn("actions/first-interaction@v3", welcome_text)

    def test_pyproject_pep621_and_pytest_hardening(self):
        pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license-files = ["LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.md"]', pyproject_text)
        self.assertIn('minversion = "7.0"', pyproject_text)
        self.assertIn(
            'norecursedirs = [".git", ".pytest_cache", "__pycache__", "build", "dist", ".venv"]',
            pyproject_text,
        )
        self.assertIn('select = ["E", "F", "W"]', pyproject_text)

    def test_gitignore_canonical_locks_and_multihost_defense(self):
        gitignore_path = ROOT / ".gitignore"
        self.assertTrue(gitignore_path.exists(), ".gitignore must exist")
        gi_text = gitignore_path.read_text(encoding="utf-8")
        # Canonical locks
        self.assertIn("LOCK\n", gi_text)
        self.assertIn("LOCK.*", gi_text)
        self.assertIn("LOCK*.txt", gi_text)
        self.assertIn("LOCK.user.*", gi_text)
        self.assertIn("LOCK.until.*", gi_text)
        self.assertIn("LOCK.condition.*", gi_text)
        self.assertIn("LOCK.permissions.json", gi_text)
        self.assertIn(".automation-lock", gi_text)
        self.assertIn("uv.lock", gi_text)
        self.assertIn("!package-lock.json", gi_text)
        # Multi-host sync conflict patterns
        self.assertIn("* (kopie)*", gi_text)
        self.assertIn("* (Kopie)*", gi_text)
        self.assertIn("* (copy)*", gi_text)
        self.assertIn("* (Copy)*", gi_text)
        self.assertIn("*conflicted copy*", gi_text)
        self.assertIn("*-WORKSTATION*", gi_text)
        self.assertIn("*-LAPTOP*", gi_text)
        self.assertIn("*-ASUS*", gi_text)
        self.assertIn("*-ASUS-GEI*", gi_text)
        self.assertIn("*-Mac Studio*", gi_text)
        self.assertIn("*-MacBook*", gi_text)
        self.assertIn("*.orig", gi_text)
        self.assertIn("*.rej", gi_text)
        # Caches
        self.assertIn(".coverage.*", gi_text)
        self.assertIn(".hypothesis/", gi_text)
        self.assertIn(".mypy_cache/", gi_text)
        self.assertIn(".tox/", gi_text)
        self.assertIn(".turbo/", gi_text)
        self.assertIn(".nyc_output/", gi_text)

    def test_changelog_recent_pfad_a_entry(self):
        changelog_path = ROOT / "CHANGELOG.md"
        self.assertTrue(changelog_path.exists(), "CHANGELOG.md must exist")
        cl_text = changelog_path.read_text(encoding="utf-8")
        self.assertIn("2026-09-12", cl_text)
        self.assertIn("2026-09-20", cl_text)
        self.assertIn("Pfad A", cl_text)
        self.assertIn("timeout-minutes: 15", cl_text)

    def test_marketing_log_recent_hygiene_entry(self):
        mkt_path = ROOT / "MARKETING-LOG.txt"
        self.assertTrue(mkt_path.exists(), "MARKETING-LOG.txt must exist")
        mkt_text = mkt_path.read_text(encoding="utf-8")
        self.assertIn("2026-09-12", mkt_text)
        self.assertIn("2026-09-20", mkt_text)
        self.assertIn("Pfad A", mkt_text)
        self.assertIn("timeout-minutes: 15", mkt_text)

    def test_target_personas_bilingual_contract(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        # English Personas and SEO
        self.assertIn("## Target Personas & Discoverability", readme_en)
        for persona_id in ["[PERSONA-01]", "[PERSONA-02]", "[PERSONA-03]", "[PERSONA-04]"]:
            self.assertIn(persona_id, readme_en)
        self.assertIn("python computer use agent core", readme_en)
        self.assertIn("claude computer use alternative python", readme_en)
        self.assertIn("normalized coordinates screen automation", readme_en)

        # German Personas and SEO
        self.assertIn("## Zielgruppen & Auffindbarkeit", readme_de)
        for persona_id in ["[PERSONA-01]", "[PERSONA-02]", "[PERSONA-03]", "[PERSONA-04]"]:
            self.assertIn(persona_id, readme_de)
        self.assertIn("ki desktop automatisierung python framework", readme_de)
        self.assertIn("modellunabhaengige gui agenten steuerung", readme_de)
        self.assertIn("sichere desktop ki automatisierung fail closed", readme_de)

    def test_comparative_matrix_vs_alternatives_contract(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        # English Comparative Matrix
        self.assertIn("## Comparative Matrix vs. Alternatives", readme_en)
        self.assertIn("Anthropic Reference Demo", readme_en)
        self.assertIn("OSWorld / Agent-S Benchmark Frameworks", readme_en)
        self.assertIn("Classical RPA Tools", readme_en)
        self.assertIn("Ad-Hoc Scripts", readme_en)

        # German Comparative Matrix
        self.assertIn("## Vergleichsmatrix gegenüber Alternativen", readme_de)
        self.assertIn("Anthropic Referenz-Demo", readme_de)
        self.assertIn("OSWorld / Agent-S Benchmark-Frameworks", readme_de)
        self.assertIn("Klassische RPA-Tools", readme_de)
        self.assertIn("Ad-Hoc-Skripte", readme_de)

        # Invariant dimension mapping in both
        for inv_id in [
            "INV-MOD-01", "INV-DEP-02", "INV-CRD-03", "INV-GRC-04", "INV-SAF-05",
            "INV-USR-06", "INV-EGR-07", "INV-SEC-08", "INV-SCP-09", "INV-SLA-10",
        ]:
            self.assertIn(inv_id, readme_en)
            self.assertIn(inv_id, readme_de)

    def test_quick_navigation_slug_parity_contract(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        # Specific slug anchors
        self.assertIn("#target-personas--discoverability", readme_en)
        self.assertIn("#zielgruppen--auffindbarkeit", readme_de)
        self.assertIn("#comparative-matrix-vs-alternatives", readme_en)
        self.assertIn("#vergleichsmatrix-gegenüber-alternativen", readme_de)

        # Parity in anchor count within the Quick Navigation section
        nav_en = readme_en.split("## Quick Navigation")[1].split("---")[0]
        nav_de = readme_de.split("## Schnellnavigation")[1].split("---")[0]
        anchors_en = re.findall(r"^- \[([^\]]+)\]\(#([^\)]+)\)", nav_en, flags=re.MULTILINE)
        anchors_de = re.findall(r"^- \[([^\]]+)\]\(#([^\)]+)\)", nav_de, flags=re.MULTILINE)
        self.assertEqual(len(anchors_en), 18, "README.md Quick Navigation must contain exactly 18 items")
        self.assertEqual(len(anchors_de), 18, "README_de.md Schnellnavigation must contain exactly 18 items")

    def test_version_and_badge_consistency(self):
        import open_compute
        self.assertEqual(open_compute.__version__, "0.9.1")

        pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "0.9.1"', pyproject_text)

        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")
        self.assertIn("status-0.9.1--stable-blue", readme_en)
        self.assertIn("status-0.9.1--stabil-blue", readme_de)

        llms_text = (ROOT / "llms.txt").read_text(encoding="utf-8")
        self.assertIn("Version: 0.9.1", llms_text)
        self.assertIn("Last-checked: 2026-09-23", llms_text)

        changelog_text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [0.9.1] - 2026-09-14", changelog_text)

        licenses_text = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
        self.assertIn("Audited:** 2026-09-23", licenses_text)
        for inv_id in ["INV-MOD-01", "INV-SLA-10"]:
            self.assertIn(inv_id, licenses_text)

    def test_notice_file_and_pyproject_contract(self):
        notice_path = ROOT / "NOTICE"
        self.assertTrue(notice_path.exists(), "NOTICE file must exist in repository root")
        notice_text = notice_path.read_text(encoding="utf-8")
        self.assertIn("Lukas Geiger", notice_text)
        self.assertIn("ellmos-ai", notice_text)
        self.assertIn("open-bricks", notice_text)

        pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('Notice = "https://github.com/ellmos-ai/open-compute/blob/master/NOTICE"', pyproject_text)
        # Verify 20 keywords in pyproject.toml
        keywords_match = re.search(r"keywords\s*=\s*\[(.*?)\]", pyproject_text, re.DOTALL)
        self.assertIsNotNone(keywords_match, "keywords list must exist in pyproject.toml")
        keywords = [kw.strip(' "\',\n') for kw in keywords_match.group(1).split(",") if kw.strip(' "\',\n')]
        self.assertEqual(len(keywords), 20, f"Expected exactly 20 keywords in pyproject.toml, found {len(keywords)}")

    def test_dual_anchor_receptive_links_contract(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")

        for i in range(1, 19):
            sec_id = f"sec-{i:02d}"
            self.assertIn(f'<a id="{sec_id}"></a>', readme_en, f"Anchor {sec_id} missing in README.md")
            self.assertIn(f'<a id="{sec_id}"></a>', readme_de, f"Anchor {sec_id} missing in README_de.md")

    def test_level1_sbom_invariants_table_contract(self):
        tpl_path = ROOT / "THIRD_PARTY_LICENSES.md"
        tpl_text = tpl_path.read_text(encoding="utf-8")
        self.assertIn("Level 1 SBOM Invariant Cross-Reference Matrix", tpl_text)
        for i in range(1, 11):
            inv_pattern = rf"INV-[A-Z]+-{i:02d}"
            self.assertRegex(tpl_text, inv_pattern, f"Invariant number {i:02d} must be in Level 1 SBOM table")

    def test_bgb521_statutory_liability_contract(self):
        readme_en = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (ROOT / "README_de.md").read_text(encoding="utf-8")
        llms_text = (ROOT / "llms.txt").read_text(encoding="utf-8")

        self.assertIn("521 BGB", readme_en)
        self.assertIn("Gefälligkeitsrecht", readme_en)
        self.assertIn("521 BGB", readme_de)
        self.assertIn("Gefälligkeitsrecht", readme_de)
        self.assertIn("521 BGB", llms_text)


if __name__ == "__main__":
    unittest.main()
