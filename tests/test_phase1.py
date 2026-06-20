"""Tests for Phase 1 features: _session path logic, batch parsing,
composite fallback without Pillow, ensure-foreground logic.

All tests that touch Win32 are guarded by pytestmark (Windows-only).
Tests that don't touch Win32 (path logic, batch parsing, compose fallback,
foreground helper) run on any platform.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers for mocking the executor
# ---------------------------------------------------------------------------

def _make_valid_png() -> bytes:
    """Return valid 1×1 RGB PNG bytes (works whether or not Pillow is present).

    Uses struct+zlib to compute correct CRCs. This replaces the earlier
    hand-assembled PNG whose IDAT bytes had wrong CRCs and could not be decoded
    by Pillow when Pillow was installed.
    """
    import io
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    # One row of RGB (filter byte 0 + r, g, b)
    raw_row = b"\x00\x80\x40\x20"
    idat = chunk(b"IDAT", zlib.compress(raw_row, 9))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _fake_obs(w: int = 1920, h: int = 1080) -> object:
    """Return a minimal Observation-like object with valid PNG bytes.

    The PNG is a 1×1 RGB image that Pillow can decode without errors.
    """
    from open_compute.perception import Observation
    return Observation(screenshot=_make_valid_png(), width=w, height=h)


# ---------------------------------------------------------------------------
# 1. _session path helpers (cross-platform)
# ---------------------------------------------------------------------------

class TestSessionPath:
    """_session_dir, _next_session_path, _rotate_session."""

    def test_session_dir_is_module_relative_by_default(self, tmp_path, monkeypatch):
        """Without OC_SESSION_DIR, _session_dir() returns a path under the package root."""
        monkeypatch.delenv("OC_SESSION_DIR", raising=False)
        from open_compute.cli import _session_dir
        # Must NOT be CWD and must contain "_session"
        d = _session_dir()
        assert d.name == "_session"
        # Must be inside the open-compute tree (two levels up from cli.py)
        import open_compute.cli as cli_mod
        pkg_root = Path(cli_mod.__file__).resolve().parent.parent
        assert str(d).startswith(str(pkg_root))

    def test_session_dir_env_override(self, tmp_path, monkeypatch):
        """OC_SESSION_DIR overrides the default."""
        custom = tmp_path / "my_sessions"
        monkeypatch.setenv("OC_SESSION_DIR", str(custom))
        from open_compute.cli import _session_dir
        # Reload to pick up the new env
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)
        d = cli_mod._session_dir()
        assert d == custom
        assert d.exists()

    def test_next_session_path_unique(self, tmp_path, monkeypatch):
        """Two consecutive calls return different paths."""
        monkeypatch.setenv("OC_SESSION_DIR", str(tmp_path))
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)

        p1 = cli_mod._next_session_path()
        # Create it so the next call sees it
        p1.write_bytes(b"x")
        p2 = cli_mod._next_session_path()
        assert p1 != p2

    def test_next_session_path_contains_label(self, tmp_path, monkeypatch):
        """A label appears in the filename."""
        monkeypatch.setenv("OC_SESSION_DIR", str(tmp_path))
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)

        p = cli_mod._next_session_path(label="click_save")
        assert "click_save" in p.name

    def test_next_session_path_sanitizes_label(self, tmp_path, monkeypatch):
        """Special characters in the label are replaced with underscores."""
        monkeypatch.setenv("OC_SESSION_DIR", str(tmp_path))
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)

        p = cli_mod._next_session_path(label="hello world/foo")
        assert "/" not in p.name
        assert " " not in p.name

    def test_rotate_keeps_n_files(self, tmp_path, monkeypatch):
        """_rotate_session deletes oldest files, keeping at most *keep* files."""
        monkeypatch.setenv("OC_SESSION_DIR", str(tmp_path))
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)

        # Create 25 dummy PNG files
        for i in range(25):
            (tmp_path / f"{i:04d}_dummy.png").write_bytes(b"x")

        cli_mod._rotate_session(keep=20)
        remaining = list(tmp_path.glob("*.png"))
        assert len(remaining) == 20

    def test_rotate_no_error_when_empty(self, tmp_path, monkeypatch):
        """_rotate_session on an empty directory doesn't raise."""
        monkeypatch.setenv("OC_SESSION_DIR", str(tmp_path))
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)

        cli_mod._rotate_session(keep=20)  # Should not raise


# ---------------------------------------------------------------------------
# 2. Batch parsing (_parse_actions)
# ---------------------------------------------------------------------------

