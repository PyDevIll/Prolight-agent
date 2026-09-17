"""UI Automation (UIA) control discovery & invocation tools for ProLight-agent.

These tools let the agent locate controls by name/role instead of guessing
pixel coordinates, then act on them:

  - win_enum_controls     : dump a window's control tree (bounded)
  - win_get_control_rects : flat list of controls with screen rects
  - win_find_controls     : search controls by name/type/automation id/class
  - win_wait_for          : wait until a matching control appears
  - win_click_control     : focus + click a control found by name
  - win_set_control_text  : set a control's value via the UIA ValuePattern

Discovery runs through ``lib.ui_tree`` (pywinauto UIA). Clicking still uses the
real mouse (SendInput) so the user can see it; UIA is only used to find where
the control is.
"""

import json

from loguru import logger

from lib import input_backend as ib
from lib import ui_tree
from lib import winapi


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _err(e: Exception) -> str:
    logger.error(f"uia tool error: {e!r}")
    return _dump({"ok": False, "error": f"{type(e).__name__}: {e}"})


def _criteria(name, control_type, automation_id, class_name) -> dict:
    return {
        "name": name,
        "control_type": control_type,
        "automation_id": automation_id,
        "class_name": class_name,
    }


def _compact(d: dict) -> dict:
    """Trim a control dict for use inside error/match listings."""
    return {
        "name": d.get("name"),
        "control_type": d.get("control_type"),
        "automation_id": d.get("automation_id"),
        "rect": d.get("rect"),
    }


async def win_enum_controls(
    hwnd: int,
    max_depth: int = 3,
    max_controls: int = 400,
    control_type: str = "",
) -> str:
    """Dump a window's UIA control tree (bounded by depth and count)."""
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    try:
        controls = await ui_tree.enum_controls(
            hwnd, max_depth=max_depth, max_controls=max_controls, control_type=control_type
        )
    except Exception as e:
        return _err(e)
    logger.debug(f"win_enum_controls({hwnd}): {len(controls)} controls")
    return _dump({
        "ok": True,
        "hwnd": hwnd,
        "count": len(controls),
        "max_depth": max_depth,
        "controls": controls,
    })


async def win_get_control_rects(
    hwnd: int,
    max_depth: int = 3,
    max_controls: int = 400,
) -> str:
    """List a window's controls with their screen rectangles (no tree nesting)."""
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    try:
        controls = await ui_tree.enum_controls(
            hwnd, max_depth=max_depth, max_controls=max_controls, rects_only=True
        )
    except Exception as e:
        return _err(e)
    logger.debug(f"win_get_control_rects({hwnd}): {len(controls)} controls")
    return _dump({"ok": True, "hwnd": hwnd, "count": len(controls), "controls": controls})


async def win_find_controls(
    hwnd: int,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    max_depth: int = 4,
    max_controls: int = 400,
) -> str:
    """Search a window's controls. All criteria are case-insensitive substrings.

    Prefer ``name`` (the visible label / accessible name) and/or
    ``control_type`` ("Button", "Edit", "MenuItem", ...). ``automation_id`` is
    the most stable when present.
    """
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    try:
        matches = await ui_tree.find_controls(
            hwnd, max_depth=max_depth, max_controls=max_controls,
            **_criteria(name, control_type, automation_id, class_name),
        )
    except Exception as e:
        return _err(e)
    logger.debug(f"win_find_controls({hwnd}, {name!r}/{control_type!r}): {len(matches)} match(es)")
    return _dump({
        "ok": True,
        "hwnd": hwnd,
        "count": len(matches),
        "matches": matches,
    })


async def win_wait_for(
    hwnd: int,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    timeout: float = 10.0,
    interval: float = 0.5,
) -> str:
    """Wait until a matching control exists (e.g. a dialog button), or time out."""
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    try:
        control = await ui_tree.wait_for_control(
            hwnd, timeout=timeout, interval=interval,
            **_criteria(name, control_type, automation_id, class_name),
        )
    except Exception as e:
        return _err(e)
    if control is None:
        return _dump({"ok": False, "error": "timed out waiting for control", "timeout": timeout})
    logger.info(f"win_wait_for({hwnd}): found {control.get('name')!r} {control.get('control_type')!r}")
    return _dump({"ok": True, "control": control})


async def win_click_control(
    hwnd: int,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    index: int = 0,
    button: str = "left",
    double: bool = False,
    focus: bool = True,
) -> str:
    """Click a control located via UIA (by name/type/automation id/class).

    Finds matching controls, brings the chosen one on screen if needed, focuses
    the window (unless ``focus=False``) and clicks its centre with the real
    mouse. When several controls match, pass ``index`` to choose one.
    """
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})

    crit = _criteria(name, control_type, automation_id, class_name)
    try:
        matches = await ui_tree.find_controls(hwnd, max_depth=4, **crit)
    except Exception as e:
        return _err(e)

    if not matches:
        return _dump({"ok": False, "error": "no matching control found", "criteria": crit})

    index = int(index)
    if index < 0 or index >= len(matches):
        return _dump({
            "ok": False,
            "error": f"index {index} out of range ({len(matches)} match(es))",
            "matches": [_compact(m) for m in matches[:10]],
        })

    chosen = matches[index]
    rect = chosen.get("rect")

    # If the control is virtualised/off-screen, ask UIA to scroll it into view.
    if not rect or rect.get("offscreen"):
        try:
            scrolled = await ui_tree.scroll_into_view(hwnd, max_depth=4, **crit)
            if scrolled and scrolled.get("rect"):
                chosen = scrolled
                rect = chosen["rect"]
        except Exception:
            pass

    if not rect:
        return _dump({"ok": False, "error": "control has no usable rectangle", "control": _compact(chosen)})

    focus_result = None
    if focus:
        try:
            focus_result = winapi.focus_window(hwnd)
        except Exception as e:
            focus_result = {"ok": False, "error": str(e)}

    x, y = rect["center_x"], rect["center_y"]
    await ib.move_to(x, y, duration=0.2)
    ib.click(button=button, clicks=2 if double else 1)

    logger.info(
        f"win_click_control({hwnd}): {chosen.get('name')!r} {chosen.get('control_type')!r} "
        f"at ({x},{y}) matches={len(matches)}"
    )
    return _dump({
        "ok": True,
        "control": _compact(chosen),
        "index": index,
        "match_count": len(matches),
        "clicked_at": {"x": x, "y": y},
        "button": button,
        "double": bool(double),
        "focus": focus_result,
    })


