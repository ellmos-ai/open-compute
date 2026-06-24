# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- `LocalExecutor.screenshot()` now falls back from mss/GDI capture to a
  Windows.Graphics.Capture backend when mss raises during monitor capture.
  This targets DirectX / hardware-composited surfaces such as Roblox Studio,
  Blender, and games.
- For the default virtual-desktop capture (`monitor_index=0`), the WGC primary
  monitor frame is placed on a virtual-desktop-sized canvas so normalized input
  coordinates remain compatible with `oc do` and agent-loop actions.
- New optional extra `open-compute[wgc]` installs `windows-capture` and Pillow
  for the WGC fallback; `[all]` includes it. `windows-capture` pulls numpy and
  OpenCV transitively and is guarded by a Windows platform marker.

### Tests

- Added mocked LocalExecutor coverage for the WGC success path and for
  re-raising the original mss error when WGC is unavailable.

---

## [0.6.0] - 2026-06-20

### Added — Feature #1: Feed-Manager + dosierte Push-Auto-Injektion

- **`open_compute/feed_manager.py`** — new module:
  - `FeedManager`: collects available feeds via DI; applies change-detection
    per cycle; dispatches to an `InjectorSink`.
  - **State-Feeds** (screenshot, uia_windows, uia_tree, ocr, caption): SHA-256
    hash-based change-detection; push only on change; in-place overwrite.
  - **Event-Feeds** (dirwatch, action_chain): rolling-window deque; push on new events.
  - **Dosage modes** per feed (`full` | `delta` | `notify` | `off`); runtime-adjustable
    via `set_dosage()` so the LLM can self-tune weight/frequency/amount.
    Defaults: screenshot→notify, uia_windows→delta, dirwatch→full, others→full.
  - **`on_demand_full(feed_name)`**: bypass dosage for a pull of the full observation
    (LLM can request when `notify` was pushed).
  - **`InjectorSink` protocol** (runtime_checkable Protocol).
  - **`LocalFileInjector`** (working default sink): writes JSON snapshots/deltas to
    `_state/inject_queue/`; state-feeds overwrite in-place; event-feeds accumulate
    in a rolling list (capped at `max_events`).
  - **`BachInjectorAdapter`** (non-functional stub — only the file fallback is real):
    renders an `[OC-FEEDS]` block (equivalent to BACH's `[BACH-REMINDERS]` format)
    and writes it to a local file. The "BACH available" branch does NOT work as
    written: BACH's `ReminderInjector.__init__` requires a `base_path` arg (not
    passed → `TypeError`), and `inject(prompt, context) -> str` is a pure string
    transform (prepends reminders to `prompt`, returns the result) — not a push/store
    sink, never reads an `oc_block` key, return value discarded. A real BACH transport
    is deferred follow-up. NOT activated by default — BACH is tightly coupled to its
    own SQLite/JSON DB and cannot be cleanly imported without violating the
    zero-deps-core constraint. Evidence: `BACH/system/hub/reminder_injector.py`.
  - **`_diff_uia_elements(prev, curr)`**: pure helper for UIA element delta
    (added/removed by `(name, role)` key); fully unit-testable.
  - **`_hash_observation(obs)`**: SHA-256 digest of observation payload (16 hex chars).
  - **`status()`**: returns feeds, dosages, per-feed push counts, sink status.
- **`oc push --status`** and **`oc push --once [--window SUBSTR]`** CLI sub-commands
  (`cli.py`): read-only status check and one-shot inject cycle (no daemon).
- **`_state/` added to `.gitignore`**.

### Added — Feature #4: Lernschicht / Profile / LESSONS-LEARNED

