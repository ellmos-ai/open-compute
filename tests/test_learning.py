"""Tests for open_compute.learning — v0.6.

Coverage
--------
1. BetaPrior — prior values; update success/fail; expected_rate; to/from_dict.
2. ActionOutcome — dataclass construction; to_dict/from_dict round-trip.
3. Lesson — dataclass construction; to_dict/from_dict round-trip.
4. LearningManager.log_outcome — weight update; JSONL append; round-trip reload.
5. LearningManager.success_rate — prior (no data) = 0.5; after outcomes.
6. LearningManager.best_feed — picks highest-rated feed.
7. LearningManager.save_profile / load_profile — round-trip via JSON.
8. LearningManager.list_profiles — returns all stored profiles.
9. LearningManager.add_lesson — appends in-memory + JSONL; survives reload.
10. LearningManager.get_lessons — all; filtered by tag.
11. LearningManager.apply_profile_to_manager — calls set_dosage; returns bool.
12. LearningManager._load_weights — warmstart from persisted JSON.
13. LearningManager._load_lessons — max_lessons cap applied on load.
14. Import-without-extras: import open_compute.learning works standalone.

All state is directed to tmp_path (no OC_STATE_DIR leak).
No live desktop, no daemon, no real files in repo.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# 1. BetaPrior
# ---------------------------------------------------------------------------

class TestBetaPrior:
    def test_default_prior(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior()
        assert p.alpha == 1.0
        assert p.beta == 1.0

    def test_initial_expected_rate_is_half(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior()
        assert abs(p.expected_rate - 0.5) < 1e-9

    def test_success_increments_alpha(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior()
        p.update(True)
        assert p.alpha == 2.0
        assert p.beta == 1.0

    def test_failure_increments_beta(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior()
        p.update(False)
        assert p.alpha == 1.0
        assert p.beta == 2.0

    def test_expected_rate_increases_with_successes(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior()
        for _ in range(9):
            p.update(True)
        # alpha=10, beta=1 → rate = 10/11 ≈ 0.909
        assert p.expected_rate > 0.5
        assert abs(p.expected_rate - 10 / 11) < 1e-9

    def test_expected_rate_decreases_with_failures(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior()
        for _ in range(9):
            p.update(False)
        # alpha=1, beta=10 → rate = 1/11 ≈ 0.0909
        assert p.expected_rate < 0.5
        assert abs(p.expected_rate - 1 / 11) < 1e-9

    def test_expected_rate_clamped_on_zero_total(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior(alpha=0.0, beta=0.0)
        assert p.expected_rate == 0.5  # degenerate case

    def test_to_dict_round_trip(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior(alpha=3.0, beta=2.0)
        d = p.to_dict()
        p2 = BetaPrior.from_dict(d)
        assert p2.alpha == 3.0
        assert p2.beta == 2.0

    def test_from_dict_defaults(self):
        from open_compute.learning import BetaPrior
        p = BetaPrior.from_dict({})
        assert p.alpha == 1.0
        assert p.beta == 1.0


# ---------------------------------------------------------------------------
# 2. ActionOutcome
# ---------------------------------------------------------------------------

class TestActionOutcome:
    def test_construction_defaults(self):
        from open_compute.learning import ActionOutcome
        o = ActionOutcome(feed_used="uia_windows", app="word", action_type="invoke", success=True)
        assert o.feed_used == "uia_windows"
        assert o.app == "word"
        assert o.action_type == "invoke"
        assert o.success is True
        assert o.note == ""
        assert o.ts > 0

    def test_to_dict_has_all_keys(self):
        from open_compute.learning import ActionOutcome
        o = ActionOutcome(feed_used="screenshot", app="explorer", action_type="left_click", success=False, note="missed")
        d = o.to_dict()
        assert d["feed_used"] == "screenshot"
        assert d["app"] == "explorer"
        assert d["action_type"] == "left_click"
        assert d["success"] is False
        assert d["note"] == "missed"
        assert "ts" in d

    def test_from_dict_round_trip(self):
        from open_compute.learning import ActionOutcome
        orig = ActionOutcome(feed_used="dirwatch", app="vscode", action_type="wait", success=True, note="ok")
        d = orig.to_dict()
        restored = ActionOutcome.from_dict(d)
        assert restored.feed_used == orig.feed_used
        assert restored.app == orig.app
        assert restored.action_type == orig.action_type
        assert restored.success == orig.success
        assert restored.note == orig.note

    def test_from_dict_defaults_on_missing_keys(self):
        from open_compute.learning import ActionOutcome
        o = ActionOutcome.from_dict({})
        assert o.feed_used == ""
        assert o.success is False


# ---------------------------------------------------------------------------
# 3. Lesson
# ---------------------------------------------------------------------------

class TestLesson:
    def test_construction_defaults(self):
        from open_compute.learning import Lesson
        l = Lesson(lesson="Test lesson")
        assert l.lesson == "Test lesson"
        assert l.tags == []
        assert l.ts > 0

    def test_to_dict_round_trip(self):
        from open_compute.learning import Lesson
        l = Lesson(lesson="App Z: UIA unreliable", tags=["word", "invoke"])
        d = l.to_dict()
        l2 = Lesson.from_dict(d)
        assert l2.lesson == "App Z: UIA unreliable"
        assert l2.tags == ["word", "invoke"]

    def test_from_dict_defaults(self):
        from open_compute.learning import Lesson
        l = Lesson.from_dict({})
        assert l.lesson == ""
        assert l.tags == []


# ---------------------------------------------------------------------------
# 4. LearningManager.log_outcome
# ---------------------------------------------------------------------------

class TestLogOutcome:
    def test_log_outcome_returns_action_outcome(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        result = mgr.log_outcome("uia_windows", "word", "invoke", success=True)
        from open_compute.learning import ActionOutcome
        assert isinstance(result, ActionOutcome)
        assert result.success is True

    def test_log_outcome_appends_to_jsonl(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.log_outcome("uia_windows", "word", "invoke", success=True)
        mgr.log_outcome("screenshot", "word", "left_click", success=False)
        lines = (tmp_path / "outcomes.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_log_outcome_updates_weight(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.log_outcome("uia_windows", "word", "invoke", success=True)
        rate = mgr.success_rate("uia_windows", "word", "invoke")
        # alpha=2, beta=1 → 2/3 ≈ 0.667
        assert abs(rate - 2 / 3) < 1e-9

    def test_log_outcome_persists_weight_on_reload(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.log_outcome("uia_windows", "word", "invoke", success=True)
        mgr.log_outcome("uia_windows", "word", "invoke", success=True)
        # Reload
        mgr2 = LearningManager(state_dir=tmp_path)
        # alpha=3, beta=1 → 3/4 = 0.75
        assert abs(mgr2.success_rate("uia_windows", "word", "invoke") - 0.75) < 1e-9

    def test_log_outcome_trims_jsonl_history(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path, max_outcomes_history=3)
        for idx in range(5):
            mgr.log_outcome("uia_windows", "word", f"invoke_{idx}", success=True)

        lines = (tmp_path / "outcomes.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        records = [json.loads(line) for line in lines]
        assert [record["action_type"] for record in records] == ["invoke_2", "invoke_3", "invoke_4"]


# ---------------------------------------------------------------------------
# 5. LearningManager.success_rate
# ---------------------------------------------------------------------------

class TestSuccessRate:
    def test_prior_is_half_when_no_data(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        assert abs(mgr.success_rate("screenshot", "anyapp", "left_click") - 0.5) < 1e-9

    def test_all_successes_drives_rate_up(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        for _ in range(10):
            mgr.log_outcome("uia_windows", "word", "invoke", success=True)
        rate = mgr.success_rate("uia_windows", "word", "invoke")
        assert rate > 0.9

    def test_all_failures_drives_rate_down(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        for _ in range(10):
            mgr.log_outcome("uia_windows", "word", "invoke", success=False)
        rate = mgr.success_rate("uia_windows", "word", "invoke")
        assert rate < 0.2

    def test_mixed_outcomes_yields_proportion(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        # 3 successes, 1 failure → alpha=4, beta=2 → 4/6 = 0.667
        for _ in range(3):
            mgr.log_outcome("uia_windows", "word", "invoke", success=True)
        mgr.log_outcome("uia_windows", "word", "invoke", success=False)
        rate = mgr.success_rate("uia_windows", "word", "invoke")
        assert abs(rate - 4 / 6) < 1e-9


# ---------------------------------------------------------------------------
# 6. LearningManager.best_feed
# ---------------------------------------------------------------------------

class TestBestFeed:
    def test_best_feed_returns_highest_rate(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        # Give uia_windows 9 successes, screenshot 1
        for _ in range(9):
            mgr.log_outcome("uia_windows", "word", "invoke", success=True)
        mgr.log_outcome("screenshot", "word", "invoke", success=True)
        best = mgr.best_feed("word", "invoke", ["uia_windows", "screenshot"])
        assert best == "uia_windows"

    def test_best_feed_returns_none_for_empty_candidates(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        assert mgr.best_feed("word", "invoke", []) is None

    def test_best_feed_single_candidate_returned(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        best = mgr.best_feed("word", "invoke", ["uia_windows"])
        assert best == "uia_windows"

    def test_best_feed_uses_prior_when_no_data(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        # All priors are equal (0.5) → result is deterministic (first max wins)
        best = mgr.best_feed("word", "invoke", ["uia_windows", "screenshot"])
        assert best in ("uia_windows", "screenshot")


# ---------------------------------------------------------------------------
# 7. save_profile / load_profile
# ---------------------------------------------------------------------------

class TestProfiles:
    def test_save_and_load_profile_round_trip(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.save_profile("word", "text_editing", {"uia_windows": "delta", "screenshot": "notify"})
        profile = mgr.load_profile("word", "text_editing")
        assert profile is not None
        assert profile["uia_windows"] == "delta"
        assert profile["screenshot"] == "notify"

    def test_load_profile_none_when_missing(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        assert mgr.load_profile("nonexistent", "nope") is None

    def test_profile_persists_across_reload(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.save_profile("explorer", "file_copy", {"dirwatch": "full"})
        mgr2 = LearningManager(state_dir=tmp_path)
        profile = mgr2.load_profile("explorer", "file_copy")
        assert profile is not None
        assert profile["dirwatch"] == "full"

    def test_overwrite_profile(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.save_profile("word", "editing", {"uia_windows": "delta"})
        mgr.save_profile("word", "editing", {"uia_windows": "full"})
        profile = mgr.load_profile("word", "editing")
        assert profile["uia_windows"] == "full"


# ---------------------------------------------------------------------------
# 8. list_profiles
# ---------------------------------------------------------------------------

class TestListProfiles:
    def test_list_profiles_returns_all(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.save_profile("word", "editing", {"uia_windows": "delta"})
        mgr.save_profile("explorer", "copy", {"dirwatch": "full"})
        profiles = mgr.list_profiles()
        assert len(profiles) == 2
        programs = {p["program"] for p in profiles}
        assert "word" in programs
        assert "explorer" in programs

    def test_list_profiles_empty_when_none(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        assert mgr.list_profiles() == []


# ---------------------------------------------------------------------------
# 9. add_lesson / JSONL append
# ---------------------------------------------------------------------------

class TestAddLesson:
    def test_add_lesson_returns_lesson(self, tmp_path):
        from open_compute.learning import LearningManager, Lesson
        mgr = LearningManager(state_dir=tmp_path)
        l = mgr.add_lesson("App Z: UIA unreliable", tags=["word"])
        assert isinstance(l, Lesson)
        assert l.lesson == "App Z: UIA unreliable"

    def test_add_lesson_appends_to_memory(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.add_lesson("Lesson 1")
        mgr.add_lesson("Lesson 2")
        assert len(mgr.lessons) == 2
        assert mgr.lessons[0].lesson == "Lesson 1"
        assert mgr.lessons[1].lesson == "Lesson 2"

    def test_add_lesson_writes_jsonl(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.add_lesson("L1")
        mgr.add_lesson("L2")
        lines = (tmp_path / "lessons.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_lessons_survive_reload(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.add_lesson("Persistent lesson", tags=["all"])
        mgr2 = LearningManager(state_dir=tmp_path)
        assert len(mgr2.lessons) == 1
        assert mgr2.lessons[0].lesson == "Persistent lesson"

    def test_add_lesson_trims_jsonl_history(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path, max_lessons_history=2)
        mgr.add_lesson("L1")
        mgr.add_lesson("L2")
        mgr.add_lesson("L3")

        lines = (tmp_path / "lessons.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        records = [json.loads(line) for line in lines]
        assert [record["lesson"] for record in records] == ["L2", "L3"]


# ---------------------------------------------------------------------------
# 10. get_lessons
# ---------------------------------------------------------------------------

class TestGetLessons:
    def test_get_all_lessons(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.add_lesson("L1", tags=["word"])
        mgr.add_lesson("L2", tags=["explorer"])
        assert len(mgr.get_lessons()) == 2

    def test_get_lessons_filtered_by_tag(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.add_lesson("L1", tags=["word"])
        mgr.add_lesson("L2", tags=["explorer"])
        word_lessons = mgr.get_lessons(tag="word")
        assert len(word_lessons) == 1
        assert word_lessons[0].lesson == "L1"

    def test_get_lessons_no_match_returns_empty(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.add_lesson("L1", tags=["word"])
        assert mgr.get_lessons(tag="nonexistent") == []


# ---------------------------------------------------------------------------
# 11. apply_profile_to_manager
# ---------------------------------------------------------------------------

class TestApplyProfileToManager:
    def test_apply_profile_calls_set_dosage(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.save_profile("word", "editing", {"uia_windows": "delta", "screenshot": "notify"})
        mock_manager = MagicMock()
        result = mgr.apply_profile_to_manager(mock_manager, "word", "editing")
        assert result is True
        mock_manager.set_dosage.assert_any_call("uia_windows", "delta")
        mock_manager.set_dosage.assert_any_call("screenshot", "notify")

    def test_apply_profile_returns_false_when_no_profile(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mock_manager = MagicMock()
        result = mgr.apply_profile_to_manager(mock_manager, "nonexistent", "nope")
        assert result is False
        mock_manager.set_dosage.assert_not_called()

    def test_apply_profile_with_real_feed_manager(self, tmp_path):
        """Integration: apply profile changes dosage in a real FeedManager."""
        from open_compute.learning import LearningManager
        from open_compute.feed_manager import FeedManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.save_profile("word", "editing", {"screenshot": "full"})
        fm = FeedManager(feeds=[])
        result = mgr.apply_profile_to_manager(fm, "word", "editing")
        assert result is True
        assert fm.get_dosage("screenshot") == "full"


# ---------------------------------------------------------------------------
# 12. LearningManager._load_weights (warmstart)
# ---------------------------------------------------------------------------

class TestLoadWeights:
    def test_weights_loaded_from_json(self, tmp_path):
        from open_compute.learning import LearningManager, _weight_key
        # Write a weights.json manually
        key = _weight_key("word", "uia_windows", "invoke")
        data = {key: {"alpha": 5.0, "beta": 2.0}}
        (tmp_path / "weights.json").write_text(json.dumps(data), encoding="utf-8")
        mgr = LearningManager(state_dir=tmp_path)
        rate = mgr.success_rate("uia_windows", "word", "invoke")
        # alpha=5, beta=2 → 5/7 ≈ 0.714
        assert abs(rate - 5 / 7) < 1e-9

    def test_malformed_weights_json_ignored(self, tmp_path):
        from open_compute.learning import LearningManager
        (tmp_path / "weights.json").write_text("not json", encoding="utf-8")
        mgr = LearningManager(state_dir=tmp_path)  # should not raise
        assert abs(mgr.success_rate("uia_windows", "word", "invoke") - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# 13. _load_lessons — max_lessons cap
# ---------------------------------------------------------------------------

class TestLoadLessonsCap:
    def test_max_lessons_respected_on_load(self, tmp_path):
        from open_compute.learning import LearningManager, Lesson
        import json as _json
        path = tmp_path / "lessons.jsonl"
        lines = []
        for i in range(20):
            l = Lesson(lesson=f"Lesson {i}", tags=[])
            lines.append(_json.dumps(l.to_dict()))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        mgr = LearningManager(state_dir=tmp_path, max_lessons=5)
        assert len(mgr.lessons) == 5
        # Should be the LAST 5
        assert mgr.lessons[-1].lesson == "Lesson 19"

    def test_lessons_jsonl_survives_two_appends(self, tmp_path):
        from open_compute.learning import LearningManager
        mgr = LearningManager(state_dir=tmp_path)
        mgr.add_lesson("First")
        mgr.add_lesson("Second")
        mgr2 = LearningManager(state_dir=tmp_path)
        texts = [l.lesson for l in mgr2.lessons]
        assert "First" in texts
        assert "Second" in texts


# ---------------------------------------------------------------------------
# 14. Import-without-extras
# ---------------------------------------------------------------------------

class TestImportWithoutExtras:
    def test_import_learning_no_extras(self):
        import importlib
        import open_compute.learning as lm_mod
        importlib.reload(lm_mod)
        assert hasattr(lm_mod, "LearningManager")
        assert hasattr(lm_mod, "BetaPrior")
        assert hasattr(lm_mod, "ActionOutcome")
        assert hasattr(lm_mod, "Lesson")

    def test_import_open_compute_still_works_with_learning(self):
        import open_compute
        assert open_compute.__version__ is not None
