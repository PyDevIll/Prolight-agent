"""Mouse actuation tools for ProLight-agent.

All movement is visible to the user. Clicks/wheel are injected via SendInput,
so the target window must be foreground (call win_focus first).
"""

import json

from loguru import logger

from lib import input_backend as ib
from lib import window_state


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


async def mouse_get_pos() -> str:
    """Return the current cursor position."""
    return _dump({"ok": True, **ib.get_cursor_pos()})


async def mouse_moveto(x: int, y: int, duration: float = 0.25) -> str:
    """Move the mouse pointer to (x, y) along a smooth, visible path.

    Args:
        x, y: absolute screen coordinates (physical pixels).
        duration: seconds for the movement (0 = instant).
    """
    pos = await ib.move_to(x, y, duration=duration)
    logger.debug(f"mouse_moveto -> {pos}")
    return _dump({"ok": True, "x": pos["x"], "y": pos["y"]})


async def mouse_click(
    x: int = None,
    y: int = None,
    id: str = "",
    button: str = "left",
    clicks: int = 1,
    duration: float = 0.2,
) -> str:
    """Click a mouse button, optionally moving to a target first.

    Args:
        x, y: target coordinates. If omitted, clicks at the current position.
        id: element id from win_snapshot (clicks its centre; overrides x/y).
        button: left | right | middle | x1 | x2.
        clicks: 1 = single, 2 = double.
        duration: movement time when a target is given.
    """
    if id:
        el = window_state.resolve_id(id)
        if el is None:
            return _dump({"ok": False, "error": f"unknown element id {id!r} — call win_snapshot first"})
        center = el.center()
        if not center:
            return _dump({"ok": False, "error": f"element {id} has no rectangle", "element": el.to_dict()})
        x, y = center
    if x is not None and y is not None:
        await ib.move_to(x, y, duration=duration)
    ib.click(button=button, clicks=clicks)
    pos = ib.get_cursor_pos()
    logger.info(f"mouse_click button={button} clicks={clicks} at ({pos['x']},{pos['y']})")
    return _dump({"ok": True, "button": button, "clicks": clicks, **pos})


async def mouse_down(button: str = "left") -> str:
    """Press and hold a mouse button."""
    ib.mouse_down(button)
    return _dump({"ok": True, "button": button, "state": "down"})


async def mouse_up(button: str = "left") -> str:
    """Release a mouse button."""
    ib.mouse_up(button)
    return _dump({"ok": True, "button": button, "state": "up"})


async def mouse_wheel(amount: int, horizontal: bool = False) -> str:
    """Scroll the mouse wheel.

    Args:
        amount: number of notches; positive = up (or right if horizontal).
        horizontal: scroll horizontally instead of vertically.
    """
    ib.wheel(amount, horizontal=horizontal)
    logger.debug(f"mouse_wheel amount={amount} horizontal={horizontal}")
    return _dump({"ok": True, "amount": amount, "horizontal": horizontal})


async def mouse_drag(
    x1: int, y1: int, x2: int, y2: int, button: str = "left", duration: float = 0.4
) -> str:
    """Drag from (x1, y1) to (x2, y2) with the given button held."""
    await ib.move_to(x1, y1, duration=0.2)
    ib.mouse_down(button)
    await ib.move_to(x2, y2, duration=duration)
    ib.mouse_up(button)
    logger.info(f"mouse_drag {button}: ({x1},{y1}) -> ({x2},{y2})")
    return _dump({"ok": True, "from": {"x": x1, "y": y1}, "to": {"x": x2, "y": y2}, "button": button})


TOOL_DEFINITIONS = [
    (
        "mouse_get_pos",
        mouse_get_pos,
        "Return the current mouse cursor position (x, y).",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "mouse_moveto",
        mouse_moveto,
        "Move the mouse pointer to (x, y) along a smooth, visible path.",
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Target X (physical pixels)"},
                "y": {"type": "integer", "description": "Target Y (physical pixels)"},
                "duration": {"type": "number", "description": "Seconds for the move (default 0.25)"},
            },
            "required": ["x", "y"],
        },
    ),
    (
        "mouse_click",
        mouse_click,
        "Click a mouse button, optionally moving to (x, y) or an element id "
        "first. Focus the target window with win_focus before clicking.",
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Target X (omit to click in place)"},
                "y": {"type": "integer", "description": "Target Y (omit to click in place)"},
                "id": {"type": "string", "description": "Element id from win_snapshot (clicks its centre)"},
                "button": {"type": "string", "description": "left | right | middle | x1 | x2 (default left)"},
                "clicks": {"type": "integer", "description": "1 = single, 2 = double (default 1)"},
                "duration": {"type": "number", "description": "Movement time when x/y given (default 0.2)"},
            },
            "required": [],
        },
    ),
    (
        "mouse_down",
        mouse_down,
        "Press and hold a mouse button (for drags or held selections).",
        {
            "type": "object",
            "properties": {"button": {"type": "string", "description": "left | right | middle"}},
            "required": [],
        },
    ),
    (
        "mouse_up",
        mouse_up,
        "Release a mouse button previously pressed with mouse_down.",
        {
            "type": "object",
            "properties": {"button": {"type": "string", "description": "left | right | middle"}},
            "required": [],
        },
    ),
    (
        "mouse_wheel",
        mouse_wheel,
        "Scroll the mouse wheel up/down (or left/right).",
        {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Notches; positive = up/right"},
                "horizontal": {"type": "boolean", "description": "Scroll horizontally (default false)"},
            },
            "required": ["amount"],
        },
    ),
    (
        "mouse_drag",
        mouse_drag,
        "Drag from (x1, y1) to (x2, y2) holding a mouse button.",
        {
            "type": "object",
            "properties": {
                "x1": {"type": "integer"}, "y1": {"type": "integer"},
                "x2": {"type": "integer"}, "y2": {"type": "integer"},
                "button": {"type": "string", "description": "left | right | middle (default left)"},
                "duration": {"type": "number", "description": "Drag duration in seconds (default 0.4)"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} mouse tool(s)")
