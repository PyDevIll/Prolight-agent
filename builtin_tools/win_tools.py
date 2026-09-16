"""Window perception & focus tools for ProLight-agent.

Tools:
  - win_list_hwnd : enumerate top-level windows with metadata
  - win_get_info  : detailed metadata for one window
  - win_focus     : restore + bring a window to the foreground
"""

import json
from typing import Optional

from loguru import logger

from lib import winapi
from lib import input_backend as ib


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


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

    # Compact, LLM-friendly view
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


async def win_get_info(hwnd: int) -> str:
    """Get detailed metadata for a window by HWND."""
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    return _dump({"ok": True, **winapi.get_window_info(hwnd)})


async def win_focus(hwnd: int) -> str:
    """Bring a window to the foreground and focus it.

    Restores the window first if it is minimized. Returns the resulting
    foreground state so the caller can verify success.
    """
    hwnd = int(hwnd)
    result = winapi.focus_window(hwnd)
    logger.info(f"win_focus({hwnd}): ok={result.get('ok')} title={result.get('title')!r}")
    return _dump(result)


async def win_get_menu_rects(hwnd: int) -> str:
    """List a window's menu-bar items (text + screen rectangle).

    Menu bars are non-client area, so these coordinates are NOT inside the
    client rect returned by other tools. Use center_x/center_y with mouse_click
    to open a menu. (To find items inside an opened dropdown, use win_see.)
    """
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    items = winapi.get_menu_items(hwnd)
    logger.debug(f"win_get_menu_rects({hwnd}): {len(items)} items")
    return _dump({"ok": True, "count": len(items), "items": items})


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
        "win_get_info",
        win_get_info,
        "Get detailed metadata for a single window: title, class, process, pid, "
        "visibility, minimized/maximized, foreground flag and rectangle.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle from win_list_hwnd"},
            },
            "required": ["hwnd"],
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
        "win_get_menu_rects",
        win_get_menu_rects,
        "List a window's menu-bar items (text + screen rect). Use center_x/center_y "
        "with mouse_click to open a menu.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle"},
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
