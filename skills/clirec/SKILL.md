---
name: clirec
description: Kompatibilitätsverweis: clirec ist aus open-compute in ein eigenes Repo/Paket ausgelagert. Nutze das externe clirec-Paket; open-compute bietet nur noch den lazy geladenen oc-rec-Shim.
---

# clirec — ausgelagert

`clirec` lebt jetzt als eigenständiges Repository/Paket:

https://github.com/ellmos-ai/clirec

In `open-compute` bleibt `oc rec ...` als Kompatibilitäts-Shim. Der Shim lädt
`clirec` erst bei Nutzung. Bis zur Paketveröffentlichung:

```bash
pip install git+https://github.com/ellmos-ai/clirec.git
```

Neue Aufnahmen, Replay-Regeln, Ringpuffer-Daemon und Pause-Hotkey gehören in das
externe `clirec`-Repo, nicht mehr in `open-compute`.
