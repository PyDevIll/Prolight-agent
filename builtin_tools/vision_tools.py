"""Vision tools for ProLight-agent.

All vision analysis goes through the dedicated vision sub-agent
(``lib.vision_agent.VisionAgent``), which uses its own API key (HELPER) and its
own small, image-aware context, so screenshots never pollute the main agent's
context.

Perception is normally done with ``win_snapshot``; these tools are for a
targeted semantic look or a structured element search:
  - vision_look    : capture a region/control/file and describe it (optional
                     ``structured`` boxes); watched for vision_compare when
                     labelled. Every call is one-shot (no history).
  - vision_compare : re-capture a watched region (BEFORE vs AFTER), or a cheap
                     pixel-only change check.
  - vision_forget  : list or drop watched regions.
"""

import json
from pathlib import Path

from loguru import logger

from lib.vision_agent import get_vision_agent


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


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
    path: str = "",
    structured: bool = False,
) -> str:
    """Look at a region/control (or an image file) and describe it.

    Give a ``rect`` [left, top, right, bottom] (screen pixels), a window
    ``hwnd``, a control (``control_name``/``control_type``/``automation_id``/
    ``class_name`` + ``hwnd``), or a ``path`` to an existing image. With a
    ``label`` the region is *watched* for ``vision_compare``.

    With ``structured=true`` it returns approximate element boxes (fractions of
    the crop, snapped to pixels) instead of prose — useful to locate an element
    the deterministic tools cannot (then verify/refine).
    """
    va = get_vision_agent()

    if path:
        p = Path(path)
        if not p.exists():
            return _dump({"ok": False, "error": f"Image not found: {path}"})
        try:
            answer = await va.ask_once(p, query)
        except Exception as e:
            logger.error(f"vision_look(path) failed: {e}")
            return _dump({"ok": False, "error": str(e)})
        return _dump({"ok": True, "path": str(p), "answer": answer})

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
        if structured:
            result = await va.locate(
                rect=rect, hwnd=hwnd, control=control, query=query, label=label,
                source=source, pad=pad, scale=scale,
            )
        else:
            result = await va.look(
                rect=rect, hwnd=hwnd, control=control, query=query, label=label,
                source=source, pad=pad, scale=scale,
            )
    except Exception as e:
        logger.error(f"vision_look failed: {e}")
        return _dump({"ok": False, "error": str(e)})
    return _dump(result)


async def vision_compare(
    label: str, query: str = "", pixel_only: bool = False, threshold: int = 12
) -> str:
    """Re-capture a watched region and compare it with the previous look.

    With ``pixel_only=true`` it does a cheap pixel diff (no vision call) — use
    it first to decide whether a vision comparison is needed.
    """
    va = get_vision_agent()
    try:
        if pixel_only:
            result = await va.changed(label, threshold=threshold)
        else:
            result = await va.compare(label, query)
    except Exception as e:
        logger.error(f"vision_compare failed: {e}")
        return _dump({"ok": False, "error": str(e)})
    return _dump(result)


async def vision_forget(label: str = "", list_only: bool = False) -> str:
    """List watched regions, or drop one (empty label = drop all)."""
    va = get_vision_agent()
    if list_only:
        return _dump({"ok": True, "watches": va.watches_list()})
    dropped = va.forget(label or None)
    return _dump({"ok": True, "dropped": dropped, "label": label or None})


TOOL_DEFINITIONS = [
    (
        "vision_look",
        vision_look,
        "Look at a small region/control (or an image file) and describe it with "
        "the vision model. Prefer a tight `rect` or a control (hwnd + "
        "control_type/name); use `scale` to zoom a tiny control. With a `label` "
        "it is watched for vision_compare. Set structured=true to get approximate "
        "element boxes (fractions of the crop, snapped to pixels).",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle (for control lookup or whole-window look)"},
                "rect": {"type": "array", "items": {"type": "integer"}, "description": "Screen rect [left, top, right, bottom]"},
                "control_name": {"type": "string", "description": "Control name substring (needs hwnd)"},
                "control_type": {"type": "string", "description": "Control type substring, e.g. CheckBox (needs hwnd)"},
                "automation_id": {"type": "string", "description": "Control automation id substring (needs hwnd)"},
                "class_name": {"type": "string", "description": "Win32 class substring (needs hwnd)"},
                "query": {"type": "string", "description": "Specific question, e.g. 'Is the checkbox ticked?'"},
                "label": {"type": "string", "description": "Name this view so it can be compared later"},
                "source": {"type": "string", "description": "auto | screen | window (default auto)"},
                "pad": {"type": "integer", "description": "Extra pixels around the region (default 0)"},
                "scale": {"type": "number", "description": "Zoom factor for tiny controls, e.g. 3.0 (default 1.0)"},
                "path": {"type": "string", "description": "Analyze an existing image file instead of capturing"},
                "structured": {"type": "boolean", "description": "Return approximate element boxes (fractions), not prose"},
            },
            "required": [],
        },
    ),
    (
        "vision_compare",
        vision_compare,
        "Re-capture a labelled region (from vision_look) and ask what changed "
        "(BEFORE vs AFTER). Set pixel_only=true for a cheap pixel diff with no "
        "vision call.",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Label of a region previously passed to vision_look"},
                "query": {"type": "string", "description": "Optional custom comparison question"},
                "pixel_only": {"type": "boolean", "description": "Cheap pixel diff only, no vision call (default false)"},
                "threshold": {"type": "integer", "description": "Per-pixel threshold for pixel_only (default 12)"},
            },
            "required": ["label"],
        },
    ),
    (
        "vision_forget",
        vision_forget,
        "List watched regions (list_only=true) or drop one by label (empty = all).",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Label to forget (empty = all)"},
                "list_only": {"type": "boolean", "description": "Just list the watches"},
            },
            "required": [],
        },
    ),
]


GROUP = "vision"


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} vision tool(s)")