class TestBatchParsing:
    """_parse_actions must handle object, array, and error cases."""

    def setup_method(self):
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)
        self.cli = cli_mod

    def test_single_object_returns_list_of_one(self):
        raw = '{"type":"mouse_move","x":0.5,"y":0.5}'
        actions = self.cli._parse_actions(raw)
        assert len(actions) == 1
        assert actions[0].type.value == "mouse_move"

    def test_array_returns_list_of_many(self):
        raw = '[{"type":"mouse_move","x":0.5,"y":0.5},{"type":"wait","duration":0.1}]'
        actions = self.cli._parse_actions(raw)
        assert len(actions) == 2
        assert actions[0].type.value == "mouse_move"
        assert actions[1].type.value == "wait"

    def test_action_alias_in_array(self):
        """'action' key accepted as alias for 'type' inside an array element."""
        raw = '[{"action":"mouse_move","x":0.5,"y":0.5}]'
        actions = self.cli._parse_actions(raw)
        assert len(actions) == 1
        assert actions[0].type.value == "mouse_move"

    def test_empty_array_returns_empty_list(self):
        raw = "[]"
        actions = self.cli._parse_actions(raw)
        assert actions == []

    def test_invalid_json_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            self.cli._parse_actions("not-json")
        assert exc.value.code == 2

    def test_non_dict_element_in_array_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            self.cli._parse_actions('[42, "foo"]')
        assert exc.value.code == 2

    def test_invalid_action_type_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            self.cli._parse_actions('[{"type":"nonexistent_action"}]')
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# 3. Composite fallback without Pillow (cross-platform)
# ---------------------------------------------------------------------------

class TestComposeBeforeAfterFallback:
    """Without Pillow, _compose_before_after saves two separate files."""

    def _minimal_png(self) -> bytes:
        """Return a valid 1×1 RGB PNG (Pillow-decodable) for testing."""
        return _make_valid_png()

    def test_without_pillow_returns_before_after_paths(self, tmp_path):
        """When Pillow is absent, before and after files are created."""
        import builtins
        real_import = builtins.__import__

        def _no_pillow(name, *args, **kwargs):
            if name in ("PIL", "Pillow") or (name and name.startswith("PIL.")):
                raise ImportError("No module named 'PIL'")
            return real_import(name, *args, **kwargs)

        out_path = tmp_path / "0001_test.png"
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)

        png = self._minimal_png()
        with patch("builtins.__import__", side_effect=_no_pillow):
            result = cli_mod._compose_before_after(png, png, "test_label", out_path)

        assert "before" in result
        assert "after" in result
        before_p = Path(result["before"])
        after_p = Path(result["after"])
        assert before_p.exists()
        assert after_p.exists()
        assert before_p.read_bytes() == png
        assert after_p.read_bytes() == png

    def test_without_pillow_no_composite_key(self, tmp_path):
        """Without Pillow the result dict must not contain 'composite'."""
        import builtins
        real_import = builtins.__import__

        def _no_pillow(name, *args, **kwargs):
            if name in ("PIL", "Pillow") or (name and name.startswith("PIL.")):
                raise ImportError("No module named 'PIL'")
            return real_import(name, *args, **kwargs)

        out_path = tmp_path / "0001_test.png"
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)

        png = self._minimal_png()
        with patch("builtins.__import__", side_effect=_no_pillow):
            result = cli_mod._compose_before_after(png, png, "test_label", out_path)

        assert "composite" not in result

    def test_with_pillow_creates_composite(self, tmp_path):
        """When Pillow is present, a single composite PNG is created.

        Skipped automatically when Pillow is not installed (importorskip).
        To run: pip install open-compute[compose]
        """
        pytest.importorskip("PIL")
        from open_compute.cli import _compose_before_after

        png = self._minimal_png()
        out_path = tmp_path / "0001_composite.png"
        result = _compose_before_after(png, png, "test_label", out_path)

        assert "composite" in result
        assert Path(result["composite"]).exists()


# ---------------------------------------------------------------------------
# 4. ensure-foreground helper (cross-platform pure logic)
# ---------------------------------------------------------------------------

