import os
import re

SKILL = os.path.join("skills", "clirec", "SKILL.md")


def test_skill_exists_and_has_frontmatter():
    assert os.path.exists(SKILL)
    text = open(SKILL, encoding="utf-8").read()
    assert text.startswith("---")
    assert re.search(r"name:\s*clirec", text)


def test_skill_documents_required_topics():
    text = open(SKILL, encoding="utf-8").read().lower()
    for needle in ["oc rec start", ".clirec", "selbst", "passwort", "ringpuffer", "referenz"]:
        assert needle in text, f"missing topic: {needle}"


def test_skill_has_no_ascii_umlaut_substitutes():
    text = open(SKILL, encoding="utf-8").read()
    # German content must use real umlauts, not ae/oe/ue substitutes in these words
    assert "fuer" not in text and "ueber" not in text
