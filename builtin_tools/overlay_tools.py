"""Overlay tools for ProLight-agent — deliberate on-screen highlighting.

These let the agent show the user *exactly* which screen area it means, e.g.
while asking whether a region is correct during an app discovery pass or while
learning a workflow. Highlights are drawn even when ambient overlay feedback is
disabled via ``PROLIGHT_OVERLAY=0``.

Tools:
  - highlight_area  : draw one or more labelled boxes on screen
  - highlight_clear : remove boxes drawn by highlight_area
"""

import json

from loguru import logger

from lib import overlay


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _count(regions) -> int:
    if isinstance(regions, dict):
        return 1
    if isinstance(regions, (list, tuple)):
        if len(regions) == 4 and all(isinstance(v, (int, float)) for v in regions):
            return 1
        return len(regions)
    return 1


async def highlight_area(
    regions: list,
    label: str = "",
    color: str = "",
    persist: bool = True,
    duration: float = 0.0,
    note: str = "",
) -> str:
    """Draw highlight box(es) on screen so the user can see the area you mean.

    Use this before/while asking about a region (e.g. "is this the right area?")
    or during discovery to show what you found. Each region is a screen
    ``[left, top, right, bottom]`` or an object with ``rect`` / ``hwnd`` /
    ``point`` (+ ``radius``) / ``bbox`` and an optional ``label``.

    Args:
        regions: one region or a list of regions to highlight.
        label: optional label chip shown on every region (overridden per-region).
        color: optional '#RRGGBB' or 'r,g,b' (default cyan).
        persist: keep the highlight until highlight_clear (default true).
        duration: seconds to show when persist=false (default 4).
        note: convenience alias for label.
    """
    label = label or note or ""
    col = color.strip() if isinstance(color, str) and color.strip() else overlay._HIGHLIGHT_COLOR
    token = overlay.highlight(
        regions,
        color=col,
        persist=bool(persist),
        duration=(float(duration) if duration else None),
        label=(label or None),
    )
    if token is None:
        return _dump({
            "ok": False,
            "error": "no valid regions — use [left,top,right,bottom] or "
                     "{rect|hwnd|point(+radius)|bbox} objects",
        })
    return _dump({
        "ok": True, "token": token, "count": _count(regions),
        "persist": bool(persist),
        "hint": "call highlight_clear(token) when done" if persist else "",
    })


async def highlight_clear(token: int = None) -> str:
    """Remove highlight box(es).

    Args:
        token: token returned by highlight_area. Omit to clear all highlights.
    """
    overlay.clear_highlights(token)
    return _dump({"ok": True, "cleared": "all" if token is None else token})


TOOL_DEFINITIONS = [
    (
        "highlight_area",
        highlight_area,
        "Draw highlight box(es) on screen so the user sees exactly which area you "
        "mean — useful before/while asking about a region, or when showing what "
        "you found during discovery. Provide [left,top,right,bottom] or objects "
        "with rect/hwnd/point(+radius)/bbox. Persists until highlight_clear.",
        {
            "type": "object",
            "properties": {
                "regions": {
                    "type": "array",
                    "description": "One or more screen regions to highlight",
                    "items": {
                        "oneOf": [
                            {"type": "array", "items": {"type": "integer"},
                             "minItems": 4, "maxItems": 4},
                            {
                                "type": "object",
                                "properties": {
                                    "rect": {"type": "array", "items": {"type": "integer"}},
                                    "hwnd": {"type": "integer"},
                                    "point": {"type": "array", "items": {"type": "integer"}},
                                    "radius": {"type": "integer"},
                                    "label": {"type": "string"},
                                },
                            },
                        ]
                    },
                },
                "label": {"type": "string", "description": "Label chip shown on the region(s)"},
                "color": {"type": "string", "description": "'#RRGGBB' or 'r,g,b' (default cyan)"},
                "persist": {"type": "boolean", "description": "Keep until highlight_clear (default true)"},
                "duration": {"type": "number", "description": "Seconds to show when persist=false (default 4)"},
                "note": {"type": "string", "description": "Alias for label"},
            },
            "required": ["regions"],
        },
    ),
    (
        "highlight_clear",
        highlight_clear,
        "Remove highlight box(es) drawn by highlight_area (all, or one token).",
        {
            "type": "object",
            "properties": {
                "token": {"type": "integer", "description": "Token from highlight_area; omit to clear all"},
            },
            "required": [],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} overlay tool(s)")
