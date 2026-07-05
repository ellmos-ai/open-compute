"""Tests for the MCP server i18n layer (open_compute.mcp_i18n).

Guarantees every one of the six supported languages covers every tool (a missing
translation fails loudly) and that the server wires the localized descriptions in.
"""

import asyncio

import pytest

pytest.importorskip("mcp")  # server + i18n exercised together

from open_compute import mcp_i18n  # noqa: E402

_NON_EN = ("de", "es", "ja", "ru", "zh")


def test_supported_is_the_six_convention_languages():
    assert set(mcp_i18n.SUPPORTED) == {"en", "de", "es", "ja", "ru", "zh"}


def test_every_language_covers_every_tool():
    for key in mcp_i18n.tool_keys():
        for lang in mcp_i18n.SUPPORTED:
            desc = mcp_i18n.tool_description(key, lang)
            assert desc, f"missing/empty {lang} description for tool {key!r}"


def test_non_english_is_actually_translated():
    # every non-English string must differ from English (catch silent fallbacks)
    for key in mcp_i18n.tool_keys():
        en = mcp_i18n.tool_description(key, "en")
        for lang in _NON_EN:
            assert mcp_i18n.tool_description(key, lang) != en, f"{lang}/{key} not translated"


def test_instructions_localized_for_all_languages():
    for lang in mcp_i18n.SUPPORTED:
        assert mcp_i18n.instructions(lang)
    for lang in _NON_EN:
        assert mcp_i18n.instructions(lang) != mcp_i18n.instructions("en")


def test_language_selection_and_fallback(monkeypatch):
    monkeypatch.setenv("OC_LANGUAGE", "de")
    assert mcp_i18n.current_language() == "de"
    monkeypatch.setenv("OC_LANGUAGE", "ZH")
    assert mcp_i18n.current_language() == "zh"  # case-insensitive
    monkeypatch.setenv("OC_LANGUAGE", "xx")
    assert mcp_i18n.current_language() == "en"  # unknown -> English
    monkeypatch.delenv("OC_LANGUAGE", raising=False)
    assert mcp_i18n.current_language() == "en"  # unset -> English


def test_unknown_tool_key_returns_empty():
    assert mcp_i18n.tool_description("does_not_exist", "en") == ""


def test_server_wires_localized_descriptions():
    # server imported with default (unset) OC_LANGUAGE -> English descriptions
    from open_compute import mcp_server as S
    tools = asyncio.run(S.mcp.list_tools())
    by_name = {t.name: t for t in tools}
    for key in mcp_i18n.tool_keys():
        assert key in by_name, f"tool {key} not registered"
        assert by_name[key].description == mcp_i18n.tool_description(key, "en")
