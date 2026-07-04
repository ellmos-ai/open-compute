# -*- coding: utf-8 -*-
"""Regressionstests fuer die Safety-Gate-Fixes aus dem Modul-Review 2026-07-04.

1. --ensure-foreground (Batch-/Label-Pfad) lief VOR der Policy-Auswertung:
   selbst im read_only-Modus fand der reale Fensterfokus-Wechsel statt.
2. oc rec replay fuehrte .clirec-Aktionen ungegatet gegen den rohen
   LocalExecutor aus — _GatedExecutor legt jetzt SafetyPolicy.evaluate()
   um jede Replay-Aktion.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from open_compute.actions import Action, ActionType  # noqa: E402
from open_compute.cli import _GatedExecutor  # noqa: E402
from open_compute.safety import SafetyPolicy  # noqa: E402


def _fake_obs():
    obs = MagicMock()
    obs.width = 1000
    obs.height = 500
    obs.screenshot = b""
    return obs


class TestEnsureForegroundGating:
    """Fokus-Wechsel darf erst NACH bestandenem Gate laufen (Batch-Pfad)."""

    def test_read_only_batch_never_activates_window(self, capsys, monkeypatch):
        monkeypatch.delenv("OC_ALWAYS_FOREGROUND", raising=False)
        with (
            patch("open_compute.cli._load_local_executor") as mock_exec,
            patch("open_compute.cli._get_foreground_title", return_value="Notepad"),
        ):
            mock_executor = MagicMock()
            mock_executor.execute.return_value = _fake_obs()
            mock_exec.return_value = mock_executor

            from open_compute.cli import cmd_do
            with pytest.raises(SystemExit) as exc_info:
                cmd_do([
                    '[{"type":"left_click","x":0.5,"y":0.5}]',
                    "--mode", "read_only",
                    "--ensure-foreground", "Word",
                ])
            assert exc_info.value.code == 1
            mock_executor.activate_window.assert_not_called()
            mock_executor.execute.assert_not_called()

    def test_allow_all_batch_still_activates_once(self, capsys, monkeypatch):
        monkeypatch.delenv("OC_ALWAYS_FOREGROUND", raising=False)
        with (
            patch("open_compute.cli._load_local_executor") as mock_exec,
            patch("open_compute.cli._get_foreground_title", return_value="Notepad"),
        ):
            mock_executor = MagicMock()
            mock_executor.execute.return_value = _fake_obs()
            mock_exec.return_value = mock_executor

            from open_compute.cli import cmd_do
            cmd_do([
                '[{"type":"mouse_move","x":0.1,"y":0.1},'
                ' {"type":"mouse_move","x":0.2,"y":0.2}]',
                "--mode", "allow_all",
                "--ensure-foreground", "Word",
            ])
            mock_executor.activate_window.assert_called_once_with("Word")
            assert mock_executor.execute.call_count == 2


class TestGatedExecutor:
    """_GatedExecutor: jede Replay-Aktion passiert das Gate."""

    def test_read_only_denies_click_without_executing(self):
        inner = MagicMock()
        gated = _GatedExecutor(inner, SafetyPolicy(mode="read_only"))
        with pytest.raises(PermissionError) as exc_info:
            gated.execute(Action(type=ActionType.LEFT_CLICK, x=0.5, y=0.5))
        assert "left_click" in str(exc_info.value)
        inner.execute.assert_not_called()

    def test_confirm_mode_blocks_risky_action(self):
        inner = MagicMock()
        gated = _GatedExecutor(inner, SafetyPolicy(mode="confirm"))
        with pytest.raises(PermissionError):
            gated.execute(Action(type=ActionType.TYPE, text="hello"))
        inner.execute.assert_not_called()

    def test_allow_all_passes_through(self):
        inner = MagicMock()
        inner.execute.return_value = "obs"
        gated = _GatedExecutor(inner, SafetyPolicy(mode="allow_all"))
        result = gated.execute(Action(type=ActionType.LEFT_CLICK, x=0.5, y=0.5))
        assert result == "obs"
        inner.execute.assert_called_once()

    def test_attribute_passthrough(self):
        inner = MagicMock()
        inner.width = 1000
        inner.height = 500
        gated = _GatedExecutor(inner, SafetyPolicy(mode="allow_all"))
        assert gated.width == 1000
        assert gated.height == 500


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
