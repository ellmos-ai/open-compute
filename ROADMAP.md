# open-compute — ROADMAP

> Strategische Übersicht geplanter Arbeit. Operative Checklisten in `TODO.md`,
> abgeschlossene Punkte wandern in `CHANGELOG.md`.

## clirec — externer Aufnahmekanal

`clirec` wurde aus `open_compute/clirec/` in ein eigenes Repository ausgelagert:
https://github.com/ellmos-ai/clirec

`open-compute` behält nur einen lazy geladenen `oc rec`-Kompatibilitäts-Shim und
die alte `open_compute.clirec.*`-Import-Namespace als Wrapper. Operative Arbeit
am Recorder, Ringpuffer-Daemon, Pause-Hotkey, Frame-Capture und Portierung lebt
ab jetzt im `clirec`-Repo.
