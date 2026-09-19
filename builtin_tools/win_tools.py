"""Window perception & focus tools for ProLight-agent.

Perception is a snapshot/diff pair:
  - win_snapshot : ONE call returns everything about the active window
                   (identity, focus/keyboard state, menu, UIA controls with ids,
                   OCR text, optional image/vision).
  - win_changes  : only what changed since a snapshot (UIA/OCR/pixels), and
                   optionally wait until a given element/text appears.

Actuation stays separate: win_focus / win_ensure_foreground / win_send_message.
"""

import asyncio
import json
import time
from typing import Optional

from loguru import logger

from lib import winapi
from lib import window_state as ws
from lib import input_backend as ib


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


_INCLUDE = {"uia", "text", "menu", "focus", "image"}


def _state_to_dict(state: ws.WindowState, vision: Optional[dict] = None) -> dict:
    d = {
        "ok": True,
        "snapshot_id": state.snapshot_id,
        "mode": state.mode,
        "hwnd": state.hwnd,
        "window": {
            k: state.window.get(k)
            for k in ("title", "class", "process", "pid", "rect",
                      "minimized", "maximized", "foreground")
        } if state.window else None,
        "focus": state.focus or None,
        "uia": {
            "coverage": state.uia_coverage,
            "accessibility_enabled": state.accessibility_enabled,
            "types": state.types,
            "controls": [c.to_dict() for c in state.controls],
        },
        "menu": [m.to_dict() for m in state.menu],
        "text": [t.to_dict() for t in state.texts],
    }
    if state.image_meta:
        d["image"] = {**state.image_meta, "path": state.image_path}
    if state.notes:
        d["notes"] = state.notes
    if vision:
        d["vision"] = vision
    return d


async def _vision_lookup(hwnd, region, query: str, structured: bool) -> Optional[dict]:
    from lib.vision_agent import get_vision_agent

    va = get_vision_agent()
    if structured:
        return await va.locate(hwnd=hwnd, rect=region, query=query)
    return await va.look(hwnd=hwnd, rect=region, query=query)


async def win_list_hwnd(
    visible_only: bool = True,
    titled_only: bool = True,
    process_filter: str = "",
) -> str:
    """List top-level windows and their HWNDs.

    Args:
        visible_only: only visible windows.
        titled_only: only windows with a non-empty title.
        process_filter: case-insensitive substring of the process name
            (e.g. "notepad") to filter by.
    """
    windows = winapi.list_windows(visible_only=visible_only, titled_only=titled_only)

    if process_filter:
        needle = process_filter.lower()
        windows = [w for w in windows if needle in (w.get("process") or "").lower()]

    compact = []
    for w in windows:
        rect = w.get("rect") or {}
        compact.append({
            "hwnd": w["hwnd"],
            "title": w["title"],
            "process": w["process"],
            "pid": w["pid"],
            "class": w["class"],
            "minimized": w["minimized"],
            "foreground": w["foreground"],
            "rect": {
                "left": rect.get("left"),
                "top": rect.get("top"),
                "width": rect.get("width"),
                "height": rect.get("height"),
            } if rect else None,
        })

    logger.debug(f"win_list_hwnd: {len(compact)} windows (filter={process_filter!r})")
    return _dump({"count": len(compact), "windows": compact})


