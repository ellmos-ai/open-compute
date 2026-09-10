# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Documentation, Marketing & Design (Pfad B - 2026-09-10)

- **Third-Party License Audit & Inventory (`THIRD_PARTY_LICENSES.md`)**:
  - Authored comprehensive Third-Party Licenses & Transparency Notice documenting runtime boundaries, optional vendor adapters (`anthropic`, `openai`, `playwright`, `mss`, `Pillow`, `uiautomation`, `windows-capture`, `watchdog`, `clirec`, `mcp`), and QA dependencies (`pytest`, `ruff`, `setuptools`).
  - Formally guaranteed 100% permissive licensing (MIT, Apache-2.0, BSD-3-Clause, PSFL-2.0, HPND) with zero copyleft viral contamination.
  - Codified Zero-Egress Privacy Boundary (no network requests in offline/mock execution) and unprivileged user-mode execution (`RunAsInvoker`).
- **Marketing Audit & Target Personas (`MARKETING-LOG.txt`)**:
  - Codified 4 distinct target personas: Enterprise AI Agent Engineers & Platform Architects, Open-Source Agent Developers & AI Researchers, Security, Safety & Governance Compliance Officers, and Desktop & GUI Automation Specialists (RPA Modernizers).
  - Compiled high-intent discovery search keywords across English and German market segments.
  - Documented Unique Value Propositions (UVPs) vs. vendor demos (Anthropic) and heavy academic benchmark frameworks (OSWorld/Agent-S).
  - Codified the 10 Governance & Runtime Invariants (`INV-MOD-01` through `INV-SLA-10`).
- **Bilingual Documentation & Badge Parity (`README.md` & `README_de.md`)**:
  - Expanded Quick Navigation to 16 points with 100% reciprocal anchor parity between English and German.
  - Added Third-Party Audited badge linking to `THIRD_PARTY_LICENSES.md`.
  - Added explicit IDs `INV-MOD-01` through `INV-SLA-10` to the Governance & Runtime Invariants table.
  - Added dedicated `## Third-Party Licenses & Transparency` (EN) and `## Drittanbieter-Lizenzen & Transparenz` (DE) sections.
  - Synchronized test metrics across both READMEs to `689 passed, 2 skipped (100% green, 2026-09-10)`.
- **PEP 621 Metadata URLs (`pyproject.toml`)**:
  - Added `"Third-Party Licenses"` and `"Marketing Log"` to `[project.urls]`.
- **Machine-Readable AI Context (`llms.txt`)**:
  - Synchronized `Last-checked: 2026-09-10`, referenced `THIRD_PARTY_LICENSES.md`, `MARKETING-LOG.txt`, and documented the 10 Governance Invariants.
- **Contract Test Suite Expansion (`tests/test_metadata.py`)**:
  - Added automated contract tests for `THIRD_PARTY_LICENSES.md` existence and dependency audit.
  - Added contract tests for `MARKETING-LOG.txt` persona audits and high-intent discovery keywords.
  - Added contract tests for PEP 621 project URLs (`Third-Party Licenses` and `Marketing Log`).
  - Added contract tests for the 10 Governance Invariant IDs (`INV-MOD-01` to `INV-SLA-10`).

### Repository Hygiene & CI Hardening (Pfad A)

- **Technical Hygiene & Multi-Host Synchronization Hardening (2026-09-09)**:
  - Hardened `.gitignore` with multi-host cloud-sync conflict patterns (`*.sync-conflict-*`, `*.conflict`, `*-CONFLIT-*`, `*-conflict-*`, `*.sync-temp-*`), multi-agent lock patterns (`LOCK.*`, `*.lock`), testing/linting/packaging caches (`.ruff_cache/`, `.coverage`, `coverage/`, `htmlcov/`, `wheelhouse/`, `.wheel-smoke/`), and temporary files (`*.tmp`, `*.bak`, `*.swp`, `*~`, `*.log`).
  - Standardized pytest configuration in `pyproject.toml` with `addopts = "-v"`.
  - Fixed test root resolution in `tests/test_metadata.py` to use portable `Path(__file__).resolve().parents[1]` for cross-platform execution (Ubuntu CI / Windows / macOS).
  - Extended automated contract tests in `tests/test_metadata.py` with `test_gitignore_hygiene`, `test_pyproject_pytest_configuration`, and `test_security_policy_umbrella_contact_and_triage` (14 contract tests total).
  - Synchronized Shields.io test status (686 passed | 100% green), security SLA (48h), and code style (Ruff) badges across `README.md` and `README_de.md`.
  - Updated machine-readable context [`llms.txt`](llms.txt) with current verification timestamp (2026-09-09) and test suite metrics (686 passed, 2 skipped).

### Documentation & Discoverability (Pfad B)

