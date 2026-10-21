"""Lazy Windows adapter for Cowork; no raw UIA data or full desktop to models."""
from __future__ import annotations

import base64
from dataclasses import replace
import threading
import time
from typing import Any

from .actions import Action, ActionType
from .interaction import (InteractionContractError, ObservationRegistry, describe_window,
                          resolve_window_reference)
from .perception_filter import filter_uia_elements, resolve_filter_profile
from .preclick import (Win32WindowProbe, coordinate_frame_from_executor, execute_with_preclick)
from .cowork import digest


class DesktopBackend:
    def __init__(self):
        self._executor: Any = None
        self.registry = ObservationRegistry()
        self.tokens: dict = {}
        self.profile = replace(resolve_filter_profile(mode="act"),
                               profile_id="cowork-focused-edit-v1", text_limit=350,
                               value_policy="all-bounded")
        # Only the selected window's focused/uniquely editable control supplies
        # a value. all-bounded here preserves that one value despite root ordering.
        self.last_edit_rect: dict[str, list] = {}
        self.io = threading.RLock()

    @property
    def executor(self):
        if self._executor is None:
            import sys
            if sys.platform != "win32":
                raise RuntimeError("Desktop actions require Windows; text and MCP work without it")
            from .drivers.local import LocalExecutor
            self._executor = LocalExecutor()
        return self._executor

    def windows(self) -> list[dict]:
        from .drivers.local import _set_dpi_awareness, list_windows
        _set_dpi_awareness()
        result = []
        for raw in list_windows():
            w = describe_window(raw)
            if any(x.casefold() in w["title"].casefold()
                   for x in (*self.profile.exclude_window_title_contains, "open-compute Cowork")):
                continue
            self.tokens[w["window_token"]] = w
            result.append(w)
        return result[:100]

    def _resolve(self, target: dict) -> dict:
        return resolve_window_reference(target, self.windows(), self.tokens)

    def _semantics(self, target: dict) -> dict:
        from .feeds.uia_windows import _get_uia, _rect_to_px_tuple
        uia = _get_uia()
        frame = coordinate_frame_from_executor(self.executor)
        elements = []
        editors = []
        # UIA uses COM; every HTTP worker initializes its own apartment.
        with uia.UIAutomationInitializerInThread():
            root = uia.ControlFromHandle(target["hwnd"])
            for index, (ctrl, depth) in enumerate(uia.WalkControl(root, maxDepth=8)):
                if index >= 96:
                    break
                try:
                    # Password data is never read, even before filtering.
                    if ctrl.IsPassword:
                        continue
                    x, y, w, h = _rect_to_px_tuple(ctrl.BoundingRectangle)
                    element = {"name": ctrl.Name or "", "role": ctrl.ControlTypeName,
                                     "rect_px": [x, y, w, h], "visible": w > 0 and h > 0,
                                     "center_norm": [(x + w / 2 - frame["left"]) / frame["width"],
                                                     (y + h / 2 - frame["top"]) / frame["height"]]}
                    elements.append(element)
                    if any(role in element["role"] for role in ("Edit", "Document")):
                        editors.append((ctrl, element, bool(ctrl.HasKeyboardFocus)))
                except Exception:
                    continue
            focused = [item for item in editors if item[2]]
            remembered = [item for item in editors if item[1]["rect_px"] == self.last_edit_rect.get(target["window_token"])]
            selected = focused[:1] or remembered[:1] or (editors if len(editors) == 1 else [])
            truncated = False
            readable = False
            center = target["center"]
            if selected:
                ctrl, element, _ = selected[0]
                self.last_edit_rect[target["window_token"]] = element["rect_px"]
                # Password was excluded before any name/value/text access above.
                raw = None
                try:
                    pattern = ctrl.GetTextPattern()
                    if pattern:
                        raw = pattern.DocumentRange.GetText(351)
                except Exception:
                    pass
                if raw is None:
                    try:
                        pattern = ctrl.GetValuePattern()
                        if pattern:
                            raw = pattern.Value
                    except Exception:
                        pass
                if isinstance(raw, str):
                    readable = True
                    truncated = len(raw) > 350
                    element["value"] = raw[:350]
                center = {"x": element["center_norm"][0], "y": element["center_norm"][1]}
                elements.remove(element)
                elements.insert(0, element)
        packet = filter_uia_elements(elements, profile=self.profile,
                                   focus={"kind": "fixed-focus", "x": center["x"],
                                          "y": center["y"], "targetName": ""})
        packet["content_readable"] = readable
        packet["content_truncated"] = truncated
        while True:
            metrics = packet["metrics"]
            metrics["includedElements"] = len(packet["elements"])
            metrics["omittedElements"] = len(elements) - metrics["excludedElements"] - len(packet["elements"])
            size = len(__import__("json").dumps(packet, ensure_ascii=False, separators=(",", ":")))
            if size > self.profile.max_characters:
                if not packet["elements"]:
                    raise ValueError("focused content exceeds semantic budget")
                packet["elements"].pop()
            elif metrics["payloadCharacters"] == size:
                break
            metrics["payloadCharacters"] = size
        return packet

    def observe(self, target: dict) -> dict:
        with self.io:
            live = self._resolve(target)
            packet = self._semantics(live)
            frame = coordinate_frame_from_executor(self.executor)
            meta = self.registry.record(kind="cowork_filtered_uia", payload=packet,
                                        window=live, frame=frame)
            return {**meta, "semantics": packet}

    def validate(self, observation: dict, target: dict) -> None:
        with self.io:
            live = self._resolve(target)
            if time.time() - observation["created_at"] > 60:
                raise PermissionError("observation expired; observe again")
            try:
                meta = self.registry.peek(observation["observation_id"])
                if (digest(self._semantics(live)) != digest(observation["semantics"])
                        or meta["coordinate_frame"] != coordinate_frame_from_executor(self.executor)):
                    raise PermissionError("target state or coordinate frame changed")
            except Exception as exc:
                raise PermissionError(str(exc)) from exc

    def _foreground(self, target: dict) -> None:
        live = self._resolve(target)
        if not live.get("foreground"):
            raise PermissionError("selected target is not foreground; dispatch cancelled")

    def _activate_target(self, target: dict) -> None:
        activate_verified(target["hwnd"])

    def execute(self, action: Action, target: dict, observation: dict, cancel) -> dict:
        with self.io:
            if cancel.is_set():
                raise PermissionError("interrupted before activation")
            live = self._resolve(target)
            monitor = PhysicalInputMonitor(cancel)
            monitor.start()
            try:
                # Human approval explicitly includes this exact HWND activation.
                self._activate_target(live)
                self._foreground(target)
                self.validate(observation, target)
                self.registry.claim(observation["observation_id"], payload=observation["semantics"],
                                    window=self._resolve(target),
                                    frame=coordinate_frame_from_executor(self.executor))
                frame = coordinate_frame_from_executor(self.executor)
                if action.x is not None:
                    point = (round(frame["left"] + action.x * frame["width"]),
                             round(frame["top"] + action.y * frame["height"]))
                    actual = Win32WindowProbe().window_at_point(*point)
                    if not actual or describe_window(actual)["window_token"] != target["window_token"]:
                        raise PermissionError("target point is covered by another window")
                if cancel.is_set():
                    raise PermissionError("human input or stop before dispatch")
                transfer = None
                if action.type is ActionType.TYPE:
                    def focus_check():
                        if cancel.is_set():
                            raise InteractionContractError("interrupted", "physical input or stop during text transfer")
                        try:
                            self._foreground(target)
                        except PermissionError as exc:
                            raise InteractionContractError("focus_lost", str(exc)) from exc
                        return self._resolve(target)
                    transfer = self.executor.type_text_verified(action.text or "",
                        check_focus=focus_check, expected_window=target, chunk_chars=8)
                else:
                    execute_with_preclick(self.executor, action, expected_window=target,
                                          coordinate_frame=observation["coordinate_frame"])
                # A screenshot/SendInput return is not a verified semantic effect.
                return {"status": "uncertain", "dispatch": "executed",
                        "reason": "Eingabe ausgeführt. Bitte das Ergebnis im Zielfenster prüfen.",
                        "text_transfer": transfer}
            finally:
                monitor.stop()
                self.registry.invalidate_all()

    def release_all(self):
        # Do not initialize a desktop solely for cleanup.
        if self._executor is not None:
            self._executor.release_all()

    def task_monitor(self, cancel):
        monitor = PhysicalInputMonitor(cancel)
        monitor.start()
        return monitor

    def prepare_task(self, target, cancel):
        if cancel.is_set():
            raise PermissionError("task cancelled before target activation")
        live = self._resolve(target)
        self._activate_target(live)
        self._foreground(target)

    def lens(self, target: dict) -> dict:
        """Explicit, local-only <=400px crop. Refuse covered/non-foreground targets."""
        with self.io:
            self._foreground(target)
            live = self._resolve(target)
            rect = live["rect"]
            width, height = min(400, rect["width"]), min(400, rect["height"])
            left = rect["left"] + (rect["width"] - width) // 2
            top = rect["top"] + (rect["height"] - height) // 2
            # Mask every pixel whose top-level owner differs, including overlays.
            # Sampling corners alone would miss a small foreign window in the middle.
            from .drivers.local import list_windows
            from PIL import Image, ImageDraw
            import io
            import mss
            import mss.tools
            with mss.mss() as sct:
                shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
                image = Image.frombytes("RGB", shot.size, shot.rgb)
            draw = ImageDraw.Draw(image)
            for w in list_windows():
                if w["window_token"] == target["window_token"]:
                    break  # Z-order: only foreign windows above the target obscure it.
                r = w["rect"]
                draw.rectangle((r["left"] - left, r["top"] - top,
                                r["left"] + r["width"] - left,
                                r["top"] + r["height"] - top), fill="black")
            out = io.BytesIO()
            image.save(out, format="PNG")
            return {"width": width, "height": height, "region": [left, top, width, height],
                    "png": base64.b64encode(out.getvalue()).decode("ascii"), "local_only": True}


