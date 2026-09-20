# Third-Party Licenses & Transparency Notice

> **Project:** `ellmos-ai/open-compute`
> **Audited:** 2026-09-14
> **Re-Audited (Pfad A Turnus-Hygiene):** 2026-09-20
> **Repository License:** [MIT License](LICENSE)  
> **Architecture & Privacy:** 100% Local-First, Zero-Egress by default, Unprivileged User-Mode (`RunAsInvoker`)

---

## Executive Summary & Compliance Assurance

`open-compute` is designed with an uncompromising architectural principle: **the core agent loop has zero mandatory runtime dependencies** and runs entirely within the Python standard library. Optional extras (such as cloud vendor API clients, local screen grabbers, accessibility tree walkers, and browser automation drivers) are dynamically and lazily loaded only when explicitly configured by the operator.

All direct, optional, and development dependencies used in `open-compute` are licensed under strictly **permissive open-source licenses** (MIT, Apache 2.0, BSD-3-Clause, PSFL, HPND). There are **zero copyleft or AGPL-style viral dependencies**, ensuring maximum freedom for enterprise integration, commercial deployments, academic research, and proprietary extensions.

Furthermore, `open-compute` adheres to a strict **Zero-Egress Privacy Boundary**:
1. When using the default `mock` backend or local-first rule sets, **no network requests or telemetry data are transmitted**.
2. API keys for cloud backends (`anthropic`, `openai`) are ingested strictly from memory or environment variables and are **never serialized to disk, state caches, or logs**.
3. All local execution operates in unprivileged user mode (`RunAsInvoker`) without requiring administrative elevation or root permissions.

---

## Runtime & Optional Dependency Matrix

