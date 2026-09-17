"""Vision tools for ProLight-agent.

All vision analysis goes through the dedicated vision sub-agent
(``lib.vision_agent.VisionAgent``), which uses its own API key (HELPER) and its
own small, image-aware context, so screenshots never pollute the main agent's
context.

Tools:
  - vision_analyze : analyze an existing image file (one-shot)
  - vision_look    : capture a region/control and analyze it (remembers it)
  - vision_compare : re-capture a watched region and compare BEFORE vs AFTER
  - vision_changed : cheap pixel-level "did it change?" check (no LLM)
  - vision_watches : list watched regions
  - vision_forget  : drop one watch (or all)
"""

import json
from pathlib import Path

from loguru import logger

from lib.vision_agent import get_vision_agent


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


async def vision_analyze(image_path: str, query: str) -> str:
    """Analyze an existing image file with the vision sub-agent (one-shot)."""
    path = Path(image_path)
    if not path.exists():
        return _dump({"ok": False, "error": f"Image not found: {image_path}"})
    try:
        answer = await get_vision_agent().ask_once(path, query)
    except Exception as e:
        logger.error(f"vision_analyze failed: {e}")
        return _dump({"ok": False, "error": str(e)})
    return _dump({"ok": True, "path": str(path), "answer": answer})


async def vision_look(
    hwnd: int = None,
    rect: list = None,
    control_name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    query: str = "What is shown here? Describe the visible state.",
    label: str = "",
    source: str = "auto",
    pad: int = 0,
    scale: float = 1.0,
    remember: bool = True,
) -> str:
    """Capture a region (or a control) and analyze it with the vision sub-agent.

    Give either ``rect`` [left, top, right, bottom] (screen pixels), or a window
    ``hwnd``, or a control (``control_name``/``control_type``/``automation_id``/
    ``class_name`` + ``hwnd``). With a ``label`` the region is *watched*, so you
    can later call ``vision_compare`` or ``vision_changed`` on it.
    """
    va = get_vision_agent()
    control = None
    if control_name or control_type or automation_id or class_name:
        if hwnd is None:
            return _dump({"ok": False, "error": "control lookup requires hwnd"})
        control = {
            "name": control_name,
            "control_type": control_type,
            "automation_id": automation_id,
            "class_name": class_name,
        }
    try:
        result = await va.look(
            rect=rect, hwnd=hwnd, control=control, query=query, label=label,
            source=source, pad=pad, scale=scale, remember=remember,
        )
    except Exception as e:
        logger.error(f"vision_look failed: {e}")
        return _dump({"ok": False, "error": str(e)})
    return _dump(result)


async def vision_compare(label: str, query: str = "") -> str:
    """Re-capture a watched region and ask the vision model to compare it with
    the previous observation (BEFORE vs AFTER)."""
    try:
        result = await get_vision_agent().compare(label, query)
    except Exception as e:
        logger.error(f"vision_compare failed: {e}")
        return _dump({"ok": False, "error": str(e)})
    return _dump(result)


async def vision_changed(label: str, threshold: int = 12) -> str:
    """Cheap pixel-level check: has the watched region changed since the last
    look? No LLM call — use it to avoid an unnecessary vision request."""
    try:
        result = await get_vision_agent().changed(label, threshold=threshold)
    except Exception as e:
        logger.error(f"vision_changed failed: {e}")
        return _dump({"ok": False, "error": str(e)})
    return _dump(result)


async def vision_watches() -> str:
    """List the currently watched regions (label, rect, last answer)."""
    return _dump({"ok": True, "watches": get_vision_agent().watches_list()})


async def vision_forget(label: str = "") -> str:
    """Drop a watched region by label (or all watches when label is empty)."""
    dropped = get_vision_agent().forget(label or None)
    return _dump({"ok": True, "dropped": dropped, "label": label or None})


TOOL_DEFINITIONS = [
    (
        "vision_analyze",
        vision_analyze,
        "Analyze an existing image file with the vision model (one-shot). "
        "(To capture and analyze a window in one step, use win_see.)",
        {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Path to the image file"},
                "query": {"type": "string", "description": "Specific question about the image"},
            },
            "required": ["image_path", "query"],
        },
    ),
    (
        "vision_look",
        vision_look,
        "Look at a small region of the screen and describe it: either a rect "
        "[left,top,right,bottom], a window hwnd, or a specific control "
        "(control_name/control_type/automation_id/class_name + hwnd). Cropping to "
        "the region of interest is far more accurate than looking at a whole "
        "window. With a `label` the region is remembered for vision_compare/"
        "vision_changed. Use it to verify a checkbox, a selection, a field value, etc.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle (for control lookup or whole-window look)"},
                "rect": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Screen rect [left, top, right, bottom] in physical pixels",
                },
                "control_name": {"type": "string", "description": "Control name substring (needs hwnd)"},
                "control_type": {"type": "string", "description": "Control type substring, e.g. CheckBox (needs hwnd)"},
                "automation_id": {"type": "string", "description": "Control automation id substring (needs hwnd)"},
                "class_name": {"type": "string", "description": "Win32 class substring (needs hwnd)"},
                "query": {"type": "string", "description": "Specific question, e.g. 'Is the checkbox ticked?'"},
                "label": {"type": "string", "description": "Name this view so it can be compared later"},
                "source": {"type": "string", "description": "auto | screen | window (default auto)"},
                "pad": {"type": "integer", "description": "Extra pixels around the region (default 0)"},
                "scale": {"type": "number", "description": "Zoom factor for tiny controls, e.g. 3.0 (default 1.0)"},
                "remember": {"type": "boolean", "description": "Add this observation to visual context (default true)"},
            },
            "required": [],
        },
    ),
    (
        "vision_compare",
        vision_compare,
        "Re-capture a labelled region (from vision_look) and ask the vision model "
        "what changed since the previous look (BEFORE vs AFTER). Use after a click "
        "or typing to confirm the effect (ticked checkbox, selection, new value).",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Label of a region previously passed to vision_look"},
                "query": {"type": "string", "description": "Optional custom comparison question"},
            },
            "required": ["label"],
        },
    ),
    (
        "vision_changed",
        vision_changed,
        "Cheap pixel-level check of whether a watched region changed since the last "
        "look. No vision call — use it to decide whether vision_compare is needed.",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Label of a watched region"},
                "threshold": {"type": "integer", "description": "Per-pixel difference threshold (default 12)"},
            },
            "required": ["label"],
        },
    ),
    (
        "vision_watches",
        vision_watches,
        "List the currently watched regions (label, rect, last answer).",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "vision_forget",
        vision_forget,
        "Drop a watched region by label (or all watches when label is empty).",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Label to forget (empty = forget all)"},
            },
            "required": [],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} vision tool(s)")