- **Bilingual Documentation & Discoverability Overhaul (2026-09-08)**:
  - Added comprehensive Shields.io badges (Status `v0.9.0-stable`, Python 3.10-3.13, Tests `683 passed | 100% green`, Platforms, Local-first zero egress, Security Policy, LLM-Ready, Ecosystem, Umbrella, License).
  - Added Quick Navigation table of contents with 14 anchor links in both English and German READMEs (`README.md` and `README_de.md`).
  - Added Highlights & Core Philosophy overview.
  - Added dual Mermaid architecture and lifecycle diagrams (`flowchart TD` System Architecture Flow and `sequenceDiagram` Agent Loop & Safety Lifecycle).
  - Added Governance & Runtime Invariants table documenting 10 capabilities and guarantees.
  - Added Sibling Ecosystem & Partner Repositories matrix linking 11 federated repositories across `ellmos-ai`, `dev-bricks`, `file-bricks`, and `open-bricks`.
  - Added PEP 621 metadata URLs (`Homepage`, `Documentation`, `Repository`, `Issues`, `Changelog`, `Security`, `Parent Organization`, `Umbrella Ecosystem`) and OS classifiers to `pyproject.toml`.
  - Hardened GitHub Actions CI (`actions/checkout@v4`, `actions/setup-python@v5`, concurrency control, Python 3.13 matrix, ruff lint check step).
  - Upgraded `SECURITY.md` to full bilingual English/German structure with 48h response SLA, GitHub Security Advisories link, official contacts, and core threat model invariants.
  - Added automated metadata and discoverability contract testsuite in `tests/test_metadata.py` with 11 contract tests.
  - Cleaned up all Ruff linter errors (`open_compute/cli.py`, `open_compute/learning.py`, `tests/test_dirwatch.py`, `tests/test_learning.py`). Full suite 100% ruff clean.

### Added

- **`note_observation` tool + work-together mode** (Ticket
  T-20260825-767105130). New MCP tool: a model-to-human short observation
  line, written to a small, non-modal, always-on-top notes window
  (`ObservationOverlay` in `indicator.py`, modeled on `TkAbortChannel` but
  fire-and-forget instead of blocking) — the mirror image of `chat`
  (human-to-model). Never gated by the safety policy or the pre-action
  grace window (not a state-changing action). New bundled skill
  `open-compute-work-together`: a three-part spectator/assist mode —
  (1) the notes window above as a low-noise "what does the machine see"
  channel, (2) the existing `open-compute-clipboard-companion` skill for
  the clipboard half (referenced, not duplicated), (3) a narrowly scoped
  micro-takeover — one `type` call on a field the human has already
  visibly focused, then immediate return to observation. The Ticket
  T-20260825-540085216 activity cooldown already keeps a follow-up
  micro-takeover from re-triggering the grace window, with no new
  special-casing needed.

### Security

- **Pre-action grace window is now mandatory, closing a real bypass**
  (Ticket T-20260825-540085216). Previously, `_await_grace_period` only ever
  blocked if something had already armed a deadline — an explicit
  `signal_show` call, or the opt-in auto-signal path (`OC_SIGNAL_AUTO`, off
  by default). A caller that simply never called `signal_show` skipped the
  whole safety window with no config change required. Every gate-relevant
  tool call (`do` / `click_name` / `invoke` / `rec_replay` / `capture`) now
  arms and waits out the configured grace window unconditionally on the
  first action of a session, independent of `OC_SIGNAL_AUTO` and of whether
  `signal_show` was ever called. Only the operator's own canonical signal
  config (`pre_action_grace_seconds: 0`) or the `OC_SIGNAL_GRACE_SECONDS=0`
  environment override can disable it — never a tool-call argument.
- **`signal_show`'s `config_path` argument can no longer shorten the
  mandatory wait.** It still accepts an alternate, locally-authored config
  file (a legitimate operator feature), but the mandatory-arm path now
  enforces a floor derived from the *canonical* config (`OC_SIGNAL_CONFIG` /
  the fixed default path, never a caller-supplied path) — a `config_path`
  pointing at a near-zero grace can only ever raise the effective wait back
  up to the canonical value, never shorten it below it.

### Added

- `SignalConfig.grace_cooldown_seconds` (default 120s, env override
  `OC_SIGNAL_GRACE_COOLDOWN_SECONDS`): once a grace window has been waited
  out, further gate-relevant calls within the cooldown skip a new one — a
  session in continuous use is not interrupted on every single action. `0`
  disables the cooldown (every call waits out the full grace again).
- Bundled `open-compute-clipboard-companion` skill for paired live sessions:
  the human retains all GUI and publication actions while the agent observes
  and prepares field-specific clipboard content.

### Changed

- `SignalConfig.pre_action_grace_seconds` default lowered from 20.0s to
  4.0s (Ticket T-20260825-540085216, part 1a — the user found the initial
  20s wait too long; 4s sits in the requested 3-5s range).

### Fixed

- **`capture` failed every real call with "validation error for
  captureOutput: result Field required"** (Ticket T-20260905-467485001).
  `capture()` builds and returns its own `CallToolResult` with a raw,
  unwrapped `structuredContent` dict, but was annotated `-> Any`. FastMCP
  only skips output-schema generation/validation for a tool when its return
  type annotation is exactly `CallToolResult`; with `-> Any` it still
  auto-generates a `{"result": ...}` schema and validates
  `structuredContent` against it, which the unwrapped meta dict never
  satisfies. Reproduced against `mcp==1.29.1` via a real stdio round trip
  (matching the `uvx`-based production launch); fixed by annotating
  `capture() -> CallToolResult`.
