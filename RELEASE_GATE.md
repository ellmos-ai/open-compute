# Release Gate: open-compute

## Status

```
+------------------------------------------+
|                                          |
|          STATUS: UNLOCKED                |
|                                          |
+------------------------------------------+
```

> **UNLOCKED** = Repository may be set to public.

---

## Gating Rule

**This repository must not be changed to public visibility unless the status above reads UNLOCKED.**

---

## Checklist

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 1 | `.gitignore` with minimum entries | PASS | `*.pyc`, `*.db`, `.idea/`, `.vscode/`, `data/`, `_session/`, `_reports/`, `_state/` |
| 2 | `README.md` in English | PASS | EN + DE (README.md + README_de.md) |
| 3 | `LICENSE` (MIT) present | PASS | MIT, "The open-compute authors" |
| 4 | No `.db` files tracked | PASS | |
| 5 | No `.env` files tracked | PASS | |
| 6 | No secrets in tracked files | PASS | |
| 7 | No hardcoded personal paths | PASS | |
| 8 | No PII patterns | PASS | |
| 9 | No BACH-internal documents | PASS | BACH transport referenced only as documented stub in CHANGELOG |
| 10 | `TODO.md` with STATUS table | PASS | |

---

## Gate Check Execution

```
Date:       2026-06-20
Script:     .MODULES/_scripts/final_gate_check.py
Command:    PYTHONIOENCODING=utf-8 python final_gate_check.py --repo-path <open-compute>
Exit Code:  0
Output:     10 PASS, 0 FAIL, 0 WARN — READY FOR PUBLIC RELEASE
```

### Additional checks performed

- `git ls-files` verified: no `_session/`, `_reports/`, `_state/`, `build/`, `*.egg-info/`, screenshots
- Leak-grep over tracked set (patterns: personal info, API keys, tokens): 0 matches
- `python -X utf8 -m pytest -q`: 354 passed, 1 skipped
- `__version__` = 0.6.0, `pyproject.toml` version = 0.6.0 (consistent)

---

## Sign-Off

| Field | Value |
|-------|-------|
| **Responsible** | The open-compute authors |
| **Review Date** | 2026-06-20 |
| **Decision** | UNLOCKED |
| **Remarks** | Alpha release v0.6.0. Known stubs: BachInjectorAdapter, browser driver, Linux/macOS UIA feeds, permanent push daemon. OpenAI backend model name unverified. |

---

*Template version: 1.0 | Source: MODULES/_templates/RELEASE_GATE_TEMPLATE.md*