- **`open_compute/learning.py`** — new module:
  - **`BetaPrior`**: Beta-distribution posterior for `(app, feed, action_type)`;
    closed-form, deterministic update (no random sampling); `expected_rate` property;
    `to_dict`/`from_dict` round-trip.
  - **`ActionOutcome`**: dataclass for one logged result `(feed_used, app,
    action_type) → success`; `to_dict`/`from_dict`.
  - **`Lesson`**: dataclass for cross-session lesson text + tags + timestamp;
    `to_dict`/`from_dict`.
  - **`LearningManager`**:
    - `log_outcome(...)`: appends to `_state/outcomes.jsonl` + updates
      in-memory Bandit/Bayes weight; persists weights to `_state/weights.json`.
    - `success_rate(feed, app, action_type)`: Beta-posterior mean (prior 0.5).
    - `best_feed(app, action_type, candidates)`: returns highest-rated feed name.
    - `save_profile(program, usecase, dosage_map)` / `load_profile(...)`:
      JSON-persisted use-case profiles in `_state/profiles.json`; warmstart seam.
    - `list_profiles()`: all stored profiles.
    - `add_lesson(text, tags)` / `get_lessons(tag=None)`: JSONL cross-session
      lessons in `_state/lessons.jsonl`; survives session restart.
    - `apply_profile_to_manager(manager, program, usecase)`: warmstart integration
      — applies stored profile dosages to a `FeedManager` instance at session start.
    - Warmstart on construction: loads weights, profiles, and last N lessons.

### Added — Tests (105 new)

- **`tests/test_feed_manager.py`** (71 tests):
  InjectorSink protocol, LocalFileInjector (push/accumulate/cap), BachInjectorAdapter
  (BACH mocked + absent), `_hash_observation`, `_diff_uia_elements`, `_default_dosage`,
  FeedManager construction/DI, dosage API (set/get/ValueError), cycle (off-skip,
  unchanged-skip, state-feed full/delta/notify, event-feed, error capture, multi-feed),
  `on_demand_full`, status, import-without-extras.
- **`tests/test_learning.py`** (46 tests):
  BetaPrior (prior, update success/fail, expected_rate, to/from_dict),
  ActionOutcome/Lesson round-trip, `log_outcome` (weight update, JSONL append, reload),
  `success_rate` (prior 0.5, monotonic with outcomes), `best_feed`,
  save/load/overwrite/list profiles, `add_lesson` + JSONL survival, `get_lessons` filtered,
  `apply_profile_to_manager` (mock + real FeedManager), `_load_weights` warmstart,
  lessons cap, import-without-extras.

### Changed

- `open_compute/__version__` bumped to `0.6.0`.
- `cli.py` `main()`: added `push` sub-command dispatch.

### Real vs Stub

| Component | Status |
|---|---|
| FeedManager + dosage + change-detection | Real, unit-tested |
| LocalFileInjector | Real, unit-tested |
| BachInjectorAdapter | Documented stub (BACH not cleanly importable); fallback to file is real |
| `_diff_uia_elements` | Real, unit-tested |
| `_hash_observation` | Real, unit-tested |
| LearningManager + BetaPrior | Real, unit-tested |
| Use-Case-Profiles (JSON) | Real, unit-tested |
| LESSONS-LEARNED (JSONL) | Real, unit-tested |
| `apply_profile_to_manager` warmstart seam | Real, unit-tested |
| Daemon / always-on push loop | NOT implemented (no permanent daemon in tests) |
| Cross-feed hash gating (OCR depends on screenshot hash) | NOT implemented (no OCR feed yet) |

---

## [0.5.0] - 2026-06-20

### Added — Feature #2: Directory-Watch Feed