- Server instructions (all six languages) now tell the calling agent to
  call `signal_show` before its first GUI action of a session, so the
  visible on-screen control indicator is not only opt-in via
  `OC_SIGNAL_AUTO`/an explicit call the agent has to remember unprompted.

## [0.9.0] - 2026-08-21

Visible pre-action countdown and accessibility release for ticket
`T-20260821-676249921`.

### Added

- `SignalConfig` now exposes `pre_action_grace_color` and the localizable
  `pre_action_grace_label` template. The configured duration remains
  `pre_action_grace_seconds`, with `OC_SIGNAL_GRACE_SECONDS` as its runtime
  override; no display-only 20-second constant was introduced.
- `signal_show` and `signal_status` now report `phase`, `countdown_seconds`,
  current/active/grace colors, and an accessibility label.
- The Win32 label window publishes its semantic title and emits
  `EVENT_OBJECT_NAMECHANGE` for screenreaders on each second transition.

### Changed

- The countdown uses a separate static purple default, displays
  `Start in N Sekunden` once per second, and changes exactly once to the
  configured mode color when the grace period ends. It does not flash, pulse,
  or depend on enabled Windows animations.
- Starting a new signal replaces and resets the previous countdown; zero
  seconds starts directly in the active phase.

### Fixed

- The custom paint handler now draws the current countdown text and phase color
  instead of repainting the immutable initial label after `SetWindowTextW`.
- Renderer startup failure, abort, hide, and restart no longer leave a hidden
  grace deadline armed.
- A mode disabled in `SignalConfig` now remains truly hidden and never arms an
  invisible countdown or auto-signal lease.

## [0.8.0] - 2026-08-21

Safety-hardening release for ticket `T-20260821-611823643`.

### Added

- `list_windows` now returns compatibility-preserving `hwnd`/`pid` fields plus
  stable `window_id`, `process_id`, and a server-issued `window_token`.
- MCP `capture` and `tree` return one-shot `observation_id` metadata;
  screenshots also expose the same value as `screenshot_id`. A coordinate
  action consumes exactly one observation and returns a fresh
  `post_action_observation` with new/owned-window candidates.
- Verified Unicode text delivery is segmented into bounded chunks and reports
  `requested_chars`, `sent_chars`, `complete`, `partial`, `segments`, and the
  exact target focus. Results and logs never echo the typed cleartext.
- Signal overlays have bounded leases (`ttl_seconds` / `OC_SIGNAL_TTL`, default
  120 seconds). `signal_status` reports `owner`, `session`, `mode`, `visible`,
  and `expires_at`.

### Changed

- **Breaking MCP safety contract:** coordinate actions require a fresh
  `observation_id` and a full descriptor or token previously issued by
  `list_windows`. Observation-bound actions are accepted only one per tool
  call. The old caller-supplied `coordinate_frame` is now only an equality
  assertion against the observation-bound frame.
- `type`, key actions, and `activate_window` require exact window binding.
  Focus is re-read immediately before input; activation uses the bound HWND
  instead of repeating a fuzzy title search.
- `click_name` and `invoke` resolve exact names first, reject ambiguous or weak
  matches by default, accept `exact=true`, and report match type, score, and
  alternatives. In particular, `Erstellen` cannot select `Wiederherstellen`.
- State-changing MCP calls show auto-signals before actuation and hide signals
  on normal turn end, abort, and error unless `keep_signal=true` explicitly
  extends the lease. Server shutdown also performs idempotent cleanup.

### Fixed

- Prevented text from reaching a different foreground window after a focus
  change and made partial writes observable.
- Prevented stale coordinates, reused observations, changed capture/tree
  states, and newly covered/modal windows from being silently trusted.
- Prevented orphaned border/cursor overlays after action completion or abort.

## [0.7.0] - 2026-07-31

Alpha release `v0.7.0-alpha`: screen-usage signaling (overlay, config, abort hotkey), chat, push-to-talk, MCP signal/chat/talk tools, plus the 2026-07-28 companion/handoff core.

### Fixed (fail-closed pre-click window verification, 2026-08-20)

Ticket T-20260819-561386711 hardened every coordinate click/drag/mouse-down in
the CLI and MCP paths. Raw coordinates are no longer sufficient: callers must
supply a robust expected top-level identity (`hwnd` + `pid` + exact normalized
title) and the physical capture frame (`left`, `top`, `width`, `height`). The
guard rebases window-local capture coordinates into the executor's current
virtual-desktop frame, then calls Win32 `WindowFromPoint` immediately before
the backend. `GetAncestor(..., GA_ROOT)` promotes child/overlay handles. A
missing identity/frame, an unresolvable point, or any mismatch returns a
structured `preclick_verification_failed` result and invokes the click backend
zero times. `click_name` derives both values from its UIA window scope, so the
semantic path stays the default and needs no manual identity plumbing.

