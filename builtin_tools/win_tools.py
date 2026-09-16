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
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} window tool(s)")