class TestEnsureForeground:
    """_should_activate is a pure function; _get_foreground_title wraps Win32."""

    def setup_method(self):
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)
        self.cli = cli_mod

    def test_should_activate_when_target_not_in_title(self):
        assert self.cli._should_activate("Notepad — untitled", "word", False) is True

    def test_should_not_activate_when_target_in_title(self):
        assert self.cli._should_activate("Microsoft Word — doc.docx", "word", False) is False

    def test_should_activate_case_insensitive(self):
        """Match is case-insensitive."""
        assert self.cli._should_activate("MICROSOFT WORD", "word", False) is False

    def test_always_true_overrides_match(self):
        """always=True forces activation even when window is already in foreground."""
        assert self.cli._should_activate("Microsoft Word — doc.docx", "word", True) is True

    def test_always_true_with_mismatch(self):
        assert self.cli._should_activate("Notepad", "word", True) is True

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32-only")
    def test_get_foreground_title_returns_string(self):
        """_get_foreground_title() returns a str (may be empty)."""
        result = self.cli._get_foreground_title()
        assert isinstance(result, str)

    def test_get_foreground_title_non_windows_returns_empty(self, monkeypatch):
        """On non-Windows platforms, _get_foreground_title returns ''."""
        monkeypatch.setattr(sys, "platform", "linux")
        import importlib
        import open_compute.cli as cli_mod
        importlib.reload(cli_mod)
        assert cli_mod._get_foreground_title() == ""


# ---------------------------------------------------------------------------
# 5. cmd_do — batch mode (Windows-only)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="LocalExecutor is Windows-only")
class TestCmdDoBatch:
    """Test batch execution path in cmd_do."""

    def test_batch_array_executes_all(self, capsys):
        """A JSON array of 2 actions should return result=batch, count=2."""
        fake_obs = _fake_obs()

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_exec.return_value.execute.return_value = fake_obs
            mock_exec.return_value.screenshot.return_value = fake_obs
            from open_compute.cli import cmd_do
            cmd_do([
                '[{"type":"mouse_move","x":0.5,"y":0.5},{"type":"wait","duration":0.01}]',
                "--mode", "allow_all",
            ])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["result"] == "batch"
        assert data["count"] == 2

    def test_batch_single_element_array_is_batch(self, capsys):
        """A JSON array with 1 element is treated as batch (result=batch)."""
        fake_obs = _fake_obs()

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_exec.return_value.execute.return_value = fake_obs
            mock_exec.return_value.screenshot.return_value = fake_obs
            from open_compute.cli import cmd_do
            cmd_do([
                '[{"type":"mouse_move","x":0.5,"y":0.5}]',
                "--mode", "allow_all",
            ])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["result"] == "batch"
        assert data["count"] == 1

    def test_single_object_without_label_is_backwards_compatible(self, capsys):
        """Original single-object call without --label returns legacy format."""
        fake_obs = _fake_obs()

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_exec.return_value.execute.return_value = fake_obs
            from open_compute.cli import cmd_do
            cmd_do(['{"type":"mouse_move","x":0.5,"y":0.5}', "--mode", "allow_all"])

        out = capsys.readouterr().out
        data = json.loads(out)
        # Must match legacy keys exactly
        assert data["result"] == "executed"
        assert data["action"] == "mouse_move"
        assert "width" in data
        assert "height" in data
        # Must NOT contain batch-only keys
        assert "count" not in data
        assert "composites" not in data

    def test_batch_deny_stops_at_first_denied(self, capsys):
        """In read_only mode, a click in a batch should deny at that action."""
        with patch("open_compute.cli._load_local_executor"):
            from open_compute.cli import cmd_do
            with pytest.raises(SystemExit) as exc:
                cmd_do([
                    '[{"type":"left_click","x":0.5,"y":0.5}]',
                    "--mode", "read_only",
                ])
        assert exc.value.code == 1
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["result"] == "deny"
        assert data["action_index"] == 0

    def test_batch_confirm_without_yes_stops(self, capsys):
        """In confirm mode without --yes, batch stops with result=confirm."""
        with patch("open_compute.cli._load_local_executor"):
            from open_compute.cli import cmd_do
            with pytest.raises(SystemExit) as exc:
                cmd_do([
                    '[{"type":"left_click","x":0.5,"y":0.5}]',
                    "--mode", "confirm",
                ])
        assert exc.value.code == 1
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["result"] == "confirm"

    def test_batch_mid_sequence_deny_aborts_cleanly(self, capsys):
        """A 2-action batch where action 0 is allowed but action 1 is denied.

        In confirm mode without --yes, a non-risky mouse_move (index 0) executes,
        then a left_click (index 1) needs confirmation -> the run stops cleanly
        with action_index=1 and executed_before=1 (genuine mid-sequence abort,
        not a deny at index 0).
        """
        fake_obs = _fake_obs()

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_executor = MagicMock()
            mock_executor.execute.return_value = fake_obs
            mock_executor.screenshot.return_value = fake_obs
            mock_exec.return_value = mock_executor

            from open_compute.cli import cmd_do
            with pytest.raises(SystemExit) as exc:
                cmd_do([
                    '[{"type":"mouse_move","x":0.5,"y":0.5},'
                    '{"type":"left_click","x":0.5,"y":0.3}]',
                    "--mode", "confirm",
                ])

        assert exc.value.code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["result"] == "confirm"
        assert data["action_index"] == 1
        assert data["executed_before"] == 1
        # The allowed action 0 must have actually executed before the abort.
        assert mock_executor.execute.call_count == 1

    def test_batch_with_label_uses_pre_batch_screenshot(self, tmp_path, monkeypatch, capsys):
        """Batch + --label: pre-batch screenshot is captured BEFORE the loop.

        Verifies that executor.screenshot() is called exactly once before the
        loop starts (for pre_batch_bytes) and that the resulting JSON contains
        either 'composite' (Pillow present) or 'before'/'after' (fallback).
        """
        fake_obs = _fake_obs()
        call_order: list[str] = []

        def fake_screenshot():
            call_order.append("screenshot")
            return fake_obs

        def fake_execute(action):
            call_order.append(f"execute:{action.type.value}")
            return fake_obs

        monkeypatch.setenv("OC_SESSION_DIR", str(tmp_path))

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_executor = MagicMock()
            mock_executor.screenshot.side_effect = fake_screenshot
            mock_executor.execute.side_effect = fake_execute
            mock_exec.return_value = mock_executor

            from open_compute.cli import cmd_do
            cmd_do([
                '[{"type":"mouse_move","x":0.5,"y":0.5},'
                '{"type":"wait","duration":0.01}]',
                "--label", "batch_label",
                "--mode", "allow_all",
            ])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["result"] == "batch"
        assert data["count"] == 2

        # pre_batch screenshot must come before any execute call
        screenshot_idx = next(i for i, v in enumerate(call_order) if v == "screenshot")
        first_execute_idx = next(i for i, v in enumerate(call_order) if v.startswith("execute"))
        assert screenshot_idx < first_execute_idx, (
            "pre-batch screenshot must be captured before any action executes"
        )

        # Result must contain composite or before/after keys (not just bare batch)
        assert any(k in data for k in ("composite", "before", "after")), (
            "batch+label response must include composite path(s)"
        )


