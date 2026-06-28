"""Read/write/validate the human-readable .clirec recording format.

A .clirec file is plain text: a small header block of `key: value` lines
(with an optional `goal:` and `params:` block), then a `--- steps ---`
marker, then one step per logical line group. Screenshots live beside the
file in `<name>.clirec.frames/` and are referenced by filename only.

Pure standard library.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field, replace

VERSION = 1
STEPS_MARKER = "--- steps ---"


@dataclass
class Step:
    index: int
    t: float
    action: str
    x: int | None = None
    y: int | None = None
    end_x: int | None = None
    end_y: int | None = None
    btn: str | None = None
    text: str | None = None
    keys: str | None = None
    scroll_dir: str | None = None
    scroll_amount: int | None = None
    ui_name: str | None = None
    ui_window: str | None = None
    ui_role: str | None = None
    frame: str | None = None


@dataclass
class Recording:
    title: str
    created: str
    host: str
    resolution: str
    goal: str = ""
    params: list[dict] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)


def _q(s: str) -> str:
    """Quote a value that may contain spaces/specials for the kv step line."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fmt_step(s: Step) -> str:
    head = f"[{s.index:03d}] t={s.t:.2f}   {s.action}"
    parts: list[str] = []
    if s.x is not None:
        parts.append(f"x={s.x} y={s.y}")
    if s.end_x is not None:
        parts.append(f"end_x={s.end_x} end_y={s.end_y}")
    if s.btn:
        parts.append(f"btn={s.btn}")
    if s.text is not None:
        parts.append(f"text={_q(s.text)}")
    if s.keys:
        parts.append(f"keys={_q(s.keys)}")
    if s.scroll_dir:
        parts.append(f"scroll_dir={s.scroll_dir} scroll_amount={s.scroll_amount}")
    line1 = head + ("      " + "  ".join(parts) if parts else "")
    meta: list[str] = []
    if s.ui_name is not None:
        meta.append(f"ui={_q(s.ui_name)}")
    if s.ui_window is not None:
        meta.append(f"window={_q(s.ui_window)}")
    if s.ui_role is not None:
        meta.append(f"role={s.ui_role}")
    if s.frame:
        meta.append(f"frame={s.frame}")
    if meta:
        return line1 + "\n      " + " ".join(meta)
    return line1


def dumps(rec: Recording) -> str:
    out: list[str] = [f"# clirec-version: {VERSION}"]
    out.append(f"title: {rec.title}")
    out.append(f"created: {rec.created}")
    out.append(f"host: {rec.host}")
    out.append(f"resolution: {rec.resolution}")
    if rec.goal:
        out.append("goal: |")
        for ln in rec.goal.splitlines():
            out.append(f"  {ln}")
    if rec.params:
        out.append("params:")
        for p in rec.params:
            out.append(f"  - name: {p.get('name','')}")
            if "desc" in p:
                out.append(f"    desc: {p['desc']}")
            out.append(f"    default: {p.get('default','')}")
    out.append("")
    out.append(STEPS_MARKER)
    for s in rec.steps:
        out.append(_fmt_step(s))
    return "\n".join(out) + "\n"


_KV = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def _unq(v: str) -> str:
    if v.startswith('"') and v.endswith('"'):
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def _parse_step(line1: str, line2: str | None) -> Step:
    m = re.match(r"\[(\d+)\]\s+t=([\d.]+)\s+(\w+)(.*)", line1)
    if not m:
        raise ValueError(f"bad step line: {line1!r}")
    idx, t, action, restp = int(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
    kv: dict[str, str] = {k: _unq(v) for k, v in _KV.findall(restp)}
    if line2:
        kv.update({k: _unq(v) for k, v in _KV.findall(line2)})
    return Step(
        index=idx, t=t, action=action,
        x=int(kv["x"]) if "x" in kv else None,
        y=int(kv["y"]) if "y" in kv else None,
        end_x=int(kv["end_x"]) if "end_x" in kv else None,
        end_y=int(kv["end_y"]) if "end_y" in kv else None,
        btn=kv.get("btn"),
        text=kv.get("text"),
        keys=kv.get("keys"),
        scroll_dir=kv.get("scroll_dir"),
        scroll_amount=int(kv["scroll_amount"]) if "scroll_amount" in kv else None,
        ui_name=kv.get("ui"),
        ui_window=kv.get("window"),
        ui_role=kv.get("role"),
        frame=kv.get("frame"),
    )


def loads(text: str) -> Recording:
    lines = text.splitlines()
    header: dict[str, str] = {}
    goal_lines: list[str] = []
    params: list[dict] = []
    i = 0
    in_goal = in_params = False
    cur_param: dict | None = None
    while i < len(lines) and lines[i].strip() != STEPS_MARKER:
        ln = lines[i]
        if ln.startswith("#") or ln.strip() == "":
            i += 1
            continue
        if in_goal:
            if ln.startswith("  "):
                goal_lines.append(ln[2:])
                i += 1
                continue
            in_goal = False
        if in_params and ln.startswith("  "):
            s = ln.strip()
            if s.startswith("- name:"):
                cur_param = {"name": s.split(":", 1)[1].strip()}
                params.append(cur_param)
            elif cur_param is not None and ":" in s:
                k, v = s.split(":", 1)
                cur_param[k.strip()] = v.strip()
            i += 1
            continue
        in_params = False
        if ln.strip() == "goal: |":
            in_goal = True
            i += 1
            continue
        if ln.strip() == "params:":
            in_params = True
            i += 1
            continue
        if ":" in ln:
            k, v = ln.split(":", 1)
            header[k.strip()] = v.strip()
        i += 1
    # steps
    steps: list[Step] = []
    i += 1  # skip marker
    while i < len(lines):
        l1 = lines[i]
        if not l1.strip():
            i += 1
            continue
        l2 = None
        if i + 1 < len(lines) and lines[i + 1].startswith("      ") and not re.match(r"\s*\[\d+\]", lines[i + 1]):
            l2 = lines[i + 1]
            i += 1
        steps.append(_parse_step(l1.strip() if not l1.startswith("[") else l1, l2))
        i += 1
    return Recording(
        title=header.get("title", ""),
        created=header.get("created", ""),
        host=header.get("host", ""),
        resolution=header.get("resolution", ""),
        goal="\n".join(goal_lines),
        params=params,
        steps=steps,
    )


def write(rec: Recording, path: str | os.PathLike) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(rec))


def read(path: str | os.PathLike) -> Recording:
    with open(path, "r", encoding="utf-8") as fh:
        return loads(fh.read())


def validate(text: str) -> list[str]:
    problems: list[str] = []
    if "title:" not in text:
        problems.append("missing required header: title")
    if STEPS_MARKER not in text:
        problems.append("missing '--- steps ---' section")
    try:
        loads(text)
    except Exception as exc:  # parse failure is a validity problem
        problems.append(f"parse error: {exc}")
    return problems


def apply_params(rec: Recording, values: dict[str, str]) -> Recording:
    new_steps = []
    for s in rec.steps:
        ns = replace(s)
        if ns.text is not None:
            for k, v in values.items():
                ns.text = ns.text.replace("${" + k + "}", v)
        new_steps.append(ns)
    return replace(rec, steps=new_steps, params=copy.deepcopy(rec.params))