- **`open_compute/feeds/dirwatch.py`** — `DirwatchFeed` (new `PerceptionFeed`):
  - `name = "dirwatch"`, `available()` always `True` (stdlib polling is always usable).
  - `observe(window=None)` returns `FeedObservation(kind="dirwatch", elements=[...])` with
    accumulated change events (newest first, rolling deque, default max 200).
  - Two backends, selected automatically:
    - **watchdog** (MIT): native OS FS events (`ReadDirectoryChangesW` / `inotify` / `FSEvents`).
      Activated when `watchdog` is importable (`pip install open-compute[watch]`).
    - **stdlib polling**: `os.scandir` + mtime-diff snapshot. Always available; no extra install needed.
  - `start(paths)` / `stop()` lifecycle (watchdog: background observer; polling: driven by `observe()`).
  - `snapshot_diff(paths, baseline)` for one-shot diff (no background observer); used by `--once`.
  - Pure helpers `_scan_snapshot(paths)` and `_diff_snapshots(old, new)` extracted for unit-testing
    without any watchdog or file-system mocking.
  - Move detection: unambiguous delete+create at the same mtime → single `"moved"` event.
- **`open_compute/feeds/registry.py`** — `DirwatchFeed` added to both `available_feeds()` and
  `feed_names()` (cross-platform, no `win32` gate; lazy import).
- **`[watch]` optional extra** (`pyproject.toml`): `pip install open-compute[watch]` installs
  `watchdog>=3.0`. Appended to `[all]`.
- **`oc watch-dir` CLI sub-command** (`cli.py`):
  - `oc watch-dir <path> [<path>...] [--for SECS]` — collect events for N seconds, print JSON array.
  - `oc watch-dir <path> [--once]` — one-time snapshot diff against last known state
    (baseline persisted in `_session/dirwatch_snapshot.json`).
  - Without `--for`/`--once`: runs until Ctrl-C, prints on exit.
  - Clear `exit(2)` error when a path does not exist.

### Added — Feature #3: Full-resolution / Annotated Verification Shot

- **`oc do --fullres`**: saves an additional full-resolution after-shot to `_session/` alongside the
  composite. Path returned as `"fullres"` (without annotation) or `"fullres_annotated"` (with marker)
  in the JSON output.
- **`oc click-name --fullres`**: same — full-res after-shot with click-coordinate marker at
  `target.center_norm`.
- **Annotation**: Pillow (lazy, optional) draws a red circle (radius 12 px) + crosshair at the click
  pixel. If Pillow is absent the full-res PNG is still saved without annotation; `"fullres"` key
  present, `"fullres_annotated"` absent.
- **`oc capture --window SUBSTR`**: capture only the bounding rect of the named window.
  - Resolves HWND via `_find_window_hwnd(substr)`: `EnumWindows` with case-insensitive,
    whitespace-normalized substring match (same convention as `UiaWindowsFeed._get_root`).
  - Reads bounding rect via `GetWindowRect(hwnd)` → mss region dict → `mss.grab(region)`.
  - JSON response includes `"window"`, `"region"`, `"width"`, `"height"`, `"path"`.
  - Clear `exit(2)` error when no matching window is found.
  - New pure helpers: `_find_window_hwnd(substr)`, `_hwnd_to_mss_region(hwnd)`.

### Added — Tests (58 new)

- **`tests/test_dirwatch.py`** (48 tests):
  - `_scan_snapshot` (5), `_diff_snapshots` (8), `DirwatchFeed.available()` (2),
    `observe()` shape (3), polling backend with real temp dir: create/modify/delete → events (5),
    `max_events` respected (1), `snapshot_diff()` first-run + second-run (4),
    registry integration (3), CLI parsing `oc watch-dir` (5),
    import/availability without watchdog (2).
- **`tests/test_fullres_and_capture_window.py`** (10 tests):
  - `_save_fullres_shot`: without Pillow (2), with Pillow (1), without coords (1), path as str (1),
    missing dimensions (1).
  - Win32 helpers mocked: `_find_window_hwnd` found/not-found/non-windows (3),
    `_hwnd_to_mss_region` rect math + zero-size clamp (2).
  - `oc do --fullres` CLI (4), `oc click-name --fullres` CLI (3),
    `oc capture --window` CLI (4).

**All 190 existing tests remain green (248 total pass, 1 skip).**

### Changed