class PhysicalInputMonitor:
    """Short-lived Win32 hooks distinguish physical input from SendInput flags.

    Active only during an approved action; no keys, coordinates or text retained.
    Failure to install is fail-closed. Hook lifetime ends on every exit path.
    """
    def __init__(self, cancel):
        self.cancel = cancel
        self.ready = threading.Event()
        self.thread = None
        self.thread_id = 0
        self.error = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(2) or self.error:
            self.stop()
            raise PermissionError("physical input monitor unavailable")

    def _run(self):
        import ctypes
        from ctypes import wintypes as w
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.thread_id = kernel.GetCurrentThreadId()
        hook_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)
        user.SetWindowsHookExW.argtypes = [ctypes.c_int, hook_type, w.HINSTANCE, w.DWORD]
        user.SetWindowsHookExW.restype = w.HHOOK
        user.CallNextHookEx.argtypes = [w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM]
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [w.HHOOK]
        hooks = []
        callbacks = []
        try:
            for kind, offset, injected in ((13, 8, 0x10), (14, 12, 1)):
                def callback(code, wp, lp, offset=offset, injected=injected):
                    if code >= 0:
                        flags = ctypes.c_uint32.from_address(lp + offset).value
                        if not flags & injected:
                            self.cancel.set()
                    return user.CallNextHookEx(None, code, wp, lp)
                cb = hook_type(callback)
                callbacks.append(cb)
                handle = user.SetWindowsHookExW(kind, cb, None, 0)
                if not handle:
                    raise OSError(ctypes.get_last_error(), "SetWindowsHookExW")
                hooks.append(handle)
            msg = w.MSG()
            user.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            self.ready.set()
            while user.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self.error = exc
            self.cancel.set()
            self.ready.set()
        finally:
            for handle in hooks:
                user.UnhookWindowsHookEx(handle)

    def stop(self):
        if self.thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread:
            self.thread.join(timeout=2)