async def win_snapshot(
    hwnd: int = None,
    region: list = None,
    monitor: int = None,
    include: list = None,
    max_controls: int = 60,
    max_text: int = 60,
    vision_query: str = "",
    structured: bool = False,
    probe: bool = False,
    lang: str = "auto",
    save: bool = False,
    label: str = "",
) -> str:
    """Snapshot the active window in ONE call (perceive once, then win_changes).

    Returns window identity, keyboard focus, menu-bar items, UI Automation
    controls and OCR text — each with a short **id** (``c7``/``t3``/``m2``) you
    can pass to actions (``win_click_control(id=...)``, ``mouse_click(id=...)``).

    Defaults to the foreground window. Give ``hwnd`` for a specific window,
    ``monitor`` or ``region`` for a screen area (no UIA). Vision is opt-in via
    ``vision_query`` (kept out of your context). Chromium apps with an empty
    accessibility tree get an automatic enable attempt (see ``uia.coverage``).

    Args:
        hwnd: window to snapshot (default: foreground).
        region: [left,top,right,bottom] screen area (screen mode).
        monitor: monitor index (0=all, 1=primary, ...) for screen mode.
        include: subset of ["uia","text","menu","focus","image"] (default all).
        max_controls: cap on UIA controls returned.
        max_text: cap on OCR text lines returned.
        vision_query: if set, also ask the vision model about the window.
        structured: with vision_query, return approximate element boxes (fractions).
        probe: actively verify keyboard delivery (injects a benign key).
        lang: OCR language ("auto" tries all installed).
        save: save the window image to data/vision/.
        label: name this snapshot so win_changes can reference it.
    """
    inc = set(include) if include else set(_INCLUDE)
    inc &= _INCLUDE
    try:
        state = await ws.capture_state(
            hwnd=int(hwnd) if hwnd is not None else None,
            region=region,
            monitor=int(monitor) if monitor is not None else None,
            include=inc,
            max_controls=int(max_controls),
            max_text=int(max_text),
            probe=bool(probe),
            lang=lang,
            save_image=bool(save),
            baseline=ws.get(label) if label else ws.get(),
        )
    except Exception as e:
        logger.exception("win_snapshot failed")
        return _dump({"ok": False, "error": str(e)})

    vision = None
    if vision_query:
        try:
            vision = await _vision_lookup(state.hwnd, region, vision_query, structured)
        except Exception as e:
            vision = {"ok": False, "error": str(e)}

    ws.store(state, label)
    logger.info(
        f"win_snapshot[{label or '-'}] {state.snapshot_id} hwnd={state.hwnd} "
        f"controls={len(state.controls)} text={len(state.texts)} cov={state.uia_coverage}"
    )
    return _dump(_state_to_dict(state, vision))


def _diff_matches(diff: dict, needle: str) -> bool:
    n = needle.lower()
    for e in diff["uia"]["added"] + diff["uia"]["changed"]:
        if n in (e.get("name") or "").lower():
            return True
    return False


async def win_changes(
    label: str = "",
    include: list = None,
    threshold: int = 12,
    vision_query: str = "",
    structured: bool = False,
    wait_for: str = "",
    timeout: float = 10.0,
    interval: float = 1.0,
    max_controls: int = None,
    max_text: int = None,
    probe: bool = False,
) -> str:
    """Return only what changed since a snapshot (act → verify loop).

    Diffs the current window against the last ``win_snapshot`` (or a labelled
    one) and returns added/removed/changed elements (with ids) plus a pixel
    diff bbox. No vision call unless ``vision_query`` is given.

    With ``wait_for`` it polls until an element/text whose name contains that
    string appears (e.g. a dialog button), then returns the diff.

    Args:
        label: snapshot to compare against (default: the most recent).
        include: subset of ["uia","text","menu","focus","image"].
        threshold: per-pixel difference threshold for the pixel diff.
        vision_query: if set, also ask the vision model what changed.
        structured: with vision_query, return approximate boxes.
        wait_for: substring to wait for before returning.
        timeout: seconds to wait (with wait_for).
        interval: poll interval in seconds.
        probe: actively verify keyboard delivery.
    """
    baseline = ws.get(label)
    if baseline is None:
        return _dump({"ok": False, "error": "no snapshot to compare — call win_snapshot first"})

    # Default to the baseline's own caps/include so the diff is apples-to-apples.
    if include:
        inc = set(include) & _INCLUDE
    else:
        inc = set(baseline.include) if baseline.include else None
    mc = int(max_controls) if max_controls is not None else baseline.max_controls
    mt = int(max_text) if max_text is not None else baseline.max_text
    deadline = time.time() + max(0.0, float(timeout))
    new = None
    diff = None
    while True:
        new = await ws.capture_state(
            hwnd=baseline.hwnd,
            region=baseline.image_meta["screen_rect"] if baseline.mode == "screen" else None,
            include=inc,
            max_controls=mc,
            max_text=mt,
            probe=bool(probe),
            baseline=baseline,
        )
        diff = ws.diff_states(baseline, new, threshold=int(threshold))
        if not wait_for or _diff_matches(diff, wait_for) or time.time() >= deadline:
            break
        await asyncio.sleep(max(0.2, float(interval)))

    ws.store(new, label)
    vision = None
    if vision_query:
        try:
            vision = await _vision_lookup(new.hwnd, None, vision_query, structured)
        except Exception as e:
            vision = {"ok": False, "error": str(e)}

    result = {
        "ok": True,
        "baseline": baseline.snapshot_id,
        "snapshot_id": new.snapshot_id,
        "waited_for": wait_for or None,
        **diff,
    }
    if vision:
        result["vision"] = vision
    logger.info(
        f"win_changes[{label or '-'}] {baseline.snapshot_id}->{new.snapshot_id} "
        f"changed={diff['changed']}"
    )
    return _dump(result)