| Package | Role / Functional Scope | License | Project Repository / Source |
|:---|:---|:---|:---|
| **Python Standard Library** | Core agent loop, canonical actions, coordinate normalization, safety gating, CLI | [PSFL-2.0](https://docs.python.org/3/license.html) | [python/cpython](https://github.com/python/cpython) |
| **anthropic** *(optional: `claude`)* | Anthropic Messages API client for Claude `computer_20251124` tool calling | [MIT](https://github.com/anthropics/anthropic-sdk-python/blob/main/LICENSE) | [anthropics/anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python) |
| **openai** *(optional: `openai`)* | OpenAI Computer-Use API client for CUA computer calls | [Apache-2.0](https://github.com/openai/openai-python/blob/main/LICENSE) | [openai/openai-python](https://github.com/openai/openai-python) |
| **playwright** *(optional: `browser`)* | Headless/headed browser driver for web DOM observation and interaction | [Apache-2.0](https://github.com/microsoft/playwright-python/blob/main/LICENSE) | [microsoft/playwright-python](https://github.com/microsoft/playwright-python) |
| **mss** *(optional: `local`)* | Ultra-fast cross-platform screenshot grabber using native OS APIs | [MIT](https://github.com/BoboTiG/python-mss/blob/master/LICENSE.txt) | [BoboTiG/python-mss](https://github.com/BoboTiG/python-mss) |
| **Pillow** *(optional: `compose`)* | Image manipulation, Before/After side-by-side composite generation, and annotations | [HPND](https://github.com/python-pillow/Pillow/blob/main/LICENSE) | [python-pillow/Pillow](https://github.com/python-pillow/Pillow) |
| **uiautomation** *(optional: `uia`)* | Microsoft UI Automation (UIA) tree extraction for Windows semantic scoping | [Apache-2.0](https://github.com/yann740/uiautomation/blob/master/LICENSE) | [yann740/uiautomation](https://github.com/yann740/uiautomation) |
| **windows-capture** *(optional: `wgc`)* | DirectX Windows Graphics Capture (WGC) for hardware-accelerated desktop surfaces | [MIT](https://github.com/Nathan-Melaku/windows-capture-python/blob/main/LICENSE) | [Nathan-Melaku/windows-capture-python](https://github.com/Nathan-Melaku/windows-capture-python) |
| **watchdog** *(optional: `watch`)* | Filesystem event monitoring for directory watch and file change triggers | [Apache-2.0](https://github.com/gorakhargosh/watchdog/blob/master/LICENSE) | [gorakhargosh/watchdog](https://github.com/gorakhargosh/watchdog) |
| **clirec** *(optional: `clirec`)* | Session recording and playback adapter for command-line workflows | [MIT / Apache-2.0](https://github.com/ellmos-ai/clirec) | [ellmos-ai/clirec](https://github.com/ellmos-ai/clirec) |
| **mcp** *(optional: `mcp`)* | Model Context Protocol Python SDK for tool exposure (`open-compute-mcp`) | [MIT](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) | [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) |

---

## Development & Quality Assurance Tooling

| Package | Usage & Purpose | License | Source / Upstream |
|:---|:---|:---|:---|
| **pytest** | Automated test runner, contract test suites, mocking fixtures | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) | [pytest-dev/pytest](https://github.com/pytest-dev/pytest) |
| **ruff** | High-performance Python linter and code style enforcement | [MIT / Apache-2.0](https://github.com/astral-sh/ruff/blob/main/LICENSE-MIT) | [astral-sh/ruff](https://github.com/astral-sh/ruff) |
| **setuptools** | Package build backend (PEP 517 / PEP 621 compliant) | [MIT](https://github.com/pypa/setuptools/blob/main/LICENSE) | [pypa/setuptools](https://github.com/pypa/setuptools) |

---

## Full License Texts (Excerpts & Notices)

### 1. Python Software Foundation License Version 2 (PSFL-2.0)
Python standard library modules (e.g. `ctypes`, `subprocess`, `json`, `dataclasses`, `tkinter`, `unittest`) are used under the PSF License Agreement.
Copyright (c) 2001-2026 Python Software Foundation. All rights reserved.

### 2. MIT License (MIT)
Used by `anthropic`, `mss`, `windows-capture`, `mcp`, `clirec`, `pytest`, `ruff`, and `setuptools`.
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

### 3. Apache License, Version 2.0 (Apache-2.0)
Used by `openai`, `playwright`, `uiautomation`, `watchdog`, and `ruff`.
> Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at `http://www.apache.org/licenses/LICENSE-2.0`.
> Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

### 4. Historical Permission Notice and Disclaimer (HPND / Pillow License)
The Pillow library is licensed under the Historical Permission Notice and Disclaimer:
> Copyright (c) 1997-2011 by Secret Labs AB  
> Copyright (c) 1995-2011 by Fredrik Lundh  
> Copyright (c) 2010-2026 by Jeffrey A. Clark (Alex) and contributors.  
> Permission to use, copy, modify, and distribute this software and its documentation for any purpose and without fee is hereby granted, provided that the above copyright notice appear in all copies and that both that copyright notice and this permission notice appear in supporting documentation.

---

## Runtime & Governance Invariants Confirmation

In addition to copyright and license compliance, `open-compute` guarantees adherence to 10 core architectural and runtime invariants verified continuously by automated contract testing:

1. `INV-MOD-01` (Model-Agnostic Core): One unified loop orchestration interface supporting Claude, OpenAI CUA, and deterministic offline mock execution without provider lock-in.
2. `INV-DEP-02` (Zero Mandatory Runtime Dependencies): The core agent loop, canonical actions, and coordinates require solely the standard Python library; all vendor SDKs are optional lazy extras.
3. `INV-CRD-03` (Normalized Coordinates 0..1): All model coordinate perceptions and actions operate within the invariant float range `[0.0, 1.0]`, with centralized DPI and multi-monitor rescaling.
4. `INV-GRC-04` (Mandatory Pre-Action Grace Window): Unconditional 4-second delay before the first mutating GUI action, providing operators with emergency-abort oversight.
5. `INV-SAF-05` (Fail-Closed Safety Gate): Three-tier policy gate (`confirm`, `allow_all`, `read_only`) intercepting every GUI action before OS dispatch.
6. `INV-USR-06` (Unprivileged User Mode): Full functionality guaranteed in non-elevated user context (`RunAsInvoker`) without administrative rights.
7. `INV-EGR-07` (Local-First & Zero Egress): Absolute zero telemetry, external network requests, or analytics when operating in offline/mock mode.
8. `INV-SEC-08` (Zero Secret Persistence): API keys are maintained strictly in volatile runtime memory and never saved to disk, logs, or state caches.
9. `INV-SCP-09` (Profile-Filtered Perception): Strict token-budgeted GUI scoping and background-window blanking prevents model context overflow and uncaptured exposure.
10. `INV-SLA-10` (Contractual Security SLA): 48-hour response time and 5-business-day triage commitment for all reported security vulnerabilities.

---

## Contact & Governance

For inquiries regarding licensing, supply-chain compliance, or security governance:
- **Security & Vulnerabilities:** [security@ellmos.ai](mailto:security@ellmos.ai) | [security@open-bricks.org](mailto:security@open-bricks.org)
- **Maintainer & Lead:** Lukas Geiger ([lukas@open-bricks.org](mailto:lukas@open-bricks.org))
- **Umbrella Organization:** [open-bricks](https://github.com/open-bricks)