- **Version**: `0.5.0` in `open_compute/__init__.py` and `pyproject.toml`.
- **`cli.py` module docstring** updated to document new commands and flags.
- **`main()` usage string** updated with `oc watch-dir`, `--fullres`, `--window`.
- **Version assertions** in `tests/test_feeds.py`, `tests/test_phase1.py`,
  `tests/test_local_executor.py` updated to `"0.5.0"`.

**Live-Verify for `--window` capture and `--fullres` annotation: OPEN — awaiting user smoke-test.**

---

## [0.4.1] - 2026-06-20

### Fixed — UIA Window Scoping Bug

- **`feeds/uia_windows.py` — `_get_root()` silent fallback removed.**
  When `--window <name>` was specified but no matching top-level window was found,
  the function silently returned the desktop root (UIA `RootElement`), causing all
  subsequent element searches to walk the Taskbar instead of the intended window.
  `oc tree --window "Schnitzeljagd"` returned Taskbar elements; `invoke "Start"`
  hit the Start button, not Word's ribbon tab.

  **Fix:**
  - `_get_root(window)` now raises `RuntimeError` with the requested name when no
    top-level window matches. No silent desktop-root fallback.
  - Window title matching is case-insensitive **and whitespace-normalized** (multiple
    consecutive spaces/tabs collapsed to one, then stripped) so titles like
    `"Schnitzeljagd  -  Kompatibilitätsmodus - Word"` match the query `"Schnitzeljagd"`.
  - Default path (`window=None`): first tries `GetForegroundWindow()` →
    `ControlFromHandle(hwnd)` for an explicit HWND-based resolution; falls back to
    `GetForegroundControl()`, then ultimately `GetRootControl()`.
  - New helper: `_normalize_window_name(s)` — collapses `\s+` to single space.

- **`cli.py`** — `cmd_tree`, `cmd_click_name`, `cmd_invoke` now catch `RuntimeError`
  from `_get_root` and route it through `_die()` (exit code 2, stderr message) instead
  of letting an unhandled exception surface as a traceback.

### Added — Tests

- **`tests/test_uia_window_scoping.py`** — 26 new unit tests (all mocked, no live OS
  calls) covering:
  - `_normalize_window_name` (whitespace collapsing, tabs, strip, empty).
  - `_get_root` named window: substring match, case-insensitivity, double-space title,
    second-child match, no-match → `RuntimeError`, never returns desktop root on miss,
    error message contains requested name.
  - `_get_root` default (foreground): `ControlFromHandle` path, fallback to
    `GetForegroundControl`, ultimate fallback to `GetRootControl`.
  - Subtree scoping: `WalkControl` called on Word control not desktop root; `observe()`
    returns Word's tabs not Taskbar; no-match does not trigger any walk.
  - CLI: all three UIA commands exit 2 with a clear message on window-not-found.

**Live-Verify: OPEN — awaiting user smoke-test confirmation.**

---

## [0.4.0] - 2026-06-20

### Added — Phase 2a: Multi-Feed Abstraction + Windows UIA Feed

- **`open_compute/feeds/base.py`** — Feed abstraction layer:
  - `PerceptionFeed` protocol: `name`, `available()`, `observe(window?)`.
  - `Targeter` protocol: `resolve(query, window?)`, `invoke(query, window?)`.
  - `FeedObservation` dataclass: `kind`, `elements`, `text`, `ts`.
  - `Target` dataclass: `name`, `role`, `rect_px`, `center_norm`, `invokable`, `feed`.
  - All coordinates in `center_norm` are 0..1 relative to the virtual desktop,
    fully compatible with the existing `oc do` coordinate system.
- **`open_compute/feeds/screenshot.py`** — `ScreenshotFeed`: wraps
  `LocalExecutor.screenshot()` as a `PerceptionFeed`. Lazy import of `mss` +
  `LocalExecutor`; `available()` = Windows + mss installed.