async def win_focus(hwnd: int) -> str:
    """Bring a window to the foreground and focus it.

    Restores the window first if it is minimized. Returns both ``ok``
    (foreground) and ``keyboard_focus`` (the target thread's real keyboard
    focus, via GetGUIThreadInfo). Keyboard input only works when
    ``keyboard_focus`` is true — check it before typing.
    """
    hwnd = int(hwnd)
    result = winapi.focus_window(hwnd)
    logger.info(
        f"win_focus({hwnd}): ok={result.get('ok')} "
        f"keyboard_focus={result.get('keyboard_focus')} title={result.get('title')!r}"
    )
    return _dump(result)


async def win_ensure_foreground(
    hwnd: int, retries: int = 3, click_title: bool = False, probe: bool = True
) -> str:
    """Focus a window, verify real keyboard focus, and (optionally) probe it.

    ``keyboard_focus`` can still be false even when ``win_focus`` reports the
    window as foreground (SendInput keyboard then silently goes nowhere). With
    ``probe=true`` a benign keystroke is injected and re-checked, returning a
    ``keyboard_delivery`` verdict (``verified``/``focus_lost``/``unverified``).
    If still unfocused and ``click_title=true``, a real click on the title bar
    (non-client, safe) is used as a last resort.
    """
    hwnd = int(hwnd)
    if probe:
        result = winapi.verify_keyboard(hwnd, retries=retries)
        success = result.get("keyboard_delivery") == "verified"
    else:
        result = winapi.focus_window(hwnd, retries=retries)
        success = bool(result.get("ok")) and result.get("keyboard_focus") is not False

    if not success and click_title:
        rect = winapi.get_window_rect(hwnd)
        if rect:
            x = rect["left"] + rect["width"] // 2
            y = rect["top"] + 12
            await ib.move_to(x, y, duration=0.1)
            ib.click("left")
            result = winapi.verify_keyboard(hwnd, retries=retries) if probe else \
                winapi.focus_window(hwnd, retries=retries)
            result["nudged_with_title_click"] = True
    logger.info(
        f"win_ensure_foreground({hwnd}): ok={result.get('ok')} "
        f"keyboard_focus={result.get('keyboard_focus')} "
        f"delivery={result.get('keyboard_delivery')}"
    )
    return _dump(result)


async def win_send_message(
    hwnd: int,
    kind: str,
    x: int = 0,
    y: int = 0,
    button: str = "left",
    key: str = "",
    char: str = "",
) -> str:
    """Send a synthetic WM_ message to a window (low-level fallback).

    This does NOT move the real cursor or change focus, and works only for
    standard Win32 controls. Many apps (browsers, Electron, 1C, custom-drawn)
    ignore synthetic messages — prefer mouse_*/keybd_* tools for those.

    Args:
        hwnd: target window (or child control) handle.
        kind: "click" (uses x,y,button, CLIENT coords) | "key" (uses key) | "char" (uses char).
    """
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    try:
        if kind == "click":
            ib.wm_click(hwnd, x, y, button)
        elif kind == "key":
            ib.wm_key(hwnd, key)
        elif kind == "char":
            ib.wm_char(hwnd, char)
        else:
            return _dump({"ok": False, "error": f"Unknown kind {kind!r} (use click/key/char)"})
    except Exception as e:
        logger.error(f"win_send_message failed: {e}")
        return _dump({"ok": False, "error": str(e)})
    logger.debug(f"win_send_message kind={kind} hwnd={hwnd}")
    return _dump({"ok": True, "kind": kind, "hwnd": hwnd})


