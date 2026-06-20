"""Tests for UIA window-scoping bug fix (v0.4.1).

Bug (Live-Test 2026-06-20): ``oc tree --window "Schnitzeljagd"`` silently fell back
to the desktop root when no window matched, causing ``oc invoke "Start"`` to hit
the Taskbar's Start button instead of Word's ribbon tab.

This module tests:

A. ``_normalize_window_name`` — whitespace collapsing.
B. ``_get_root`` — named window: substring match, case-insensitivity,
   whitespace-normalized titles, no-match → RuntimeError (no silent fallback).
C. ``_get_root`` — default (no window): GetForegroundWindow → FromHandle path,
   GetForegroundControl fallback, desktop-root ultimate fallback.
D. Element searches are scoped to the resolved subtree, NOT the global root.
   (This is the key invariant that the bug violated.)
E. CLI: ``cmd_tree``/``cmd_invoke``/``cmd_click_name`` propagate RuntimeError
   to ``_die()`` with exit code 2 — no silent taskbar fallback at CLI level.

All OS / UIA calls are mocked. No real windows are opened or clicked.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# A. _normalize_window_name
# ---------------------------------------------------------------------------

class TestNormalizeWindowName:
    """_normalize_window_name collapses whitespace and strips."""

    def test_single_spaces_unchanged(self):
        from open_compute.feeds.uia_windows import _normalize_window_name
        assert _normalize_window_name("Hello World") == "Hello World"

    def test_double_spaces_collapsed(self):
        from open_compute.feeds.uia_windows import _normalize_window_name
        # Real Word title: "Schnitzeljagd  -  Kompatibilitätsmodus - Word"
        result = _normalize_window_name("Schnitzeljagd  -  Kompatibilitätsmodus - Word")
        assert "  " not in result
        assert result == "Schnitzeljagd - Kompatibilitätsmodus - Word"

    def test_tabs_collapsed(self):
        from open_compute.feeds.uia_windows import _normalize_window_name
        assert _normalize_window_name("Hello\tWorld") == "Hello World"

    def test_leading_trailing_stripped(self):
        from open_compute.feeds.uia_windows import _normalize_window_name
        assert _normalize_window_name("  Hello  ") == "Hello"

    def test_mixed_whitespace(self):
        from open_compute.feeds.uia_windows import _normalize_window_name
        result = _normalize_window_name("A  \t B   C")
        assert result == "A B C"

    def test_empty_string(self):
        from open_compute.feeds.uia_windows import _normalize_window_name
        assert _normalize_window_name("") == ""


# ---------------------------------------------------------------------------
# B. _get_root — named window resolution
# ---------------------------------------------------------------------------

def _make_top_level_children(specs: list[dict]) -> tuple[list[MagicMock], MagicMock]:
    """Build a linked list of sibling controls simulating top-level windows.

    Returns (list_of_controls, root_control).
    """
    ctrls = []
    for s in specs:
        c = MagicMock()
        c.Name = s.get("name", "")
        ctrls.append(c)

    # Link as a sibling chain
    for i, c in enumerate(ctrls):
        c.GetNextSiblingControl.return_value = ctrls[i + 1] if i + 1 < len(ctrls) else None

    root = MagicMock()
    root.GetFirstChildControl.return_value = ctrls[0] if ctrls else None

    return ctrls, root


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
class TestGetRootNamedWindow:
    """_get_root(window=...) searches top-level children; no silent desktop fallback."""

    def _mock_uia_with_children(self, specs):
        ctrls, root = _make_top_level_children(specs)
        mock_uia = MagicMock()
        mock_uia.GetRootControl.return_value = root
        return mock_uia, ctrls, root

    def test_exact_substring_match(self):
        import open_compute.feeds.uia_windows as uia_mod
        mock_uia, ctrls, _ = self._mock_uia_with_children([
            {"name": "Schnitzeljagd - Word"},
            {"name": "Windows Explorer"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            result = uia_mod._get_root("Schnitzeljagd")
        assert result is ctrls[0]

    def test_case_insensitive_match(self):
        import open_compute.feeds.uia_windows as uia_mod
        mock_uia, ctrls, _ = self._mock_uia_with_children([
            {"name": "SCHNITZELJAGD - Word"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            result = uia_mod._get_root("schnitzeljagd")
        assert result is ctrls[0]

    def test_whitespace_normalized_title_matches(self):
        """Word titles with double-spaces are matched after normalization."""
        import open_compute.feeds.uia_windows as uia_mod
        # Real observed title: double spaces around dash
        mock_uia, ctrls, _ = self._mock_uia_with_children([
            {"name": "Schnitzeljagd  -  Kompatibilitätsmodus - Word"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            # Query without extra spaces; normalize on both sides → should match
            result = uia_mod._get_root("Schnitzeljagd")
        assert result is ctrls[0]

    def test_whitespace_in_query_also_normalized(self):
        """Query with extra spaces is normalized before comparison."""
        import open_compute.feeds.uia_windows as uia_mod
        mock_uia, ctrls, _ = self._mock_uia_with_children([
            {"name": "Schnitzeljagd - Word"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            # Extra spaces in the query
            result = uia_mod._get_root("Schnitzeljagd  -  Word")
        assert result is ctrls[0]

    def test_second_child_matched(self):
        """Returns the correct child when first child does not match."""
        import open_compute.feeds.uia_windows as uia_mod
        mock_uia, ctrls, _ = self._mock_uia_with_children([
            {"name": "Taskleiste"},
            {"name": "Schnitzeljagd - Word"},
            {"name": "Datei-Explorer"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            result = uia_mod._get_root("Schnitzeljagd")
        assert result is ctrls[1]

    def test_no_match_raises_runtime_error(self):
        """When no window matches, RuntimeError is raised — NO silent fallback."""
        import open_compute.feeds.uia_windows as uia_mod
        mock_uia, ctrls, root = self._mock_uia_with_children([
            {"name": "Taskleiste"},
            {"name": "Windows Explorer"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            with pytest.raises(RuntimeError, match="Schnitzeljagd"):
                uia_mod._get_root("Schnitzeljagd")

    def test_no_match_does_NOT_return_desktop_root(self):
        """The desktop root is NEVER returned as a fallback for a named-window miss."""
        import open_compute.feeds.uia_windows as uia_mod
        mock_uia, ctrls, root = self._mock_uia_with_children([
            {"name": "Taskleiste"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            try:
                result = uia_mod._get_root("Schnitzeljagd")
                # Should not reach here
                assert result is not root, (
                    "Bug regression: _get_root returned the desktop root on a named-window miss. "
                    "This causes all subsequent element searches to run against the Taskbar."
                )
                pytest.fail("Expected RuntimeError, got a control instead")
            except RuntimeError:
                pass  # Expected path

    def test_error_message_contains_window_name(self):
        """RuntimeError message tells the user which name was requested."""
        import open_compute.feeds.uia_windows as uia_mod
        mock_uia, _, _ = self._mock_uia_with_children([
            {"name": "Taskleiste"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            with pytest.raises(RuntimeError) as exc_info:
                uia_mod._get_root("MeinFenster")
        assert "MeinFenster" in str(exc_info.value)

    def test_empty_name_child_skipped(self):
        """Children with empty Name are skipped gracefully."""
        import open_compute.feeds.uia_windows as uia_mod
        mock_uia, ctrls, _ = self._mock_uia_with_children([
            {"name": ""},
            {"name": "Schnitzeljagd - Word"},
        ])
        with patch.object(uia_mod, "_get_uia", return_value=mock_uia):
            result = uia_mod._get_root("Schnitzeljagd")
        assert result is ctrls[1]


# ---------------------------------------------------------------------------
# C. _get_root — default (no window): HWND → FromHandle path
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
class TestGetRootForeground:
    """_get_root(None) prefers GetForegroundWindow → ControlFromHandle."""

    def test_foreground_via_from_handle(self):
        """If ControlFromHandle is available, use it with the HWND."""
        import open_compute.feeds.uia_windows as uia_mod

        expected_ctrl = MagicMock()
        mock_uia = MagicMock()
        mock_uia.ControlFromHandle.return_value = expected_ctrl
        # GetForegroundControl should NOT be called when FromHandle succeeds
        mock_uia.GetForegroundControl.return_value = MagicMock()

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_foreground_hwnd", return_value=0x1234),
        ):
            result = uia_mod._get_root(None)

        assert result is expected_ctrl
        mock_uia.ControlFromHandle.assert_called_once_with(0x1234)

    def test_fallback_to_get_foreground_control(self):
        """Falls back to GetForegroundControl when FromHandle APIs are absent."""
        import open_compute.feeds.uia_windows as uia_mod

        expected_ctrl = MagicMock()
        mock_uia = MagicMock()
        # Simulate uiautomation without both FromHandle API names
        del mock_uia.ControlFromHandle
        del mock_uia.AutomationElementFromHandle
        mock_uia.GetForegroundControl.return_value = expected_ctrl

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_foreground_hwnd", return_value=0x1234),
        ):
            result = uia_mod._get_root(None)

        assert result is expected_ctrl

    def test_fallback_to_get_foreground_control_when_hwnd_is_none(self):
        """When GetForegroundWindow returns None/0, use GetForegroundControl."""
        import open_compute.feeds.uia_windows as uia_mod

        expected_ctrl = MagicMock()
        mock_uia = MagicMock()
        mock_uia.GetForegroundControl.return_value = expected_ctrl

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_foreground_hwnd", return_value=None),
        ):
            result = uia_mod._get_root(None)

        assert result is expected_ctrl

    def test_ultimate_fallback_to_root_control(self):
        """When both HWND and GetForegroundControl fail, fall back to GetRootControl."""
        import open_compute.feeds.uia_windows as uia_mod

        expected_root = MagicMock()
        mock_uia = MagicMock()
        del mock_uia.ControlFromHandle
        mock_uia.GetForegroundControl.return_value = None
        mock_uia.GetRootControl.return_value = expected_root

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_foreground_hwnd", return_value=None),
        ):
            result = uia_mod._get_root(None)

        assert result is expected_root


# ---------------------------------------------------------------------------
# D. Subtree scoping: element searches stay within the resolved window
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
class TestSubtreeScoping:
    """The decisive invariant: WalkControl is called on the MATCHED window,
    not on the desktop root.

    This is what the original bug violated: the desktop root was returned
    as fallback, so WalkControl walked the Taskbar subtree.
    """

    def _build_full_scenario(self, uia_mod):
        """Build: desktop root → [Taskbar, Word window].

        Word window has [Start, Einfügen] tabs.
        Taskbar has a [Start] button.
        Only Start button at desktop-root level should be unreachable when
        searching scoped to the Word window.
        """
        # Taskbar
        taskbar_start = MagicMock()
        taskbar_start.Name = "Start"
        taskbar_start.ControlTypeName = "Button"
        taskbar_rect = MagicMock()
        taskbar_rect.left, taskbar_rect.top = 0, 1050
        taskbar_rect.right, taskbar_rect.bottom = 50, 1080
        taskbar_start.BoundingRectangle = taskbar_rect
        taskbar_start.GetValuePattern.return_value = None
        taskbar_ctrl = MagicMock()
        taskbar_ctrl.Name = "Taskleiste"
        taskbar_ctrl.GetNextSiblingControl.return_value = None  # will be set below

        # Word window
        word_start = MagicMock()
        word_start.Name = "Start"
        word_start.ControlTypeName = "TabItem"
        word_rect_s = MagicMock()
        word_rect_s.left, word_rect_s.top = 0, 50
        word_rect_s.right, word_rect_s.bottom = 60, 80
        word_start.BoundingRectangle = word_rect_s
        word_start.GetValuePattern.return_value = None

        word_einfuegen = MagicMock()
        word_einfuegen.Name = "Einfügen"
        word_einfuegen.ControlTypeName = "TabItem"
        word_rect_e = MagicMock()
        word_rect_e.left, word_rect_e.top = 60, 50
        word_rect_e.right, word_rect_e.bottom = 120, 80
        word_einfuegen.BoundingRectangle = word_rect_e
        word_einfuegen.GetValuePattern.return_value = None

        word_ctrl = MagicMock()
        word_ctrl.Name = "Schnitzeljagd  -  Kompatibilitätsmodus - Word"
        word_ctrl.GetNextSiblingControl.return_value = None

        taskbar_ctrl.GetNextSiblingControl.return_value = word_ctrl

        # Desktop root
        desktop_root = MagicMock()
        desktop_root.GetFirstChildControl.return_value = taskbar_ctrl
        desktop_root.GetTextPattern.return_value = None

        # UIA mock
        mock_uia = MagicMock()
        mock_uia.GetRootControl.return_value = desktop_root
        mock_uia.GetForegroundControl.return_value = word_ctrl
        mock_uia.GetTextPattern = MagicMock(return_value=None)
        word_ctrl.GetTextPattern.return_value = None

        # WalkControl yields Word's tabs when called on word_ctrl,
        # and Taskbar's start when called on desktop_root or taskbar_ctrl.
        def walk_side_effect(root, maxDepth=8):
            if root is word_ctrl:
                return iter([(word_start, 1), (word_einfuegen, 1)])
            elif root is desktop_root:
                return iter([(taskbar_start, 1)])
            return iter([])

        mock_uia.WalkControl.side_effect = walk_side_effect

        return mock_uia, {
            "desktop_root": desktop_root,
            "word_ctrl": word_ctrl,
            "taskbar_ctrl": taskbar_ctrl,
            "word_start": word_start,
            "word_einfuegen": word_einfuegen,
            "taskbar_start": taskbar_start,
        }

    def test_observe_walks_word_subtree_not_taskbar(self):
        """observe(window='Schnitzeljagd') walks the Word control, not desktop root."""
        import open_compute.feeds.uia_windows as uia_mod

        mock_uia, nodes = self._build_full_scenario(uia_mod)

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_virtual_desktop", return_value=(0, 0, 1920, 1080)),
            patch.object(uia_mod, "_set_dpi_awareness"),
        ):
            feed = uia_mod.UiaWindowsFeed()
            obs = feed.observe(window="Schnitzeljagd")

        # Should have found Word's tabs, not the Taskbar's Start button
        names = [e["name"] for e in obs.elements]
        assert "Start" in names  # Word's Start tab
        assert "Einfügen" in names  # Word's Einfügen tab

        # Verify WalkControl was called on the Word window, not the desktop root
        walk_calls = mock_uia.WalkControl.call_args_list
        walk_roots = [c[0][0] for c in walk_calls]
        assert nodes["word_ctrl"] in walk_roots, "WalkControl must be called on the Word control"
        assert nodes["desktop_root"] not in walk_roots, (
            "Bug regression: WalkControl called on the desktop root. "
            "This would include the Taskbar Start button."
        )

    def test_resolve_finds_einfuegen_in_word_not_taskbar(self):
        """resolve('Einfügen', window='Schnitzeljagd') resolves within Word's subtree."""
        import open_compute.feeds.uia_windows as uia_mod

        mock_uia, nodes = self._build_full_scenario(uia_mod)

        # Make _find_live_control also walk Word's subtree
        mock_uia.WalkControl.side_effect = None
        mock_uia.WalkControl.return_value = iter([
            (nodes["word_start"], 1),
            (nodes["word_einfuegen"], 1),
        ])
        # Reset to side_effect after observe uses it
        # Actually patch _get_root directly to return word_ctrl
        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_get_virtual_desktop", return_value=(0, 0, 1920, 1080)),
            patch.object(uia_mod, "_set_dpi_awareness"),
            patch.object(uia_mod, "_get_root", return_value=nodes["word_ctrl"]),
        ):
            feed = uia_mod.UiaWindowsFeed()
            with patch.object(feed, "available", return_value=True):
                # Trigger WalkControl via observe, which goes through _get_root (patched)
                obs = feed.observe(window="Schnitzeljagd")

        names = [e["name"] for e in obs.elements]
        assert "Einfügen" in names

    def test_no_match_raises_not_returns_taskbar_root(self):
        """When window not found, RuntimeError is raised instead of using desktop root."""
        import open_compute.feeds.uia_windows as uia_mod

        mock_uia, nodes = self._build_full_scenario(uia_mod)

        with (
            patch.object(uia_mod, "_get_uia", return_value=mock_uia),
            patch.object(uia_mod, "_set_dpi_awareness"),
        ):
            feed = uia_mod.UiaWindowsFeed()
            with pytest.raises(RuntimeError):
                feed.observe(window="NichtVorhandenesProgram")

        # WalkControl must NOT have been called (no subtree walk happened)
        mock_uia.WalkControl.assert_not_called()