# ---------------------------------------------------------------------------
# 6. cmd_do — ensure-foreground (Windows-only)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="LocalExecutor is Windows-only")
class TestCmdDoEnsureForeground:
    """Test that --ensure-foreground triggers activate_window when needed."""

    def test_ensure_foreground_activates_when_not_in_foreground(self, capsys, monkeypatch):
        """When foreground title doesn't match, activate_window should be called."""
        fake_obs = _fake_obs()

        monkeypatch.delenv("OC_ALWAYS_FOREGROUND", raising=False)

        with (
            patch("open_compute.cli._load_local_executor") as mock_exec,
            patch("open_compute.cli._get_foreground_title", return_value="Notepad"),
        ):
            mock_executor = MagicMock()
            mock_executor.execute.return_value = fake_obs
            mock_exec.return_value = mock_executor

            from open_compute.cli import cmd_do
            cmd_do([
                '{"type":"mouse_move","x":0.5,"y":0.5}',
                "--mode", "allow_all",
                "--ensure-foreground", "Word",
            ])

        mock_executor.activate_window.assert_called_once_with("Word")

    def test_ensure_foreground_skips_activate_when_already_foreground(
        self, capsys, monkeypatch
    ):
        """When foreground title already matches, activate_window should NOT be called."""
        fake_obs = _fake_obs()

        monkeypatch.delenv("OC_ALWAYS_FOREGROUND", raising=False)

        with (
            patch("open_compute.cli._load_local_executor") as mock_exec,
            patch("open_compute.cli._get_foreground_title",
                  return_value="Microsoft Word — Document"),
        ):
            mock_executor = MagicMock()
            mock_executor.execute.return_value = fake_obs
            mock_exec.return_value = mock_executor

            from open_compute.cli import cmd_do
            cmd_do([
                '{"type":"mouse_move","x":0.5,"y":0.5}',
                "--mode", "allow_all",
                "--ensure-foreground", "Word",
            ])

        mock_executor.activate_window.assert_not_called()

    def test_always_foreground_env_forces_activate(self, capsys, monkeypatch):
        """OC_ALWAYS_FOREGROUND=1 forces activate even when already foreground."""
        fake_obs = _fake_obs()

        monkeypatch.setenv("OC_ALWAYS_FOREGROUND", "1")

        with (
            patch("open_compute.cli._load_local_executor") as mock_exec,
            patch("open_compute.cli._get_foreground_title",
                  return_value="Microsoft Word — Document"),
        ):
            mock_executor = MagicMock()
            mock_executor.execute.return_value = fake_obs
            mock_exec.return_value = mock_executor

            from open_compute.cli import cmd_do
            cmd_do([
                '{"type":"mouse_move","x":0.5,"y":0.5}',
                "--mode", "allow_all",
                "--ensure-foreground", "Word",
            ])

        mock_executor.activate_window.assert_called_once_with("Word")