TOOL_DEFINITIONS = [
    (
        "win_list_hwnd",
        win_list_hwnd,
        "List top-level windows of running apps with their HWND, title, process, "
        "pid, class and rectangle. Use this first to find the window you need.",
        {
            "type": "object",
            "properties": {
                "visible_only": {"type": "boolean", "description": "Only visible windows (default true)"},
                "titled_only": {"type": "boolean", "description": "Only windows with a title (default true)"},
                "process_filter": {"type": "string", "description": "Case-insensitive substring of process name, e.g. 'notepad'"},
            },
            "required": [],
        },
    ),
    (
        "win_snapshot",
        win_snapshot,
        "Snapshot the active window in ONE call: identity, keyboard focus, menu, "
        "UI Automation controls and OCR text, each with an id (c7/t3/m2) you can "
        "act on. Prefer this over many separate perception calls; then use "
        "win_changes to see what changed. Defaults to the foreground window.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window to snapshot (default: foreground)"},
                "region": {"type": "array", "items": {"type": "integer"}, "description": "[left,top,right,bottom] screen area (screen mode)"},
                "monitor": {"type": "integer", "description": "Monitor index for screen mode (0=all, 1=primary)"},
                "include": {"type": "array", "items": {"type": "string"}, "description": "Subset of uia,text,menu,focus,image (default all)"},
                "max_controls": {"type": "integer", "description": "Cap on UIA controls (default 60)"},
                "max_text": {"type": "integer", "description": "Cap on OCR text lines (default 60)"},
                "vision_query": {"type": "string", "description": "Also ask the vision model about the window"},
                "structured": {"type": "boolean", "description": "With vision_query: return approximate element boxes (fractions)"},
                "probe": {"type": "boolean", "description": "Actively verify keyboard delivery (default false)"},
                "lang": {"type": "string", "description": "OCR language, or 'auto' for all installed (default auto)"},
                "save": {"type": "boolean", "description": "Save the window image to data/vision/ (default false)"},
                "label": {"type": "string", "description": "Name this snapshot for win_changes"},
            },
            "required": [],
        },
    ),
    (
        "win_changes",
        win_changes,
        "Show ONLY what changed since the last win_snapshot (or a labelled one): "
        "added/removed/changed elements (with ids) and a pixel-diff bbox. Use it "
        "after an action to verify the effect. With wait_for it polls until a "
        "named element/text appears (e.g. a dialog button).",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Snapshot to compare against (default: most recent)"},
                "include": {"type": "array", "items": {"type": "string"}, "description": "Subset of uia,text,menu,focus,image"},
                "threshold": {"type": "integer", "description": "Per-pixel difference threshold (default 12)"},
                "vision_query": {"type": "string", "description": "Also ask the vision model what changed"},
                "structured": {"type": "boolean", "description": "With vision_query: return approximate boxes"},
                "wait_for": {"type": "string", "description": "Wait until an element/text containing this appears"},
                "timeout": {"type": "number", "description": "Seconds to wait with wait_for (default 10)"},
                "interval": {"type": "number", "description": "Poll interval in seconds (default 1)"},
                "max_controls": {"type": "integer", "description": "Cap on UIA controls (default 60)"},
                "max_text": {"type": "integer", "description": "Cap on OCR text lines (default 60)"},
                "probe": {"type": "boolean", "description": "Actively verify keyboard delivery"},
            },
            "required": [],
        },
    ),
    (
        "win_focus",
        win_focus,
        "Bring a window to the foreground and focus it (restores if minimized). "
        "Call this before sending mouse/keyboard input to that window.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle to focus"},
            },
            "required": ["hwnd"],
        },
    ),
    (
        "win_ensure_foreground",
        win_ensure_foreground,
        "Focus a window and verify real keyboard delivery (injects a benign key "
        "and returns keyboard_delivery: verified/focus_lost/unverified). Prefer "
        "this over win_focus before typing when focus is unreliable.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle to focus"},
                "retries": {"type": "integer", "description": "Focus attempts (default 3)"},
                "click_title": {"type": "boolean", "description": "Nudge with a title-bar click if needed (default false)"},
                "probe": {"type": "boolean", "description": "Actively verify keyboard delivery (default true)"},
            },
            "required": ["hwnd"],
        },
    ),
    (
        "win_send_message",
        win_send_message,
        "Send a synthetic WM_ message to a window (low-level fallback; no cursor "
        "move, no focus change; standard controls only). Prefer mouse_*/keybd_*.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Target window/control handle"},
                "kind": {"type": "string", "description": "click | key | char"},
                "x": {"type": "integer", "description": "Client X for kind=click"},
                "y": {"type": "integer", "description": "Client Y for kind=click"},
                "button": {"type": "string", "description": "left | right | middle (kind=click)"},
                "key": {"type": "string", "description": "Key name (kind=key)"},
                "char": {"type": "string", "description": "Character (kind=char)"},
            },
            "required": ["hwnd", "kind"],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} window tool(s)")
