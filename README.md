# open-compute

<img src="assets/banner.svg" width="100%" alt="open-compute banner"/>

**EN** | [DE](README_de.md)

[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange)](CHANGELOG.md)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![Tests](https://github.com/ellmos-ai/open-compute/actions/workflows/tests.yml/badge.svg)](https://github.com/ellmos-ai/open-compute/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**A model-agnostic computer-use core: one agent loop, any reasoning model behind a single interface.**

open-compute is a small, dependency-light Python core for building computer-use
agents (LLM-driven GUI / desktop / browser automation). It implements the
**perception → model-tool-call → action → feedback** loop and keeps the
reasoning model swappable behind a single `ComputerBackend` interface. **No
provider is privileged**: Anthropic Claude and OpenAI CUA are two equally-ranked
API backends, and the offline `mock` backend is the default. A **keyless** path
also exists today via Mode A, where the host model itself reasons — and it can
run that loop either inline or in a self-spawned subagent for context economy
(see [usage pattern](#usage-pattern--inline-a-vs-self-subagent-b)). The core has
**zero runtime dependencies**; vendor SDKs (`anthropic`, `openai`) are
**optional, lazily imported** extras — `import open_compute` works with none of
them installed, and the default mock wiring runs fully offline.

---

## Why

Every computer-use model — Anthropic's Claude `computer` tool and OpenAI's
computer-use tool — shares the same agent-loop *shape* but differs in transport,
coordinate frame, and action names. open-compute factors out the common parts so
you write the loop once and swap the reasoning model freely behind one
`ComputerBackend` interface:

- A **canonical action schema** with one mapper per backend.
- **Normalized (0..1) coordinates** internally, denormalized per backend /
  resolution / DPI in one tested utility — the DPI problem solved centrally.
- A **central safety gate** ("confirm before risky actions") evaluated before
  every action.
- A **hybrid perception** interface (screenshot + Set-of-Marks / accessibility /
  DOM), so you can move from pure pixel-vision to semantic targeting later.

---

## Architecture

```
                        +-----------------------------------------+
                        |        AGENT LOOP / ORCHESTRATOR        |
                        |  goal -> perceive -> backend -> safety  |
                        |        -> execute -> re-perceive        |
                        +-------------------+---------------------+
                                            |
        +-----------------------------------+-----------------------------------+
        |                                   |                                   |
+-------v---------+              +----------v-----------+            +----------v----------+
| PERCEPTION      |              | CANONICAL ACTIONS    |            | SAFETY / POLICY     |
| - screenshot    |              | click/type/key/      |            | - confirm-at-action |
| - set-of-marks  |              | scroll/drag/wait/    |            | - allow / deny list |
|   (OmniParser)* |              | screenshot + OS ext  |            | - read-only mode    |
| - accessibility*|              | (launch/activate)    |            | - audit log         |
+-------+---------+              +----------+-----------+            +----------+----------+
        |                                   |                                   |
        +-----------------+-----------------+----------------------------------+
                          |
              +-----------v------------+   COORDINATE / DPI NORMALIZATION
              | BACKEND ABSTRACTION    |   - internal: normalized (0..1)
              | (ComputerBackend)      |   - denormalize per backend:
              +-----+--------+---------+     * Claude: global px (display_w x display_h)
                    |        |    |          * OpenAI: px (computer_call)
        +-----------+        |    +-----------+   * Mock: synthetic
        |                    |                |
+-------v-------+   +--------v-------+  +-----v---------+
| Claude        |   | OpenAI CUA     |  | Mock backend  |
| computer_2025 |   | computer-use-  |  | (no SDK,      |
| 1124 + beta   |   | preview [?]    |  |  offline)     |
| (host runs)   |   | (host runs)    |  |               |
+---------------+   +----------------+  +---------------+

  * = stub / interface in this release (see Status)
```

---

## Install

```bash
pip install open-compute               # core only, zero runtime deps
pip install open-compute[claude]       # + anthropic SDK
pip install open-compute[openai]       # + openai SDK
pip install open-compute[local]        # + mss (real Windows screenshots + input)
pip install open-compute[wgc]          # + WGC fallback for DirectX surfaces (pulls numpy/OpenCV)
pip install open-compute[compose]      # + Pillow (Before|After composite + annotated shots)
pip install open-compute[watch]        # + watchdog (native FS events for directory-watch feed)
pip install open-compute[clirec]       # + external clirec package for oc rec workflows
pip install open-compute[record]       # + clirec[record] capture backend compatibility
pip install open-compute[mcp]          # + mcp SDK — MCP server (console script: open-compute-mcp)
pip install open-compute[local,wgc,claude] # local executor + WGC fallback + Claude backend
pip install open-compute[all]          # + anthropic, openai, playwright, mss, WGC, Pillow, watchdog, clirec, mcp
```

Until `clirec` has a package release, install it directly when using `oc rec`:

```bash
pip install git+https://github.com/ellmos-ai/clirec.git
```

Python 3.10+.

---

## Quick start

### Mode A — No API key: session-agent as reasoner (chat skill)

Run `oc capture` / `oc do` manually from a Claude Code session. The session
model sees the PNG via the Read tool and decides the next action:

```bash
# 1. Install the local extra (Windows only; provides real screenshots + input)
pip install open-compute[local]

# 2. Capture a screenshot — saved automatically to _session/ (never loose on Desktop)
oc capture
# -> {"path": ".../_session/0001_20260620_143200.png", "width": 1920, "height": 1080}
# Then: read the PNG with your Read tool to see the screen.

# 3a. Execute one canonical action (single, backwards-compatible)
oc do '{"type":"mouse_move","x":0.5,"y":0.5}' --mode allow_all
oc do '{"type":"left_click","x":0.25,"y":0.1}' --yes   # --yes = agent pre-approved

# 3b. Execute with Before|After composite (Pillow optional)
oc do '{"type":"left_click","x":0.5,"y":0.3}' --label "click_ok" --yes
# -> {"result":"executed","action":"left_click","composite":"_session/0002_click_ok.png"}

# 3c. Execute a batch/macro (JSON array, one call = multiple actions)
oc do '[{"type":"mouse_move","x":0.5,"y":0.5},{"type":"left_click","x":0.5,"y":0.3}]' --yes
# -> {"result":"batch","count":2,"width":1920,"height":1080}

# 3d. Ensure the target window is in the foreground before acting
oc do '{"type":"left_click","x":0.5,"y":0.3}' --ensure-foreground "Word" --yes

# 3e. Save a full-res after-shot + annotated click marker (v0.5, Pillow optional)
oc do '{"type":"left_click","x":0.5,"y":0.3}' --yes --fullres
# -> {"result":"executed",...,"fullres_annotated":"_session/...fullres.png"}

# 3f. Capture only the active window's bounding rect (v0.5, Windows)
oc capture --window "Word"
# -> {"path":"...","width":800,"height":600,"window":"Word","region":{...}}

# 3g. Watch a directory for changes (v0.5)
oc watch-dir ~/Downloads --for 5       # collect 5 s, print JSON events
oc watch-dir ~/Downloads --once        # one-time snapshot diff

# 4. Recapture and repeat until done (or read the "composite" After-shot directly).
```

See `SKILL.md` for the full loop protocol, action schema, coordinate guide, and
environment variable reference.

### Mode B — Autonomous loop with an API backend

The backend is selected by name; `claude` and `openai` are equally supported
(each needs its own key + extra). For a **keyless** path, use Mode A above — the
host model reasons itself, optionally in a self-spawned subagent (see
[usage pattern](#usage-pattern--inline-a-vs-self-subagent-b)).

```bash
# Claude (needs ANTHROPIC_API_KEY + open-compute[local,claude]):
oc run "Find the latest invoice in the Downloads folder" --backend claude --max-steps 15

# OpenAI (needs OPENAI_API_KEY + open-compute[local,openai]):
oc run "Find the latest invoice in the Downloads folder" --backend openai --max-steps 15
```

Or in Python — `get_backend(name, ...)` builds whichever you name; inject your
own executor or use `LocalExecutor`:

```python
from open_compute import AgentLoop, Config, get_backend
from open_compute.drivers.local import LocalExecutor   # Windows; needs mss
from open_compute.safety import SafetyPolicy

executor = LocalExecutor()   # real display + input
config = Config(backend="claude", scope="os",
                display_width=executor.width, display_height=executor.height)
backend = get_backend("claude", executor.width, executor.height, model="claude-opus-4-8")

loop = AgentLoop(
    config,
    backend=backend,
    executor=executor,
    policy=SafetyPolicy(mode="confirm",
                        confirm_callback=lambda a: input(f"run {a.type.value}? [y/N] ") == "y"),
)
loop.run("Find the latest invoice in the Downloads folder")
```

### Offline dry-run (no API key, no display, mock only)

```python
from open_compute import AgentLoop, Config

loop = AgentLoop(Config(backend="mock", safety_mode="allow_all"))
result = loop.run("Open the settings page and enable dark mode")
print(result.done, result.steps)
for trace in result.traces:
    print(trace.index, trace.backend_message, [a.type.value for a in trace.executed])
```

---

## MCP server (native tool-calls, keyless)

Expose the keyless **Mode A** loop to any MCP client as **native tools** — the
client is the reasoner (no API key, model-agnostic). Versus driving `oc` by hand,
a long-lived server keeps **one warm `LocalExecutor`** resident (no Python restart
per action) and returns screenshots as MCP **image** blocks. Windows-only for real
capture/input.

```bash
pip install open-compute[mcp,local,uia]
open-compute-mcp          # stdio server (console script)
```

**Tools:** `capture` · `do` (single or batch canonical actions) · `tree` ·
`click_name` · `invoke` (UIA semantic targeting) · `watch_dir` · `push_status` ·
`rec_replay`. Coordinates are normalized 0..1.

**Safety.** `OC_SAFETY_MODE` is an operator **ceiling** (`confirm` default ·
`read_only` · `allow_all`); a per-call `mode` can only *tighten* it, never loosen it,
so a prompt-injected agent cannot escape a `read_only`/`confirm` server via
`mode="allow_all"`. Because stdio MCP has no server→client confirm callback,
`confirm`/`read_only` return a `needs_confirmation`/`deny` result **without acting**.
For interactive use, run the server with `OC_SAFETY_MODE=allow_all` **in an isolated
VM** and let the client's tool-permission dialog be the human-in-the-loop. Optional
`OC_DENY` (comma-separated action types) is a hard deny list.

Client config (via `uvx`, no manual install):

```json
{ "mcpServers": { "open-compute": {
  "command": "uvx",
  "args": ["--from", "open-compute[mcp,local,uia] @ git+https://github.com/ellmos-ai/open-compute.git", "open-compute-mcp"] } } }
```

The snippet above starts in the safe `confirm` ceiling — the server *reports*
actions but does not perform them. To let it act, add
`"env": {"OC_SAFETY_MODE": "allow_all"}` (isolated VM), gated by the client dialog.
An npm launcher (`npx open-compute-mcp`) is also published for parity with Node MCP
servers. The MCP server is the ideal shape for short, inline tasks; for long,
context-heavy runs, still delegate to a self-spawned subagent (see the usage
pattern below) and call these tools inside it.

---

## Backend matrix

| Backend | SDK | Tool / model | Coordinates | Status |
|---|---|---|---|---|
| `mock` | none | scripted, offline | synthetic | Fully implemented (**default backend**) |
| `claude` | `anthropic` (lazy) | `computer` tool `computer_20251124`, beta header `computer-use-2025-11-24`, default model `claude-opus-4-8` | global pixels; host executes | Implemented; tested via injected client |
| `openai` | `openai` (lazy) | computer-use, model `computer-use-preview` *(configurable, `[UNSICHER]`)* | pixels; host executes | Implemented; model name / request shape not fully verified |
| `local` (foreign reasoner) | none | a *different* model as reasoner — local Ollama, or agy / codex / kimi CLIs | host executes | **Separate, low-priority, optional idea** — would be a real new backend with possible capability differences. Not scheduled. |

The keyless / no-API path is **not** a backend row — it is Mode A, where the
**host model itself** reasons (inline, or in a self-spawned subagent for context
economy; see [usage pattern](#usage-pattern--inline-a-vs-self-subagent-b)).

The implemented backends (`mock` / `claude` / `openai`) share one
`ComputerBackend` Protocol and are dispatched by name from `get_backend()`
(`open_compute/backends/factory.py`) — no provider is hard-wired into the loop.
The Claude tool type / beta header pair
is configurable on the backend
(`tool_type=`, `beta_header=`) so you can target the older `computer_20250124`
/ `computer-use-2025-01-24` pair on older models.

### Executor matrix

| Executor | Requires | Platform | Status |
|---|---|---|---|
| `MockExecutor` | none | any | Fully implemented; used in tests and dry-runs |
| `LocalExecutor` | `mss` (`open-compute[local]`), optional WGC fallback (`open-compute[wgc]`) | Windows only | Implemented; `oc capture` live-tested (368 KB PNG at 1920×1080); `oc do mouse_move` live-tested |

---

## Status — what is real vs. stub

**Fully implemented and tested**

- Canonical action schema + `to_claude` / `to_openai` mappers.
- Coordinate normalize / denormalize / rescale.
- Safety policy gate (`confirm` / `allow_all` / `read_only`, deny list,
  confirmation callback, audit log).
- `Config` dataclass + JSON loader.
- Agent loop orchestrator (dry-run via mocks).
- Backend dispatch via factory + `MockBackend`; Claude backend tested with an
  injected fake client.
- **`LocalExecutor`** (Windows, `open-compute[local]`): real screenshot via mss,
  real mouse/keyboard via ctypes SendInput with VIRTUALDESK + DPI-awareness.
  Optional `open-compute[wgc]` adds a Windows.Graphics.Capture fallback for
  DirectX / hardware-composited surfaces when mss/GDI capture fails. Action
  dispatch for all action types. Live-tested: `oc capture` → PNG 368 KB
  (1920×1080); `oc do mouse_move` → cursor moved.
- **`oc` CLI** (`oc capture` / `oc do` / `oc run`): Mode A (no-key skill loop)
  and Mode B (autonomous AgentLoop with API backend) wired end-to-end.
  - v0.3: `oc capture` defaults to `_session/` (never loose in CWD/Desktop).
  - v0.3: `oc do` accepts JSON arrays (batch/macro) and `--label` for
    automatic Before|After composite screenshots.
  - v0.3: `--ensure-foreground SUBSTR` / `OC_ALWAYS_FOREGROUND` on `oc do`
    and `oc run` for automatic window activation before actions.
  - v0.3: `Config.always_foreground` field + `[compose]` optional extra (Pillow).
- **`SKILL.md`**: loop protocol for the session-agent (Mode A).
- **Multi-feed abstraction** (v0.4, `open_compute/feeds/`): `PerceptionFeed` +
  `Targeter` protocols, `ScreenshotFeed` (pixel), and a runtime feed registry
  (`available_feeds()`) with graceful capability detection.
- **`UiaWindowsFeed`** (v0.4, Windows, `open-compute[uia]`): UIA element-tree
  perception + semantic targeting. `observe()` walks the ControlView tree;
  `resolve()` does exact > prefix > contains disambiguation; `invoke()` does
  click-free activation via InvokePattern → Toggle → SelectionItem →
  LegacyIAccessible fallback. `center_norm` is the exact inverse of
  `LocalExecutor`'s virtual-desktop mapping (round-trip covered by tests,
  incl. negative multi-monitor origin). The full invoke/resolve/coordinate
  logic is unit-tested with `uiautomation` **mocked**; real-OS smoke tests
  (`oc tree`, `oc click-name --mode confirm`, `oc invoke --mode confirm`) were
  run on Windows 11 — see `CHANGELOG.md`.
- **`oc` CLI** (v0.4): `oc tree`, `oc click-name`, `oc invoke` — all routed
  through the Safety gate.
- **`DirwatchFeed`** (v0.5, `open_compute/feeds/dirwatch.py`): directory-watch
  event feed. Monitors configured paths and emits change events (created /
  modified / deleted / moved) into a rolling deque. Two backends: watchdog
  (MIT, native OS events — `open-compute[watch]`) or stdlib polling (always
  available without extras). `available()` always returns `True`.
  `oc watch-dir <path> [--for SECS] [--once]` CLI.
- **Full-res / annotated verification shot** (v0.5): `oc do --fullres` and
  `oc click-name --fullres` save an additional full-resolution after-shot
  alongside the composite. Pillow (optional) annotates the click position with
  a red circle + crosshair. JSON keys: `"fullres"` / `"fullres_annotated"`.
- **`oc capture --window SUBSTR`** (v0.5, Windows): captures only the bounding
  rect of the named window via Win32 `GetWindowRect`. Case-insensitive,
  whitespace-normalized substring match (same convention as `UiaWindowsFeed`).

- **`FeedManager`** (v0.6, `open_compute/feed_manager.py`): dosierte Push-Auto-Injektion.
  Collects available feeds, applies change-detection per cycle (State-Feeds: SHA-256 hash;
  Event-Feeds: rolling window), dispatches to an `InjectorSink`. Dosage modes per feed:
  `full` | `delta` | `notify` | `off`; runtime-adjustable via `set_dosage()`.
  `LocalFileInjector` (working default; writes to `_state/inject_queue/`).
  `BachInjectorAdapter` (stub; see `feed_manager.py` docstring for activation instructions).
  `oc push --status` / `oc push --once` CLI.
- **`LearningManager`** (v0.6, `open_compute/learning.py`): Bandit/Bayes weighting
  (`BetaPrior`), use-case profiles (JSON, warmstart via `apply_profile_to_manager()`),
  and cross-session LESSONS-LEARNED (JSONL). All state in gitignored `_state/`.

**Interface / stub (honest)**

- Browser driver and OS driver are **interfaces only** (no Playwright / CDP /
  host implementation yet).
- Perception providers other than `ScreenshotPerception` and the v0.4 UIA feed
  (Set-of-Marks, OCR, vision overlays, DOM) are **not yet implemented**.
- `BachInjectorAdapter` is a documented stub; `LocalFileInjector` is the working default sink.
- Always-on push daemon (permanent background loop) is **not yet implemented**.
- `oc rec` is a **lazy compatibility shim** for the external
  [`ellmos-ai/clirec`](https://github.com/ellmos-ai/clirec) package; install
  `clirec` only when recording/replay workflows are needed.
- The UIA feed is **Windows-only**; Linux (AT-SPI) and macOS (AXUIElement)
  accessibility feeds are **open / planned**.
- The OpenAI backend's model name and exact Responses-API request shape are
  **not fully verified** — validate against live OpenAI docs before production.
- The **self-subagent mode (b)** is a **usage pattern** (docs), not new reasoning
  code — see below. A **foreign / local reasoner** (Ollama / agy / codex / kimi)
  is a **separate, low-priority, optional** idea, not implemented.

See `TODO.md` for the full breakdown.

---

## Usage pattern — inline (a) vs. self-subagent (b)

> **Pattern, not a new backend.** Same host model, no API key — only the
> **context budget** differs. Full design in `ARCHITECTURE.md`
> ("Host-Modell-Kontext: Inline (a) vs. Selbst-Subagent (b)").

When the host model (e.g. Claude Code on a subscription) runs the no-key Mode A
loop, it can spend its context two ways — **same model, same vision, same
reasoning**:

- **(a) Inline (today's solution).** The host model runs
  `capture → decide → do → recapture` **in its own context**. Best for
  **short / simple** tasks (a few steps).
- **(b) Self-subagent (concept).** The host model spawns a subagent **of
  itself** (e.g. via a `Task`) that runs the whole loop in the **subagent's**
  context and returns only the **distilled result** ("invoice found at …").
  The **main context stays clean**; it "feels like API" but is the **same
  model** — the win is **context economy, not a reasoning/vision trade-off**.
  Best for **long / repeated / context-heavy** tasks.

**The model decides per task**, exactly like normal subagent delegation. Rough
heuristic: short → inline (a); long / repeated / context-heavy → spawn a
subagent (b).

A **persistent 24h experience-subagent** is an optional variant of (b): a
long-lived self-subagent that takes repeated jobs and reuses accumulated
experience via the existing `learning.py` (`BetaPrior` / use-case profiles /
LESSONS-LEARNED in `_state/`). Experience lives in `_state/` (persistent), not
in the volatile subagent context. (Lessons should carry decay / confidence to
avoid false lessons — a small additive change, not yet implemented.)

> A *different* model as reasoner (local Ollama, or agy / codex / kimi CLIs) is a
> **separate, low-priority, optional** idea — that would be a real new
> `ComputerBackend` with possible capability differences, and is **not** mode (b).

---

## Safety

Computer-use is powerful. The default `SafetyPolicy` mode is `confirm`: clicks,
typing, key presses, drags, and app launches are blocked unless a confirmation
callback approves them. Recommended practice (mirrors both vendors' guidance):

- Run real backends in an **isolated VM or container**, never your main desktop.
- Keep a **human in the loop**.
- Treat **on-screen content as untrusted** (prompt-injection risk).

See `SECURITY.md`.

---

## Running tests

```bash
python -X utf8 -m pytest -q
```

Tests are mock-only and require no SDK. `pip install open-compute[dev]` for
pytest.

---

## License

MIT — see [LICENSE](LICENSE).
