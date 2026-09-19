"""Keyboard actuation and clipboard tools for ProLight-agent.

Text is injected as Unicode (layout-independent), so Cyrillic and other
scripts are typed correctly. Keys are injected via SendInput and require the
target window to be foreground (call win_focus first).
"""

import json
from typing import Optional

import pyperclip
from loguru import logger

from lib import input_backend as ib
from lib import winapi


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _focus_guard(hwnd: Optional[int]) -> Optional[dict]:
    """If ``hwnd`` is given, ensure it has real keyboard focus before sending.

    Returns an error dict (and the caller must NOT send) when the window is not
    foreground or its thread has no keyboard focus — otherwise SendInput
    silently delivers nothing. Returns None when it is safe to proceed.
    """
    if hwnd is None:
        return None
    res = winapi.focus_window(int(hwnd))
    if not res.get("ok"):
        return {
            "ok": False,
            "error": "target window is not foreground — keyboard not sent",
            "focus": res,
        }
    if res.get("keyboard_focus") is False:
        return {
            "ok": False,
            "error": "target window has no keyboard focus — keyboard not sent "
                     "(try win_ensure_foreground with click_title=true)",
            "focus": res,
        }
    return None


async def keybd_type(text: str, interval: float = 0.01, hwnd: Optional[int] = None) -> str:
    """Type text into the focused control.

    Uses Unicode injection, so it works regardless of keyboard layout
    (including Cyrillic). Newlines become Enter, tabs become Tab.

    Args:
        text: the text to type.
        interval: delay between characters in seconds (helps slow apps).
        hwnd: optional target window; if given, it is focused and verified
            first, so a focus loss fails loudly instead of silently.
    """
    guard = _focus_guard(hwnd)
    if guard is not None:
        logger.warning(f"keybd_type refused: {guard['error']}")
        return _dump(guard)
    count = ib.type_text(text, interval=interval)
    logger.info(f"keybd_type: {count} chars")
    return _dump({"ok": True, "characters": count})


async def keybd_stroke(key: str, hwnd: Optional[int] = None) -> str:
    """Press and release a single key (e.g. 'enter', 'tab', 'f5', 'a', 'left')."""
    guard = _focus_guard(hwnd)
    if guard is not None:
        logger.warning(f"keybd_stroke refused: {guard['error']}")
        return _dump(guard)
    vk = ib.key_stroke(key)
    logger.debug(f"keybd_stroke {key!r} (vk=0x{vk:02X})")
    return _dump({"ok": True, "key": key, "vk": vk})


async def keybd_hotkey(combo: str, hwnd: Optional[int] = None) -> str:
    """Press a key combination, e.g. 'ctrl+s', 'ctrl+shift+esc', 'alt+f4'."""
    guard = _focus_guard(hwnd)
    if guard is not None:
        logger.warning(f"keybd_hotkey refused: {guard['error']}")
        return _dump(guard)
    vks = ib.hotkey(combo)
    logger.info(f"keybd_hotkey {combo!r} -> vks {[hex(v) for v in vks]}")
    return _dump({"ok": True, "combo": combo, "vks": vks})


async def keybd_down(key: str, hwnd: Optional[int] = None) -> str:
    """Press and hold a key (combine with keybd_up for sustained holds)."""
    guard = _focus_guard(hwnd)
    if guard is not None:
        return _dump(guard)
    vk = ib.key_down(key)
    return _dump({"ok": True, "key": key, "vk": vk, "state": "down"})


async def keybd_up(key: str, hwnd: Optional[int] = None) -> str:
    """Release a key previously pressed with keybd_down."""
    guard = _focus_guard(hwnd)
    if guard is not None:
        return _dump(guard)
    vk = ib.key_up(key)
    return _dump({"ok": True, "key": key, "vk": vk, "state": "up"})


async def clipboard_set(text: str) -> str:
    """Put text on the clipboard (use with Ctrl+V to paste exact values)."""
    pyperclip.copy(text)
    logger.debug(f"clipboard_set: {len(text)} chars")
    return _dump({"ok": True, "characters": len(text)})


async def clipboard_get() -> str:
    """Read the current clipboard text."""
    try:
        text = pyperclip.paste()
    except Exception as e:
        return _dump({"ok": False, "error": str(e)})
    return _dump({"ok": True, "text": text})


TOOL_DEFINITIONS = [
    (
        "keybd_type",
        keybd_type,
        "Type text into the focused control using Unicode injection "
        "(layout-independent, supports Cyrillic). Newline=Enter, Tab=Tab.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
                "interval": {"type": "number", "description": "Delay between chars in seconds (default 0.01)"},
                "hwnd": {"type": "integer", "description": "Optional target window; focus is verified before sending"},
            },
            "required": ["text"],
        },
    ),
    (
        "keybd_stroke",
        keybd_stroke,
        "Press and release a single key by name (enter, tab, esc, f5, a, left, delete, ...).",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name"},
                "hwnd": {"type": "integer", "description": "Optional target window; focus is verified before sending"},
            },
            "required": ["key"],
        },
    ),
    (
        "keybd_hotkey",
        keybd_hotkey,
        "Press a key combination, e.g. 'ctrl+s', 'ctrl+shift+esc', 'alt+f4', 'ctrl+a'.",
        {
            "type": "object",
            "properties": {
                "combo": {"type": "string", "description": "Combination joined with '+'"},
                "hwnd": {"type": "integer", "description": "Optional target window; focus is verified before sending"},
            },
            "required": ["combo"],
        },
    ),
    (
        "keybd_down",
        keybd_down,
        "Press and hold a key (use keybd_up to release).",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name"},
                "hwnd": {"type": "integer", "description": "Optional target window; focus is verified before sending"},
            },
            "required": ["key"],
        },
    ),
    (
        "keybd_up",
        keybd_up,
        "Release a key previously pressed with keybd_down.",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name"},
                "hwnd": {"type": "integer", "description": "Optional target window; focus is verified before sending"},
            },
            "required": ["key"],
        },
    ),
    (
        "clipboard_set",
        clipboard_set,
        "Put text on the clipboard (then paste with keybd_hotkey 'ctrl+v').",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to copy"}},
            "required": ["text"],
        },
    ),
    (
        "clipboard_get",
        clipboard_get,
        "Read the current clipboard text.",
        {"type": "object", "properties": {}, "required": []},
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} keyboard tool(s)")