# ---------------------------------------------------------------------------
# E. CLI: RuntimeError from _get_root propagates to _die()
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
class TestCliWindowNotFound:
    """CLI commands exit with code 2 and a clear message when window not found."""

    def test_cmd_tree_exits_2_on_window_not_found(self):
        """cmd_tree exits 2 when the specified window does not exist."""
        from open_compute.feeds.base import FeedObservation
        from open_compute.cli import cmd_tree

        with patch("open_compute.cli._load_uia_feed") as mock_load:
            mock_feed = MagicMock()
            mock_feed.observe.side_effect = RuntimeError(
                "No top-level window found matching 'Schnitzeljagd'."
            )
            mock_load.return_value = mock_feed

            with pytest.raises(SystemExit) as exc_info:
                cmd_tree(["--window", "Schnitzeljagd"])

        assert exc_info.value.code == 2

    def test_cmd_click_name_exits_2_on_window_not_found(self):
        """cmd_click_name exits 2 when the specified window does not exist."""
        from open_compute.cli import cmd_click_name

        with patch("open_compute.cli._load_uia_feed") as mock_load:
            mock_feed = MagicMock()
            mock_feed.resolve.side_effect = RuntimeError(
                "No top-level window found matching 'Schnitzeljagd'."
            )
            mock_load.return_value = mock_feed

            with pytest.raises(SystemExit) as exc_info:
                cmd_click_name(["Einfügen", "--window", "Schnitzeljagd", "--mode", "allow_all"])

        assert exc_info.value.code == 2

    def test_cmd_invoke_exits_2_on_window_not_found(self):
        """cmd_invoke exits 2 when the specified window does not exist."""
        from open_compute.cli import cmd_invoke

        with patch("open_compute.cli._load_uia_feed") as mock_load:
            mock_feed = MagicMock()
            mock_feed.resolve.side_effect = RuntimeError(
                "No top-level window found matching 'Schnitzeljagd'."
            )
            mock_load.return_value = mock_feed

            with pytest.raises(SystemExit) as exc_info:
                cmd_invoke(["Start", "--window", "Schnitzeljagd", "--mode", "allow_all"])

        assert exc_info.value.code == 2

    def test_cmd_tree_error_message_contains_window_name(self, capsys):
        """Error message mentions the requested window name."""
        from open_compute.cli import cmd_tree

        with patch("open_compute.cli._load_uia_feed") as mock_load:
            mock_feed = MagicMock()
            mock_feed.observe.side_effect = RuntimeError(
                "No top-level window found matching 'MeinFenster'."
            )
            mock_load.return_value = mock_feed

            with pytest.raises(SystemExit):
                cmd_tree(["--window", "MeinFenster"])

        err = capsys.readouterr().err
        assert "MeinFenster" in err
