"""Keyboard actuation and clipboard tools for ProLight-agent.

Text is injected as Unicode (layout-independent), so Cyrillic and other
scripts are typed correctly. Keys are injected via SendInput and require the
target window to be foreground (call win_focus first).
"""

import json

import pyperclip
from loguru import logger

from lib import input_backend as ib


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


async def keybd_type(text: str, interval: float = 0.01) -> str:
    """Type text into the focused control.

    Uses Unicode injection, so it works regardless of keyboard layout
    (including Cyrillic). Newlines become Enter, tabs become Tab.

    Args:
        text: the text to type.
        interval: delay between characters in seconds (helps slow apps).
    """
    count = ib.type_text(text, interval=interval)
    logger.info(f"keybd_type: {count} chars")
    return _dump({"ok": True, "characters": count})


async def keybd_stroke(key: str) -> str:
    """Press and release a single key (e.g. 'enter', 'tab', 'f5', 'a', 'left')."""
    vk = ib.key_stroke(key)
    logger.debug(f"keybd_stroke {key!r} (vk=0x{vk:02X})")
    return _dump({"ok": True, "key": key, "vk": vk})


async def keybd_hotkey(combo: str) -> str:
    """Press a key combination, e.g. 'ctrl+s', 'ctrl+shift+esc', 'alt+f4'."""
    vks = ib.hotkey(combo)
    logger.info(f"keybd_hotkey {combo!r} -> vks {[hex(v) for v in vks]}")
    return _dump({"ok": True, "combo": combo, "vks": vks})


async def keybd_down(key: str) -> str:
    """Press and hold a key (combine with keybd_up for sustained holds)."""
    vk = ib.key_down(key)
    return _dump({"ok": True, "key": key, "vk": vk, "state": "down"})


async def keybd_up(key: str) -> str:
    """Release a key previously pressed with keybd_down."""
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
            "properties": {"key": {"type": "string", "description": "Key name"}},
            "required": ["key"],
        },
    ),
    (
        "keybd_hotkey",
        keybd_hotkey,
        "Press a key combination, e.g. 'ctrl+s', 'ctrl+shift+esc', 'alt+f4', 'ctrl+a'.",
        {
            "type": "object",
            "properties": {"combo": {"type": "string", "description": "Combination joined with '+'"}},
            "required": ["combo"],
        },
    ),
    (
        "keybd_down",
        keybd_down,
        "Press and hold a key (use keybd_up to release).",
        {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Key name"}},
            "required": ["key"],
        },
    ),
    (
        "keybd_up",
        keybd_up,
        "Release a key previously pressed with keybd_down.",
        {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Key name"}},
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