def activate_verified(hwnd: int, *, user32=None, kernel32=None) -> None:
    """Exact HWND activation with temporary input-queue attachment and readback.

    SetForegroundWindow's return alone is not authoritative. No synthetic Alt,
    global foreground permission or fuzzy title lookup is used. If Windows still
    refuses, the action is blocked; the human can switch targets during the grace.
    """
    import ctypes
    from ctypes import wintypes as w
    native = user32 is None
    user = user32 or ctypes.WinDLL("user32", use_last_error=True)
    kernel = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
    if native:
        user.GetForegroundWindow.restype = w.HWND
        user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        user.GetWindowThreadProcessId.restype = w.DWORD
        user.SetForegroundWindow.argtypes = [w.HWND]
        user.BringWindowToTop.argtypes = [w.HWND]
        user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        user.IsIconic.argtypes = [w.HWND]
        user.AttachThreadInput.argtypes = [w.DWORD, w.DWORD, w.BOOL]
    if int(user.GetForegroundWindow() or 0) == hwnd:
        return
    source_thread = int(kernel.GetCurrentThreadId())
    foreground = user.GetForegroundWindow()
    threads = {int(user.GetWindowThreadProcessId(foreground, None)) if foreground else 0,
               int(user.GetWindowThreadProcessId(hwnd, None))}
    attached = []
    try:
        for target_thread in sorted(threads - {0, source_thread}):
            if user.AttachThreadInput(source_thread, target_thread, True):
                attached.append(target_thread)
        if user.IsIconic(hwnd):
            user.ShowWindow(hwnd, 9)
        user.BringWindowToTop(hwnd)
        user.SetForegroundWindow(hwnd)
    finally:
        for target_thread in reversed(attached):
            user.AttachThreadInput(source_thread, target_thread, False)
    for _ in range(5):
        if int(user.GetForegroundWindow() or 0) == hwnd:
            return
        time.sleep(.05)
    raise PermissionError("Windows hat die Zielaktivierung blockiert. Im Abbruchfenster manuell zum Zielfenster wechseln und erneut annehmen.")
