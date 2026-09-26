"""UI Automation (UIA) action tools for ProLight-agent.

Control *discovery* now happens in ``win_snapshot`` (which returns UIA controls
with ids). These tools act on a control by its snapshot ``id`` or by explicit
criteria:

  - win_click_control    : click a control (by id or name/type/automation id)
  - win_set_control_text : set a control's value via the UIA ValuePattern

Clicking uses the real mouse (SendInput) so the user can see it; UIA is only
used to find where the control is.
"""

import json

from loguru import logger

from lib import input_backend as ib
from lib import ui_tree
from lib import winapi
from lib import window_state


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
    return {
        "name": d.get("name"),
        "control_type": d.get("control_type"),
        "automation_id": d.get("automation_id"),
        "rect": d.get("rect"),
    }


def _element_criteria(el) -> dict:
    """Turn a snapshot element into UIA search criteria."""
    return {
        "name": "" if el.automation_id else el.name,
        "control_type": el.control_type,
        "automation_id": el.automation_id,
        "class_name": "",
    }


async def win_click_control(
    hwnd: int = None,
    id: str = "",
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    index: int = 0,
    button: str = "left",
    double: bool = False,
    focus: bool = True,
) -> str:
    """Click a control by snapshot ``id`` or by name/type/automation id/class.

    With ``id`` (from ``win_snapshot``) the click is placed at the element's
    centre directly. Otherwise matching controls are found via UIA, the chosen
    one is scrolled into view if needed, and its centre is clicked.
    """
    # ── by snapshot id ────────────────────────────────────────────────────
    if id:
        el = window_state.resolve_id(id)
        if el is None:
            return _dump({"ok": False, "error": f"unknown element id {id!r} — call win_snapshot first"})
        rect = el.rect
        if not rect:
            return _dump({"ok": False, "error": f"element {id} has no rectangle", "element": el.to_dict()})
        target = int(hwnd) if hwnd else (window_state.get().hwnd if window_state.get() else None)
        if focus and target:
            winapi.focus_window(target)
        x, y = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        await ib.move_to(x, y, duration=0.2)
        ib.click(button=button, clicks=2 if double else 1)
        logger.info(f"win_click_control(id={id}): {el.name!r} at ({x},{y})")
        return _dump({"ok": True, "element": el.to_dict(), "clicked_at": {"x": x, "y": y},
                      "button": button, "double": bool(double)})

    # ── by criteria ───────────────────────────────────────────────────────
    if hwnd is None:
        return _dump({"ok": False, "error": "need hwnd (or an element id)"})
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
    text: str,
    hwnd: int = None,
    id: str = "",
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
) -> str:
    """Set a control's text/value directly via the UIA ValuePattern.

    Target it by snapshot ``id`` or by explicit criteria. Works for
    Edit/Document controls without typing (and without needing focus).
    """
    if id:
        el = window_state.resolve_id(id)
        if el is None:
            return _dump({"ok": False, "error": f"unknown element id {id!r} — call win_snapshot first"})
        crit = _element_criteria(el)
        if not (crit["name"] or crit["automation_id"]):
            return _dump({"ok": False, "error": f"element {id} has no name/automation id to target",
                          "element": el.to_dict()})
        if hwnd is None:
            st = window_state.get()
            hwnd = st.hwnd if st else None
    else:
        crit = _criteria(name, control_type, automation_id, class_name)

    if hwnd is None:
        return _dump({"ok": False, "error": "need hwnd (or an element id from a snapshot)"})
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})
    try:
        result = await ui_tree.set_control_text(hwnd, text=text, max_depth=4, **crit)
    except Exception as e:
        return _err(e)
    if not result.get("ok"):
        return _dump(result)
    logger.info(f"win_set_control_text({hwnd}): {result.get('method')} on {result.get('control', {}).get('name')!r}")
    return _dump({"ok": True, "method": result.get("method"),
                  "control": _compact(result.get("control", {}))})


TOOL_DEFINITIONS = [
    (
        "win_click_control",
        win_click_control,
        "Click a control: pass an `id` from win_snapshot, or name/control_type/"
        "automation_id/class_name to find it via UI Automation. Clicks the "
        "centre with the real mouse (focusing the window first).",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle (optional when using id)"},
                "id": {"type": "string", "description": "Element id from win_snapshot, e.g. c7"},
                "name": {"type": "string", "description": "Control name substring"},
                "control_type": {"type": "string", "description": "Control type substring, e.g. Button"},
                "automation_id": {"type": "string", "description": "Automation id substring"},
                "class_name": {"type": "string", "description": "Win32 class substring"},
                "index": {"type": "integer", "description": "Which match to click when several match (default 0)"},
                "button": {"type": "string", "description": "left | right | middle (default left)"},
                "double": {"type": "boolean", "description": "Double-click (default false)"},
                "focus": {"type": "boolean", "description": "Focus the window before clicking (default true)"},
            },
            "required": [],
        },
    ),
    (
        "win_set_control_text",
        win_set_control_text,
        "Set a control's text/value directly through UI Automation "
        "(ValuePattern) without typing. Pass an `id` from win_snapshot or "
        "explicit criteria. Useful for Edit/Document controls.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to set"},
                "hwnd": {"type": "integer", "description": "Window handle (optional when using id)"},
                "id": {"type": "string", "description": "Element id from win_snapshot, e.g. c7"},
                "name": {"type": "string", "description": "Control name substring"},
                "control_type": {"type": "string", "description": "Control type substring"},
                "automation_id": {"type": "string", "description": "Automation id substring"},
                "class_name": {"type": "string", "description": "Win32 class substring"},
            },
            "required": ["text"],
        },
    ),
]


GROUP = "uia"


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} UIA tool(s)")
