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

        self.assertGreaterEqual(len(anchors_en), 12, "README.md must have >= 12 quick navigation anchor links")
        self.assertGreaterEqual(len(anchors_de), 12, "README_de.md must have >= 12 quick navigation anchor links")

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
            "security-policy--vulnerability-reporting",
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
            "sicherheitsrichtlinie--meldung-von-schwachstellen",
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
        self.assertIn('addopts = "-v"', pyproject_text)

    def test_security_policy_umbrella_contact_and_triage(self):
        sec_text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("security@open-bricks.org", sec_text)
        self.assertIn("security@ellmos.ai", sec_text)
        self.assertIn("5 business days", sec_text)
        self.assertIn("5 Werktagen", sec_text)
        self.assertIn("48 hours", sec_text)
        self.assertIn("48 Stunden", sec_text)


if __name__ == "__main__":
    unittest.main()
