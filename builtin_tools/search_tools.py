"""Deterministic geometry tools for ProLight-agent (no vision model).

Use these when UIA exposes nothing useful (custom-drawn UIs like MS Paint) and
the vision model's pixel coordinates are unreliable. They capture a window /
region at native resolution and locate elements by **colour** or by **template**,
returning physical SCREEN coordinates ready for `mouse_click`.

Tools:
  - screen_find_color    : connected blobs matching a colour (e.g. a red brush dot)
  - screen_find_template : locate a small template image inside a window/region
"""

import json
from typing import Optional

import mss
from PIL import Image
from loguru import logger

from lib import image_ops


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _capture(hwnd: Optional[int], rect) -> tuple[Optional[Image.Image], Optional[tuple]]:
    """Capture at native resolution and return (image, screen_rect)."""
    if hwnd is not None:
        img = image_ops.grab_window(int(hwnd))
        if img is None:
            return None, None
        from lib import winapi
        wr = winapi.get_window_rect(int(hwnd))
        if not wr:
            return None, None
        return img, (wr["left"], wr["top"], wr["right"], wr["bottom"])
    if rect is not None:
        screen_rect = image_ops.normalize_rect(rect)
        return image_ops.grab_screen(screen_rect), screen_rect
    with mss.mss() as sct:
        mon = sct.monitors[1]
    screen_rect = (mon["left"], mon["top"], mon["left"] + mon["width"], mon["top"] + mon["height"])
    return image_ops.grab_screen(None, monitor=1), screen_rect


def _to_screen(screen_rect, img, ix, iy) -> tuple[int, int]:
    meta = {
        "screen_rect": screen_rect,
        "scale_x": ((screen_rect[2] - screen_rect[0]) / img.width) if img.width else 1.0,
        "scale_y": ((screen_rect[3] - screen_rect[1]) / img.height) if img.height else 1.0,
    }
    return image_ops.map_image_point(meta, ix, iy)


async def screen_find_color(
    color,
    hwnd: int = None,
    rect: list = None,
    tolerance: int = 16,
    min_area: int = 4,
    max_regions: int = 32,
    save: bool = False,
) -> str:
    """Find connected blobs of a colour and return their SCREEN coordinates.

    Deterministic (no vision model): capture the target at native resolution,
    match pixels within ``tolerance`` of ``color`` and group them into blobs.
    Ideal for a red brush mark, a palette swatch, a status LED, etc.

    Args:
        color: [r,g,b] (or '#RRGGBB').
        hwnd: window to search (native pixels; works while occluded).
        rect: [left,top,right,bottom] screen region to search (if no hwnd).
        tolerance: per-channel tolerance 0-255 (default 16).
        min_area: ignore blobs smaller than this many pixels.
        max_regions: cap on returned blobs (largest first).
        save: also save the captured image to data/vision/.
    """
    if hwnd is None and rect is None:
        pass  # full primary monitor
    img, screen_rect = _capture(hwnd, rect)
    if img is None:
        return _dump({"ok": False, "error": "capture failed"})

    try:
        regions = image_ops.find_color_regions(
            img, color, tolerance=tolerance, min_area=min_area, max_regions=max_regions
        )
    except Exception as e:
        logger.error(f"screen_find_color failed: {e}")
        return _dump({"ok": False, "error": str(e)})

    matches = []
    for r in regions:
        cx, cy = r["center"]
        scx, scy = _to_screen(screen_rect, img, cx, cy)
        x0, y0 = _to_screen(screen_rect, img, r["bbox"][0], r["bbox"][1])
        x1, y1 = _to_screen(screen_rect, img, r["bbox"][2], r["bbox"][3])
        matches.append({
            "screen_center": {"x": scx, "y": scy},
            "screen_bbox": [x0, y0, x1, y1],
            "image_bbox": r["bbox"],
            "width": r["width"],
            "height": r["height"],
            "area": r["area"],
        })

    result = {
        "ok": True,
        "color": color,
        "tolerance": tolerance,
        "image_size": {"width": img.width, "height": img.height},
        "screen_rect": list(screen_rect),
        "count": len(matches),
        "matches": matches,
    }
    if save:
        result["path"] = str(image_ops.save(img, "find_color"))
    logger.info(f"screen_find_color: {len(matches)} blob(s) for {color}")
    return _dump(result)