- **`open_compute/feeds/uia_windows.py`** — `UiaWindowsFeed` + `UiaTargeter`:
  - Lazy import of `uiautomation` (MIT); importable without it installed.
  - `available()` = `sys.platform == "win32"` and `uiautomation` importable.
  - `observe(window?)`: walks UIA ControlView tree via `WalkControl(maxDepth)`.
    Reads document text via `TextPattern` where available.
  - `resolve(query, window?)`: disambiguates by exact name > prefix > contains;
    optional role filter via `"name:Role"` syntax; prefers visible elements.
    Returns `Target` with `center_norm` = `(xcenter - virt_left) / virt_width`
    (exact inverse of `LocalExecutor._sendinput_coords`).
  - `invoke(query, window?)`: click-free pattern fallback chain:
    InvokePattern → TogglePattern → SelectionItemPattern →
    LegacyIAccessible.DoDefaultAction.
  - Limits: `OC_UIA_MAX_DEPTH` (default 8), `OC_UIA_MAX_ELEM` (default 200).
  - DPI awareness set before every tree walk (Per-Monitor-v2).
- **`open_compute/feeds/registry.py`** — `available_feeds()` / `feed_names()`:
  runtime capability detection; degrades cleanly when extras are absent.
- **`[uia]` optional extra** (`pyproject.toml`):
  `pip install open-compute[uia]` installs `uiautomation>=2.0.18`.
  `[all]` now includes uiautomation.
- **CLI sub-commands** (`cli.py`):
  - `oc tree [--window SUBSTR] [--max N] [--depth N]` — JSON element list.
  - `oc click-name "<query>" [--window SUBSTR] [--mode] [--yes] [--ensure-foreground]`
    — UIA resolve → click at `center_norm` via LocalExecutor + Safety gate.
  - `oc invoke "<query>" [--window SUBSTR] [--mode] [--yes]`
    — click-free UIA invoke via pattern fallback chain + Safety gate.
- **55 new tests** (`tests/test_feeds.py`): protocol conformance (5),
  dataclasses (4), registry with/without UIA (5), ScreenshotFeed (4),
  coordinate math round-trip (6), disambiguator (8), resolve (4), invoke
  fallback chain (5), availability (3), CLI parsing (5), import-without-extras
  (4), version (1). All 109 existing tests remain green (total: 164 pass, 1 skip).

### Changed

- Version bumped to `0.4.0`.
- `open_compute/__init__.py`: `__version__` updated to `0.4.0`.

---

## [0.3.0] - 2026-06-20

### Added — Phase 1 Automation & UX Roadmap

- **`_session/` capture default** (`cli.py`): `oc capture` without `--out`
  now writes to `<module-root>/_session/<seq>_<timestamp>.png` instead of
  a fixed filename in CWD/Desktop. The directory is gitignored. Old files
  rotate automatically (default: keep last 20, configurable via
  `OC_SESSION_KEEP`). The `_session/` directory can be overridden with
  `OC_SESSION_DIR`.  New helpers: `_session_dir()`, `_next_session_path()`,
  `_rotate_session()`.
- **Auto Before|After composite** (`cli.py`): `oc do '<json>' --label NAME`
  takes a screenshot before the action and after, then stitches both into one
  labeled PNG (`_session/<seq>_NAME.png`) using Pillow (optional).  Without
  Pillow the two separate images are saved and both paths returned in the JSON
  output (`"before"` / `"after"` keys).  New helper: `_compose_before_after()`.
- **Batch/macro execution** (`cli.py`): `oc do` now accepts a **JSON array**
  of actions in a single call (e.g. `oc do '[{"type":"mouse_move",...},...]'`).
  Executes them in sequence, applying the SafetyPolicy to each action.  The
  first DENY or CONFIRM stops the batch (includes `action_index` and
  `executed_before` in the JSON response so the caller can resume).  Optional
  `--shots each` flag creates one composite per step.  Single-object input
  remains supported (backwards-compatible).  New helper: `_parse_actions()`.