async def win_set_control_text(
    hwnd: int,
    text: str,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
) -> str:
    """Set a control's text/value directly via the UIA ValuePattern.

    Works for Edit/Document controls without typing (and without needing focus).
    Falls back to pywinauto's ``set_edit_text``.
    """
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    try:
        result = await ui_tree.set_control_text(
            hwnd, text=text, max_depth=4,
            **_criteria(name, control_type, automation_id, class_name),
        )
    except Exception as e:
        return _err(e)
    if not result.get("ok"):
        return _dump(result)
    logger.info(f"win_set_control_text({hwnd}): {result.get('method')} on {result.get('control', {}).get('name')!r}")
    return _dump({"ok": True, "method": result.get("method"), "control": _compact(result.get("control", {}))})


TOOL_DEFINITIONS = [
    (
        "win_enum_controls",
        win_enum_controls,
        "Dump a window's UI Automation control tree (name, control_type, "
        "automation_id, class, screen rect, value). Use to discover what controls "
        "a window exposes before acting. Bounded by max_depth/max_controls.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle from win_list_hwnd"},
                "max_depth": {"type": "integer", "description": "Tree depth to walk (default 3)"},
                "max_controls": {"type": "integer", "description": "Max controls to return (default 400)"},
                "control_type": {"type": "string", "description": "Only controls whose type contains this text, e.g. 'Button'"},
            },
            "required": ["hwnd"],
        },
    ),
    (
        "win_get_control_rects",
        win_get_control_rects,
        "List a window's controls with their screen rectangles (flat list, no "
        "tree). Use center_x/center_y with mouse_click when you prefer explicit clicks.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle"},
                "max_depth": {"type": "integer", "description": "Tree depth to walk (default 3)"},
                "max_controls": {"type": "integer", "description": "Max controls to return (default 400)"},
            },
            "required": ["hwnd"],
        },
    ),
    (
        "win_find_controls",
        win_find_controls,
        "Search a window's controls by name / control_type / automation_id / "
        "class_name (case-insensitive substrings). Returns matches with rects.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle"},
                "name": {"type": "string", "description": "Visible label / accessible name substring"},
                "control_type": {"type": "string", "description": "e.g. Button, Edit, MenuItem, CheckBox"},
                "automation_id": {"type": "string", "description": "Stable automation id substring"},
                "class_name": {"type": "string", "description": "Win32 class name substring"},
                "max_depth": {"type": "integer", "description": "Tree depth to walk (default 4)"},
                "max_controls": {"type": "integer", "description": "Max controls to inspect (default 400)"},
            },
            "required": ["hwnd"],
        },
    ),
    (
        "win_wait_for",
        win_wait_for,
        "Wait until a matching control appears in a window (e.g. a dialog button "
        "or a loaded list item), or time out.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle"},
                "name": {"type": "string", "description": "Control name substring"},
                "control_type": {"type": "string", "description": "Control type substring"},
                "automation_id": {"type": "string", "description": "Automation id substring"},
                "class_name": {"type": "string", "description": "Win32 class substring"},
                "timeout": {"type": "number", "description": "Seconds to wait (default 10)"},
                "interval": {"type": "number", "description": "Poll interval in seconds (default 0.5)"},
            },
            "required": ["hwnd"],
        },
    ),
    (
        "win_click_control",
        win_click_control,
        "Find a control by name/type/automation id/class via UI Automation and "
        "click its centre with the real mouse (focusing the window first). "
        "Preferred over guessing coordinates. Pass index when several controls match.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle"},
                "name": {"type": "string", "description": "Control name substring"},
                "control_type": {"type": "string", "description": "Control type substring, e.g. Button"},
                "automation_id": {"type": "string", "description": "Automation id substring"},
                "class_name": {"type": "string", "description": "Win32 class substring"},
                "index": {"type": "integer", "description": "Which match to click when several match (default 0)"},
                "button": {"type": "string", "description": "left | right | middle (default left)"},
                "double": {"type": "boolean", "description": "Double-click (default false)"},
                "focus": {"type": "boolean", "description": "Focus the window before clicking (default true)"},
            },
            "required": ["hwnd"],
        },
    ),
    (
        "win_set_control_text",
        win_set_control_text,
        "Set a control's text/value directly through UI Automation (ValuePattern) "
        "without typing. Useful for Edit/Document controls. Falls back to set_edit_text.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle"},
                "text": {"type": "string", "description": "Text to set"},
                "name": {"type": "string", "description": "Control name substring"},
                "control_type": {"type": "string", "description": "Control type substring"},
                "automation_id": {"type": "string", "description": "Automation id substring"},
                "class_name": {"type": "string", "description": "Win32 class substring"},
            },
            "required": ["hwnd", "text"],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} UIA tool(s)")