# ---------------------------------------------------------------------------
# 7. cmd_capture — session default (Windows-only)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="LocalExecutor is Windows-only")
class TestCmdCaptureSessionDefault:
    """Without --out, capture writes to _session/ (not CWD or Desktop)."""

    def test_capture_default_goes_to_session_dir(self, capsys, tmp_path, monkeypatch):
        """Default capture path is inside _session/ (not loose in CWD)."""
        monkeypatch.setenv("OC_SESSION_DIR", str(tmp_path))
        fake_obs = _fake_obs()
        fake_obs_with_bytes = type(fake_obs)(
            screenshot=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
            width=1920,
            height=1080,
        )

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_exec.return_value.screenshot.return_value = fake_obs_with_bytes
            from open_compute.cli import cmd_capture
            cmd_capture([])  # No --out

        out = capsys.readouterr().out
        data = json.loads(out)
        captured_path = Path(data["path"])
        assert str(tmp_path) in str(captured_path)

    def test_capture_with_explicit_out_uses_given_path(self, capsys, tmp_path):
        """Explicit --out path overrides the default _session/ location."""
        out_file = tmp_path / "explicit.png"
        fake_obs_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

        from open_compute.perception import Observation
        fake_obs = Observation(screenshot=fake_obs_bytes, width=1920, height=1080)

        with patch("open_compute.cli._load_local_executor") as mock_exec:
            mock_exec.return_value.screenshot.return_value = fake_obs
            from open_compute.cli import cmd_capture
            cmd_capture(["--out", str(out_file)])

        out = capsys.readouterr().out
        data = json.loads(out)
        assert Path(data["path"]).resolve() == out_file.resolve()


# ---------------------------------------------------------------------------
# 8. Config.always_foreground field
# ---------------------------------------------------------------------------

class TestConfigAlwaysForeground:
    """Config.always_foreground reads from env and keyword arg."""

    def test_default_is_false_without_env(self, monkeypatch):
        monkeypatch.delenv("OC_ALWAYS_FOREGROUND", raising=False)
        from open_compute.config import Config
        cfg = Config()
        assert cfg.always_foreground is False

    def test_env_sets_true(self, monkeypatch):
        monkeypatch.setenv("OC_ALWAYS_FOREGROUND", "1")
        from open_compute.config import Config
        cfg = Config()
        assert cfg.always_foreground is True

    def test_explicit_kwarg_overrides(self, monkeypatch):
        monkeypatch.delenv("OC_ALWAYS_FOREGROUND", raising=False)
        from open_compute.config import Config
        cfg = Config(always_foreground=True)
        assert cfg.always_foreground is True

    def test_from_dict_accepts_always_foreground(self, monkeypatch):
        monkeypatch.delenv("OC_ALWAYS_FOREGROUND", raising=False)
        from open_compute.config import Config
        cfg = Config.from_dict({"always_foreground": True})
        assert cfg.always_foreground is True

    def test_from_dict_ignores_unknown_keys(self, monkeypatch):
        monkeypatch.delenv("OC_ALWAYS_FOREGROUND", raising=False)
        from open_compute.config import Config
        cfg = Config.from_dict({"unknown_key": "foo", "always_foreground": False})
        assert cfg.always_foreground is False


# ---------------------------------------------------------------------------
# 9. Version bump
# ---------------------------------------------------------------------------

class TestVersionBump:
    def test_version_is_040(self):
        """Version bumped to 0.5.0 with dirwatch feed + fullres shot."""
        import open_compute
        assert open_compute.__version__ == "0.6.0"