- **Foreground-window check** (`cli.py`): `oc do` and `oc run` accept
  `--ensure-foreground SUBSTR`.  Before execution the foreground window title
  is queried via Win32 `GetForegroundWindow` / `GetWindowTextW`; if the target
  substring is absent, `activate_window(SUBSTR)` is called first.  Setting
  `OC_ALWAYS_FOREGROUND=1` (or `Config.always_foreground = True`) forces
  activation even when the window is already in the foreground.  New helpers:
  `_get_foreground_title()`, `_should_activate()`.
- **`Config.always_foreground`** (`config.py`): new `bool` field, defaulting
  to `False`; reads `OC_ALWAYS_FOREGROUND` env var at instantiation.
- **`[compose]` optional extra** (`pyproject.toml`): `pip install
  open-compute[compose]` installs `Pillow>=10.0`.  `[all]` now includes
  Pillow.  Pillow is **never** imported at module level (lazy, optional).
- **42 new tests** (`tests/test_phase1.py`): `_session` path logic + rotation
  (7), batch parsing (7), composite fallback without Pillow (3), foreground
  helper logic (7), `cmd_do` batch (Windows, 6), `cmd_do` foreground
  (Windows, 3), `cmd_capture` session default (Windows, 2), `Config.always_
  foreground` (5). All 68 existing tests remain green (total: 109 pass, 1 skip).

### Changed

- `oc capture`: `--out` default changed from fixed `_session/screenshot.png`
  (CWD-relative) to a sequenced/timestamped path inside the module-relative
  `_session/` folder.
- `oc do`: now accepts JSON array input; adds `--label`, `--shots`, and
  `--ensure-foreground` flags.  Single-object input without `--label` retains
  the original response format exactly.
- `oc run`: adds `--ensure-foreground` flag (single pre-loop activation check).
- `__init__.py` / `pyproject.toml`: version bumped to `0.3.0`.
- `tests/test_local_executor.py`: version assertion updated to `0.3.0`.

---

## [0.2.0] - 2026-06-20

### Added

- **`LocalExecutor`** (`drivers/local.py`, Windows): real OS driver via ctypes
  SendInput + mss. Implements the full `Executor` protocol plus the `OSDriver`
  surface (`launch_app` / `activate_window`).
  - **Screenshots** via `mss.grab()` + `mss.tools.to_png()` — pure PNG bytes,
    no numpy/opencv/Pillow needed. Adapted from USBPodcastStudio `screen_source.py`
    (MIT), threading/numpy stripped to a single synchronous grab.
  - **Mouse/keyboard input** via ctypes SendInput (zero extra deps, no GPL/LGPL).
    `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK` maps coordinates across the
    entire virtual desktop (multi-monitor safe). Keyboard via KEYBDINPUT
    (VK codes for combos, KEYEVENTF_UNICODE for `type`). Scroll via
    MOUSEEVENTF_WHEEL.
  - **DPI awareness** set at init via `SetProcessDpiAwarenessContext(-4)`
    (Per-Monitor-v2), so GetSystemMetrics and mss report true physical pixels on
    high-DPI displays.
  - **Pure coordinate math** extracted into `to_sendinput_coords()` — testable
    without any OS call. Verified: (0,0)→(0,0), (1,1)→(65535,65535),
    (0.5,0.5)→≈(32767,32767); negative-origin multi-monitor case covered.
  - Available as optional extra: `pip install open-compute[local]` (adds `mss`).
  - Live-tested on Windows 11: `oc capture` → 368 KB PNG at 1920×1080;
    `oc do mouse_move` → cursor moved to center of screen.
- **`oc` CLI** (`cli.py`): three sub-commands wired end-to-end.
  - `oc capture [--out PATH] [--monitor N]` — screenshot to PNG + JSON dims.
  - `oc do '<json>' [--mode MODE] [--yes]` — single action through SafetyPolicy
    + LocalExecutor. `--yes` pre-approves for non-interactive agent use.
    Exit codes: 0=executed, 1=deny/confirm, 2=error. Accepts `"action"` as alias
    for `"type"` in the JSON (Claude-style dicts).
  - `oc run "<goal>" --backend claude|openai [--max-steps N] [--model ID]` —
    autonomous AgentLoop with real API backend + LocalExecutor. OpenAI backend
    remains `[UNSICHER]`.
  - Entry point registered in `pyproject.toml`: `oc = "open_compute.cli:main"`.