Root-cause matrix from the four incidents:

| Class | Code evidence | Resolution / remaining boundary |
|---|---|---|
| DPI / rounding | Per-Monitor-v2 and one normalized mapping already existed, but integer conversion can still move a boundary point by about one pixel. More importantly, `capture(window=...)` produced window-local 0..1 coordinates while `do` interpreted them as virtual-desktop 0..1. | The explicit source frame is now transformed through physical pixels into the live executor frame; invalid/out-of-frame mappings fail closed. A one-pixel edge remains possible inside the verified window. |
| Z-order / focus race | Safety and optional foreground activation happened before `executor.execute`; nothing re-read the window at the target point after another window could cover it. | `WindowFromPoint` is the final probe before backend dispatch; a changed top-level identity blocks the action. |
| Overlay / child handle | Win32 can return a child control or overlay HWND rather than the application's top-level HWND; there was no comparison at all. | The probe resolves child → `GA_ROOT` and compares root HWND, PID and title. Unresolvable roots fail closed. |
| Capture → click time gap | Capture/UIA resolution and click were separate calls with an unbounded human/model delay; the code trusted stale coordinates. | Expected capture identity is compared with the live target-point root immediately before dispatch. Same-window content/layout changes remain possible; use UIA `click_name`/`invoke` rather than raw coordinates. |

The requested post-click transaction was evaluated but is not claimed as a
correctness gate yet. `LocalExecutor.execute` already returns an after-capture
and CLI composites can record before/after, but an exact full-screen hash is
dominated by clocks, animations and unrelated windows. A reliable target-only
diff first needs the split CLI/MCP GDI→WGC window-capture paths centralized by
HWND plus a settle/difference policy; that bounded follow-up is recorded in
`TODO.md`. Pre-click prevention is therefore fail-closed now, while outcome
classification remains explicit future work.

### Added (Not-Aus / kill switch, pre-action grace period, 2026-08-18)

Ticket T-20260818-895473048: an incident where a screenshot briefly captured
private mail content while the user was actively working made the planned
"abort button" feature priority. Every gate-relevant tool
(`do`/`click_name`/`invoke`/`rec_replay`/`capture`) now honors a server-side
kill switch:

- **Always-visible abort button** drawn top-right on the signal overlay — the
  one popup that is NOT click-through, independent of whether an abort
  hotkey is also configured. **Panic hotkey** (`signal.abort_hotkey`)
  triggers the exact same hard stop, not just a reason box as before.