async def screen_find_template(
    template_path: str,
    hwnd: int = None,
    rect: list = None,
    threshold: float = 30.0,
    max_results: int = 8,
    save: bool = False,
) -> str:
    """Locate a template image inside a window/region; return SCREEN coordinates.

    Deterministic coarse-to-fine template match (pure PIL). Best for small,
    distinctive templates. Lower ``threshold`` = stricter (mean pixel diff 0-255).

    Args:
        template_path: path to a small PNG/JPG to find.
        hwnd: window to search (native pixels; works while occluded).
        rect: [left,top,right,bottom] screen region to search (if no hwnd).
        threshold: max mean pixel difference to accept (default 30).
        max_results: cap on returned matches.
        save: also save the captured image to data/vision/.
    """
    try:
        template = Image.open(str(template_path))
    except Exception as e:
        return _dump({"ok": False, "error": f"cannot open template: {e}"})

    img, screen_rect = _capture(hwnd, rect)
    if img is None:
        return _dump({"ok": False, "error": "capture failed"})

    try:
        found = image_ops.find_template(
            img, template, threshold=threshold, max_results=max_results
        )
    except Exception as e:
        logger.error(f"screen_find_template failed: {e}")
        return _dump({"ok": False, "error": str(e)})

    matches = []
    for m in found:
        cx, cy = m["center"]
        scx, scy = _to_screen(screen_rect, img, cx, cy)
        x0, y0 = _to_screen(screen_rect, img, m["bbox"][0], m["bbox"][1])
        x1, y1 = _to_screen(screen_rect, img, m["bbox"][2], m["bbox"][3])
        matches.append({
            "score": m["score"],
            "screen_center": {"x": scx, "y": scy},
            "screen_bbox": [x0, y0, x1, y1],
        })

    result = {
        "ok": True,
        "template": str(template_path),
        "threshold": threshold,
        "image_size": {"width": img.width, "height": img.height},
        "screen_rect": list(screen_rect),
        "count": len(matches),
        "matches": matches,
    }
    if save:
        result["path"] = str(image_ops.save(img, "find_template"))
    logger.info(f"screen_find_template: {len(matches)} match(es)")
    return _dump(result)


TOOL_DEFINITIONS = [
    (
        "screen_find_color",
        screen_find_color,
        "Deterministically find blobs of a colour on screen/window and return "
        "their SCREEN coordinates (no vision model). Use when UIA exposes nothing "
        "(e.g. MS Paint) to locate a palette swatch, a coloured mark, a status dot. "
        "Then click the returned screen_center with mouse_click.",
        {
            "type": "object",
            "properties": {
                "color": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "[r,g,b], e.g. [255,0,0] for red",
                },
                "hwnd": {"type": "integer", "description": "Window to search (native pixels)"},
                "rect": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "[left,top,right,bottom] screen region (if no hwnd)",
                },
                "tolerance": {"type": "integer", "description": "Per-channel tolerance 0-255 (default 16)"},
                "min_area": {"type": "integer", "description": "Ignore blobs smaller than this (default 4)"},
                "max_regions": {"type": "integer", "description": "Max blobs returned, largest first (default 32)"},
                "save": {"type": "boolean", "description": "Save the captured image (default false)"},
            },
            "required": ["color"],
        },
    ),
    (
        "screen_find_template",
        screen_find_template,
        "Deterministically locate a small template image inside a window/region and "
        "return SCREEN coordinates. Use for a distinctive icon/button when UIA and "
        "colour search are not enough. Lower threshold = stricter.",
        {
            "type": "object",
            "properties": {
                "template_path": {"type": "string", "description": "Path to a small template image"},
                "hwnd": {"type": "integer", "description": "Window to search (native pixels)"},
                "rect": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "[left,top,right,bottom] screen region (if no hwnd)",
                },
                "threshold": {"type": "number", "description": "Max mean pixel diff to accept (default 30)"},
                "max_results": {"type": "integer", "description": "Max matches (default 8)"},
                "save": {"type": "boolean", "description": "Save the captured image (default false)"},
            },
            "required": ["template_path"],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} search tool(s)")