- **`SKILL.md`**: loop protocol for Mode A (session-agent as reasoner, no API
  key). Documents the `capture → Read-Tool (see PNG) → decide action → do →
  recapture` cycle, the full action schema with all fields, coordinate convention,
  safety defaults, and Mode B pointer.
- **Tests** (`tests/test_local_executor.py`): 31 new tests — pure coordinate
  math (7), action dispatch with mocked Win32 (14), import-without-mss (3),
  CLI argument parsing (7). No real OS clicks in CI.
- **`open-compute[local]`** extra in `pyproject.toml` (`mss>=9.0`).
  `open-compute[all]` now includes `mss`.

### Changed

- Version bumped to `0.2.0`.
- `drivers/__init__.py`: added docstring explaining that `LocalExecutor` is
  intentionally not re-exported (preserves zero-import-time deps).

## [0.1.0] - 2026-06-20

### Added

- **Canonical action schema** (`actions.py`): backend-agnostic `Action` /
  `ActionType` plus pure mappers `to_claude()` and `to_openai()`. Coordinates are
  stored normalized (0..1); mappers denormalize per backend.
- **Coordinate handling** (`coordinates.py`): `normalize` / `denormalize` /
  `rescale`, centralizing the DPI/resolution problem. Fully tested.
- **Safety gate** (`safety.py`): central `SafetyPolicy` with `confirm` /
  `allow_all` / `read_only` modes, deny lists, human-in-the-loop confirmation
  callback, and an audit log. Fully tested.
- **Configuration** (`config.py`): `Config` dataclass with backend / scope /
  display / safety settings; `from_dict` / `from_json` loaders. No hard-coded
  paths.
- **Agent loop** (`loop.py`): the perception -> model-tool-call -> action ->
  feedback orchestrator. Backend, executor, perception provider, and policy are
  dependency-injected; default wiring runs offline on mocks. Step-by-step trace.
- **Backend abstraction** (`backends/`): `ComputerBackend` protocol +
  `get_backend()` factory. `MockBackend` (scripted, no SDK),
  `ClaudeComputerBackend` (Anthropic Messages API + `computer` tool
  `computer_20251124`, beta header `computer-use-2025-11-24`), and
  `OpenAIComputerBackend` (computer-use; model name configurable / `[UNSICHER]`).
  Vendor SDKs are imported lazily -- the package imports with no SDK installed.
- **Drivers** (`drivers/`): `Executor` / `BrowserDriver` / `OSDriver` protocols
  and a fully working `MockExecutor` for dry-runs and tests.
- **Perception** (`perception.py`): hybrid `Observation` + `PerceptionProvider`
  protocol. `ScreenshotPerception` is fully implemented; Set-of-Marks
  (OmniParser), accessibility, and DOM-snapshot providers ship as marked stubs.
- **Tests** (`tests/`): pytest coverage for coordinates, action mapping, safety
  gate, backend dispatch (incl. a Claude backend test via an injected fake
  client), and the agent-loop dry-run.
- Packaging: `pyproject.toml` with optional extras `claude` / `openai` /
  `browser` / `dev` / `all` (core has zero runtime dependencies); `LICENSE`
  (MIT), `SECURITY.md`, `.gitignore`, `llms.txt`, README (EN + DE).

### Known limitations

- Browser and OS drivers are **interfaces only** (no Playwright/CDP/host
  implementation yet).
- Perception providers other than `ScreenshotPerception` are **stubs**.
- The OpenAI backend's model name and exact Responses-API request shape are
  not fully verified; validate against live OpenAI docs before production use.