- **Immediate + total stop:** the kill switch latches synchronously (before
  any reason dialog even opens), stops a `do` batch mid-flight (checked
  before every queued step) and a running `rec_replay` (checked before
  every replayed action via `cli._GatedExecutor`'s new `abort_check`), and
  denies every further gate-relevant call — even under
  `OC_SAFETY_MODE=allow_all` — until a fresh `signal_show(...)` re-arms the
  session.
- **Abort with reason:** free text or 1-click from a configurable list
  (`signal.abort_reasons`); delivered as `abort_reason` directly on the
  result of the blocked/next tool call, not only via `signal_status`
  (which still reports `aborted`/`abort_reason`, non-consuming).
- **Pre-action grace countdown:** an explicit `signal_show(...)` call arms
  `signal.pre_action_grace_seconds` (default 20s, override via
  `OC_SIGNAL_GRACE_SECONDS`); the first state-changing action *and* the
  first `capture()` after that block server-side until the countdown
  elapses or the kill switch fires, with the overlay label showing
  "Uebernahme in Ns" meanwhile. Deliberately NOT armed by `OC_SIGNAL_AUTO`'s
  reactive auto-show (which only appears *after* an action already ran) —
  arming it there would stall the agent's very next step instead of
  protecting the first one.
- **User-activity watch (opt-in, `OC_HUMAN_ACTIVITY_WATCH=on`):** wired the
  existing (previously unused) `human_activity` module into
  `do`/`click_name`/`invoke` — genuine, non-agent-issued recent mouse/
  keyboard input now auto-pauses the session (same kill-switch state,
  fresh `signal_show` needed to resume). Off by default: a shared
  workstation's `GetLastInputInfo` reflects whatever the operator is doing
  *right now*, including issuing the tool call itself, so an unconditional
  default risks false-positive pauses. Not yet wired into `rec_replay`
  (documented follow-up).
- `open_compute/indicator.py`: `SignalConfig` gained `pre_action_grace_seconds`
  and `abort_reasons`; `TkAbortChannel` gained a `reasons` quick-pick row
  above the free-text field; `WindowsBorderOverlay` gained the abort button,
  a debounced `_fire_abort` (one human gesture, one dialog), and the
  countdown label update.

### Changed (Discoverability verification, 2026-08-16)

- Refreshed the EN/DE README test and hygiene badges plus the machine-readable
  `llms.txt` check date after a clean full suite run (564 passed, 1 skipped).

### Added (Signal auto-hide: `OC_SIGNAL_IDLE_HIDE`, 2026-08-06)

- An overlay put up by `OC_SIGNAL_AUTO` now takes itself down once the
  steering stops. Every state-changing tool call (`do` / `click_name` /
  `invoke` / `rec_replay`) re-arms an idle countdown; when it expires with
  no further action, the overlay is hidden — no more red CONTROL border
  left standing after a run, waiting for a manual `signal_hide`.

### Changed (README banner, 2026-08-06)

- New README banner `assets/banner.png` (generated glassmorphic
  capture-reason-act motif with typographic overlay); the previous
  SVG banners (`banner-relief.svg`, `banner_bw.svg`) were removed.
  `OC_SIGNAL_IDLE_HIDE` sets the window in **seconds (default 60)**; `0`,
  empty, or `off` disables it and keeps the previous behavior. Only an
  auto-shown overlay is ever swept away: a manual `signal_show` is never
  touched, and showing one manually over an auto overlay takes ownership
  and cancels the countdown. The timer is a daemon `threading.Timer`
  guarded by a reentrant lock (no polling), cancelled on `signal_hide` and
  at server shutdown, and it re-checks ownership when it fires so it cannot
  hide an overlay that changed hands in the meantime. `signal_status` gained
  `auto_shown` and `idle_hide_armed`; an unusable env value reports
  `signal_idle_hide_error` in the tool result instead of failing the action.
  16 new tests in `tests/test_mcp_server.py` (suite: 551 passed, 1 skipped).

### Changed (Discoverability audit, 2026-08-03)

- Linked the verified Glama directory listing for the published `open-compute-mcp` launcher from both README landing pages, refreshed the machine-readable `llms.txt` verification date, and synchronized the current full-suite status (535 passed, 1 skipped).

### Added (Auto-Signal: `OC_SIGNAL_AUTO`, 2026-08-02)

- Added `OC_SIGNAL_AUTO` (MCP server env var, value = a `SessionMode` name
  like `control`): once set, the first state-changing tool call that
  actually passes the safety gate (`do` / `click_name` / `invoke` /
  `rec_replay`) auto-shows the screen-usage signal overlay in that mode —
  no more forgetting to call `signal_show` before steering the desktop. A
  signal already visible (shown manually, in any mode) is never overridden;
  read-only tools (`capture`, `tree`, `list_windows`, ...) never trigger it;
  an action blocked by the gate (denied / needs confirmation) never
  triggers it either. An invalid `OC_SIGNAL_AUTO` value reports
  `auto_signal_error` in the tool result instead of crashing or blocking
  the action it is attached to. `OC_SIGNAL_AUTO=off` (or unset, the
  default) disables the feature. Shared logic factored out of `signal_show`
  into `_show_signal_indicator()`; 12 new tests in `tests/test_mcp_server.py`.
### Added (Profile-filtered MCP perception, 2026-08-31)

- Added strict, provider-agnostic filter profiles in
  `open_compute/perception_filter.py`: semantic-first UIA focus packets with
  hard character/element/value budgets, selection digest fallback, bounded
  visual lenses, excluded GUI names/windows, and tool/action allowlists.
- Added MCP tools `observe_filtered` and `capture_filtered`. Raw UIA trees stay
  local to the filter call; visual capture is clamped to the declared lens and
  excluded overlapping windows are blanked before the image is returned.
- `do(profile=...)` now rejects any action outside the supplied profile before
  the existing SafetyPolicy gate and executor.
- Added localized tool descriptions in en/de/es/ja/ru/zh and 8 focused tests,
  including exact excluded-window pixel blanking at the lens boundary.
  Verified full suite after integration with the current safety baseline:
  672 passed, 1 skipped.

### Fixed (Maintainer verification, 2026-08-01)

- Made Win32 foreground-window detection robust when the host returns no
  foreground handle.
- Refreshed the README and TODO test status to the verified full-suite result:
  525 passed.

### Added (MCP-Tools für Signal/Chat/Talk, 2026-07-31)

- Added six human-in-the-loop tools to the FastMCP server: `signal_show` /
  `signal_hide` / `signal_status` / `signal_abort` (the signal overlay now
  persists in the server process across calls — no time-bounded CLI wrapper;
  abort-hotkey messages are held in server state and consumed via
  `signal_status`, never printed to the stdio transport), `chat`
  (human→model message + optional screenshot) and `talk` (push-to-talk WAV).
  All side effects injectable for headless tests; the overlay is cleared on
  server shutdown alongside the held-input release.
- Added i18n tool descriptions (en/de/es/ja/ru/zh) for the new tools and
  10 MCP tests. Full suite: 524 passed, 1 skipped.

### Docs / Fixed (README update + test-count correction, 2026-07-31)

- Documented the 2026-07-31 signal work in both READMEs: architecture sketch
  gained the human-in-the-loop signaling layer (`indicator.py` / `talk.py`),
  quick start gained `oc signal` / `oc chat` / `oc talk` examples (3j–3l),
  the status section describes the overlay, `SignalConfig`, abort channel,
  push-to-talk and chat, and the stale "ownership-overlay rendering not
  implemented" stub note was corrected.
- Fixed the test badge: it now reads **515 passed**. The 684 figure measured
  earlier today included 169 duplicate tests from untracked
  `tests/*-WORKSTATION-LG.py` sync-mirror files that only existed in the
  OneDrive working tree; the canonical repo collects 515 (+1 skipped).

### Added (Signal-Config: Farben und Rahmen/Cursor getrennt schaltbar, 2026-07-31)

- Added `SignalConfig` / `SignalModeConfig` (`open_compute/indicator.py`):
  JSON config for the signal overlay with the built-in palette as defaults.
  Per mode: `enabled`, `color`, `label`, `border` (screen frame) and
  `cursor` (cursor ring) — border and cursor toggle independently, e.g. the
  use case "cursor highlight only while the model drives, and no screen
  border" is `control: {border: false, cursor: true}`. Atomic writes,
  `OC_SIGNAL_CONFIG` env or `_state/signal-config.json`.
- Added `oc signal config --init|--show [--path]` and
  `oc signal on --config PATH [--no-border] [--no-cursor]`; the abort hotkey
  can also come from the config file.
- `OverlayRenderer.show()` gained `border`/`cursor` flags;
  `WindowsBorderOverlay` renders frames/ring per flag (live-verified:
  `_session/signal_usecase.png`).
- Added 9 config tests (`tests/test_signal_config.py`). Full suite:
  684 passed, 1 skipped.

### Added (Folgeblöcke Signalisierung: Abort-Hotkey, Chat, Push-to-Talk, 2026-07-31)

- Added a global abort hotkey to `WindowsBorderOverlay`:
  `oc signal on --abort-hotkey [COMBO]` (default `ctrl+alt+esc`) registers a
  MOD_NOREPEAT hotkey on the overlay thread; on press the topmost Tk reason
  box opens and the message is printed as a JSON line for the calling agent.
  `parse_hotkey()` handles `ctrl+alt+esc` / `f9` / `shift+a` style specs.
- Added `oc chat [--channel console|tk|none] [--shot]`: human-to-model short
  message about screen content, optionally with a fullscreen screenshot from
  `_session/`, delivered as a JSON line. No model call inside the command —
  the agent answers in its own channel.
- Added `open_compute/talk.py` + `oc talk --key F9`: push-to-talk voice
  capture — hold key → speak → release — writing WAV to `_session/` via
  winmm MCI (zero-dependency). STT/TTS deliberately stay model-side; the WAV
  path goes to the agent as JSON. All side effects injectable (mci sender,
  key probe, sleep) for headless tests.
- Added 17 headless tests (`tests/test_talk.py`, `tests/test_signal_hotkey.py`).
  Full suite: 676 passed, 1 skipped.

### Added (Bildschirm-Signalisierung / screen-usage display, 2026-07-31)

- Added `open_compute/indicator.py`: the renderer side of
  `cooperative.OwnershipIndicator` (previously protocol + null only).
  `ScreenSignalIndicator` maps session modes to signal colors — red for
  CONTROL (model may act), blue for OBSERVE (model only watches), green
  COMPANION, orange HANDOFF, grey PAUSED — and is a drop-in for
  `CooperativeOrchestrator(indicator=…)`.
- Added `WindowsBorderOverlay` (ctypes, Windows-only, lazy): click-through,
  topmost glowing border around the virtual screen, neon cursor ring and a
  status strip (agent | mode | scope), on a dedicated message-loop thread.
  Live-verified on a real desktop (screenshot `_session/signal_smoke.png`).
- Added abort channel with reason entry: `AbortChannel` protocol,
  `ConsoleAbortChannel` (stdin) and `TkAbortChannel` (topmost Tk input box).
  The captured short message is forwarded to `on_abort_message` so the caller
  can pass it to the model when compute mode is aborted.
- Added `oc signal on --mode … [--for SECS] [--no-cursor]` and
  `oc signal abort [--channel console|tk|none]` CLI commands.
- Wired the previously test-only CLI commands into `cli.py`: `oc session`
  (companion/request-control/grant/status/pause/release), lease-gated
  `oc window`, and `oc capture-series` — the three pre-existing red tests
  now pass.
- Added 14 headless tests (`tests/test_indicator.py`). Full suite:
  659 passed, 1 skipped.

### Changed / Fixed (Technical Hygiene & Maintenance Check, 2026-07-30)

- Updated `llms.txt` Last-checked header timestamp to 2026-07-30.
- Synchronized Pytest test badges in `README.md` and `README_de.md` to 476 passed tests (1 skipped, 100% green).
- Cleaned 35 unused module imports via Ruff linter pass and fixed `SafetyPolicy` string type annotation in `cli.py`.
- Fixed typo in German README badge (`Gefrüft` -> `Geprüft`).

### Added (Headless cooperative safety core, 2026-07-28)

- Added a pure/mockable `GetLastInputInfo` single-shot adapter and timestamp-only
  human-vs-agent activity classifier; no hook, watcher, or raw input logging.
- Added a backend-independent perceive/stabilize/act/verify orchestrator with
  scoped lease, human/emergency-stop gates, action idempotency, bounded retries
  and fail-closed verification.
- Added screen-prompt-injection blocking, SHA-256 hash-chained sanitized audit,
  bounded retention with explicit deletion, and crash cleanup contracts.
- Added non-rendering ownership-indicator and in-memory emergency-stop
  interfaces. Live input/capture/window/overlay/voice/display/GUI acceptance
  remains explicitly outside this headless slice.
- Added 25 pure/mock tests. The headless suite is green with 473 passed and
  four explicitly deselected host-query tests.

### Added (Companion/Handoff core, 2026-07-28)

- Added an explicit, fail-closed companion/handoff/control state machine with
  scoped expiring leases, heartbeat, pause/release and human-interrupt yield.
- Added `oc session ...`, lease- and SafetyPolicy-gated `oc window ...`
  operations, and bounded deduplicating `oc capture-series`.
- Added unambiguous title/PID/HWND resolution and injectable Win32 adapters;
  ambiguous selectors return candidates instead of choosing the first match.
- Added 17 pure/mock CLI and core tests. Full suite: 452 passed. Real Windows
  interaction, human-interrupt detection, voice, overlays and virtual displays
  remain explicit live/future gates.

### Changed / Added (Discoverability & Marketing Pass, 2026-07-27)

- **Test Suite Alignment:** Updated test badges and `llms.txt` index status to reflect 435 passing tests (100% green).
- **README & Landing Pages:** Added Ecosystem badge (`Ecosystem: ELLMOS / open-bricks`) and Hygiene verification badge (`Hygiene: 2026-07-27`) to `README.md` and `README_de.md`.
- **LLM Metadata:** Updated `llms.txt` `Last-checked` timestamp to `2026-07-27`.

### Fixed (After-Care-Pflegerunde, 2026-07-26)

- **Installationsanleitung zeigte auf ein fremdes Paket.** `pip install open-compute` installiert
  nicht dieses Projekt: Der Name ist auf PyPI von einem unabhängigen Projekt belegt
  ("multi-agent systems for healthtech", 0.1.9), und open-compute hat dort nie veröffentlicht.
  README (EN + DE), `llms.txt` und `SKILL.md` weisen jetzt auf
  `git+https://github.com/ellmos-ai/open-compute.git` und benennen die Namenskollision
  ausdrücklich. Die Extras-Liste ist dabei von einem Codeblock in eine Tabelle gewandert.
- **Test-Zahl nachgezählt statt fortgeschrieben:** Badge und Fließtext sagten `434 passed`,
  der Lauf ergibt `434 passed, 1 skipped`. In README (EN + DE), `CHANGELOG.md` und der
  STATUS-Tabelle in `TODO.md` (dort stand noch `360 pass`) korrigiert.
- **Verweise auf `RELEASE_GATE.md` entfernt.** Die Datei ist gitignored; drei veröffentlichte
  Dateien zeigten auf sie und liefen für Leser ins Leere.
- **Interne Angaben entfernt:** absoluter lokaler Installationspfad in `LIVE_SMOKE_RUNBOOK.md`,
  Verweise auf interne Ablagen und ein Personenname in `TODO.md`.
- **Modul-Manifest:** `visibility` von `public-candidate` auf `public` — das Repository ist
  seit Längerem öffentlich.

### Changed / Added (Discoverability & Maintenance, 2026-07-25)

- **Pytest Configuration:** Added `pythonpath = "."` to `[tool.pytest.ini_options]` in `pyproject.toml` for zero-setup root `pytest` test discovery (434 passed tests).
- **README & Landing Pages:** Added Pytest status badge (`434 passed`) and LLM-Ready status badge to `README.md` and `README_de.md`, along with machine-readable AI/LLM integration notice callout (`> [!NOTE]`).
- **LLM Metadata:** Updated `llms.txt` `Last-checked` timestamp to `2026-07-25`.


### Added (comparison with AB498/computer-control-mcp, 2026-07-12)

- **Hold primitives in the canonical schema:** `mouse_down` / `mouse_up` /
  `key_down` / `key_up`, with an optional `button` field. The composite actions
  cannot express a button that stays down *across* other actions, which is what
  rubber-band selection, modifier-held multi-select and press-to-move game input
  need. They are host-side (no Claude/OpenAI computer-tool equivalent — the
  mappers reject them) and gated as risky in **both** halves, so `read_only`
  stays free of synthesized input. `LocalExecutor` tracks what it holds and
  offers `release_all()`; the MCP server calls it on shutdown so a client that
  dies mid-drag cannot strand a pressed button or modifier on the user's desktop.
- **`list_windows` and `get_screen_size` MCP tools.** The reasoner had no way to
  discover window titles and was left guessing a substring for
  `capture(window=...)` / `tree(window=...)`. `list_windows` returns exact titles,
  pixel rects, minimized/foreground flags and a normalized 0..1 center in the same
  frame `do` expects; `get_screen_size` returns the virtual-desktop geometry that
  frame refers to.

### Fixed

- **`LearningManager` no longer lets `_state/outcomes.jsonl` and
  `_state/lessons.jsonl` grow without bound.** Both logs are now trimmed to a
  bounded tail after each append, so long-lived 24h sessions keep warmstart
  history without accumulating an ever-growing JSONL file. The related
  `weights.json` and `profiles.json` writes now also go through the same
  atomic temp-file replacement path.
- **`oc watch-dir --once` now updates `_session/dirwatch_snapshot.json` with a
  lock file and atomic replace.** Snapshot updates for one watched path-set no
  longer risk clobbering unrelated stored baselines through a direct
  read-modify-write writeback, and the on-disk JSON store is never rewritten in
  place.

- **`capture(window=...)` returned an all-black image for hardware-composited
  windows** (Roblox Studio, Blender, GPU-accelerated browsers). A GDI region grab
  of such a window does not fail — it silently yields a black rectangle, so
  "the grab succeeded" was never proof of a usable frame. The capture path now
  checks the frame (`wgc.is_blank_png`) and re-grabs through
  Windows.Graphics.Capture when it is blank; `OC_WGC_WINDOWS` forces WGC for
  named windows. Window resolution stays HWND-based (`grab_window_png(hwnd)`),
  so WGC and GDI cannot disagree about which window was meant. Without the `wgc`
  extra the black frame is still returned rather than failing the call.
- **WGC could hang the calling process indefinitely.** The watchdog only received
  its `CaptureControl` through the frame callback, so a target that never
  produced a frame — WGC only pushes on redraw, so an idle window is normal —
  left nothing to stop and blocked forever (reproduced on a real desktop: a
  paused player window hung the call past 200s). Capture now runs
  `start_free_threaded()`, which returns the control up front; an uncapturable
  window now raises immediately and an idle one fails within seconds. This also
  affected the pre-existing monitor fallback, not just the new window path.
- **The `wgc` extra was not installed in the registered MCP venv**, so the
  fallback was dead in the deployed server; and the editable install still
  pointed at the pre-`.TOOLS` module path, so `import open_compute` failed
  outside pytest. Both repaired on the laptop host.

### Security (module review 2026-07-04)

- **`oc rec replay` no longer bypasses the safety gate.** Replaying a
  `.clirec` file previously drove the raw `LocalExecutor` directly — real
  mouse/keyboard input without confirm dialog, deny list, or audit trail.
  A new `_GatedExecutor` wrapper now routes every replayed action through
  `SafetyPolicy.evaluate()`; `oc rec` accepts `--mode`/`--yes` (default
  `confirm` — a replay of real inputs never runs unprompted), and a gated
  denial exits cleanly instead of tracebacking.
- **`--ensure-foreground` no longer fires before the policy check** in the
  batch/label path of `oc do`: even in `read_only` mode the real
  focus switch (`SetForegroundWindow`) used to execute before the gate
  denied the batch. The activation is now deferred until the first action
  has passed the gate (matching the already-correct single-action path).
- Regression tests for both in `tests/test_safety_gating_fixes.py`
  (suite: 360 → 365 green).

### Fixed

- Test collection no longer breaks without the optional `clirec` package:
  `tests/test_clirec_external_adapter.py` skips cleanly (and picks up a
  sibling `../clirec` checkout for local development); the mss-dependent
  window-capture test skips when `mss` is not installed.

### Added

- **MCP server** (`open-compute[mcp]`): a FastMCP server (`open_compute/mcp_server.py`,
  console script `open-compute-mcp`) exposing the keyless Mode-A loop as native MCP
  tools — `capture` (returns an image), `do` (single or batch canonical actions),
  `tree` / `click_name` / `invoke` (Windows UIA semantic targeting), `watch_dir`,
  `push_status`, and `rec_replay`. One warm `LocalExecutor` persists for the process
  lifetime (closes the "Prozess-Persistenz" TODO item); screenshots return as MCP
  image blocks; every state-changing tool passes `SafetyPolicy` — `OC_SAFETY_MODE`
  is an operator ceiling (a per-call `mode` can only tighten it, never loosen it) plus
  an optional `OC_DENY` hard deny list. 19 mock-only tests (`tests/test_mcp_server.py`).
  A thin npm launcher package `open-compute-mcp` (spawns the Python server, `npx`
  parity with Node MCP servers) lives alongside the other MCP servers.
- GitHub Actions workflow `open-compute tests` runs the mock-only pytest suite
  and a compile check on Python 3.10, 3.11, and 3.12 for pushes and pull requests.
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
- Optional extra `open-compute[clirec]` now points to the external `clirec`
  package; `oc rec` lazy-loads it only when recording/replay commands are used.

### Changed

- Extracted the previous `open_compute/clirec/` implementation into the new
  standalone repository `ellmos-ai/clirec`. `open_compute.clirec.*` remains as
  a compatibility wrapper namespace.

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
