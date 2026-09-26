"""Deterministic locator tools for ProLight-agent (no vision model).

Use these when ``win_snapshot`` gives you nothing usable — custom-drawn UIs
(empty UIA tree) or a pixel-precise target. They capture a window/region at
native resolution and locate elements by **colour**, **template** or **text
(OCR)**, returning physical SCREEN coordinates ready for ``mouse_click``.

Tool:
  - screen_find : kind="color" | "template" | "text"
"""

import json
from typing import Optional

import mss
from PIL import Image
from loguru import logger

from lib import image_ops, ocr


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


def _box_to_screen(screen_rect, img, bbox) -> list:
    x0, y0 = _to_screen(screen_rect, img, bbox[0], bbox[1])
    x1, y1 = _to_screen(screen_rect, img, bbox[2], bbox[3])
    return [x0, y0, x1, y1]


def _match(needle: str, hay: str, exact: bool, case_sensitive: bool) -> bool:
    if not case_sensitive:
        needle, hay = needle.lower(), hay.lower()
    return hay == needle if exact else needle in hay


async def _find_text(text, screen_rect, img, exact, case_sensitive, lang, max_results):
    result = await ocr.recognize_auto(img) if lang in (None, "", "auto") else \
        await ocr.recognize(img, lang=lang)
    matches = []
    if " " in text.strip():
        candidates = [(ln["text"], ln["bbox"]) for ln in result.get("lines", [])]
    else:
        candidates = [(w["text"], w["bbox"]) for w in result.get("words", [])]
    for value, bbox in candidates:
        if not value or not _match(text, value, exact, case_sensitive):
            continue
        sb = _box_to_screen(screen_rect, img, bbox)
        matches.append({
            "text": value,
            "screen_bbox": sb,
            "screen_center": [(sb[0] + sb[2]) // 2, (sb[1] + sb[3]) // 2],
        })
        if len(matches) >= max_results:
            break
    return matches, result.get("lang")


async def screen_find(
    kind: str,
    color=None,
    template_path: str = "",
    text: str = "",
    hwnd: int = None,
    rect: list = None,
    tolerance: int = 16,
    min_area: int = 4,
    max_regions: int = 32,
    threshold: float = 30.0,
    max_results: int = 8,
    exact: bool = False,
    case_sensitive: bool = False,
    lang: str = "auto",
    save: bool = False,
) -> str:
    """Deterministically locate elements and return SCREEN coordinates.

    Args:
        kind: "color" (colour blobs), "template" (match an image), or "text" (OCR).
        color: [r,g,b] or "#RRGGBB" (kind=color).
        template_path: small PNG/JPG to find (kind=template).
        text: label/text to find (kind=text).
        hwnd: window to search (native pixels; works while occluded).
        rect: [left,top,right,bottom] screen region (if no hwnd).
        tolerance: per-channel colour tolerance 0-255 (kind=color).
        min_area: ignore colour blobs smaller than this (kind=color).
        max_regions: cap on colour blobs, largest first (kind=color).
        threshold: max mean pixel diff to accept (kind=template).
        max_results: cap on returned matches.
        exact: text must match exactly (kind=text).
        case_sensitive: case-sensitive text match (kind=text).
        lang: OCR language, or "auto" for all installed (kind=text).
        save: save the captured image to data/vision/.
    """
    kind = (kind or "").lower().strip()
    img, screen_rect = _capture(hwnd, rect)
    if img is None:
        return _dump({"ok": False, "error": "capture failed"})

    try:
        if kind == "color":
            if color is None:
                return _dump({"ok": False, "error": "kind='color' needs `color`"})
            regions = image_ops.find_color_regions(
                img, color, tolerance=tolerance, min_area=min_area, max_regions=max_regions
            )
            matches = [{
                "screen_center": {"x": _to_screen(screen_rect, img, r["center"][0], r["center"][1])[0],
                                  "y": _to_screen(screen_rect, img, r["center"][0], r["center"][1])[1]},
                "screen_bbox": _box_to_screen(screen_rect, img, r["bbox"]),
                "image_bbox": r["bbox"], "width": r["width"], "height": r["height"], "area": r["area"],
            } for r in regions]
        elif kind == "template":
            if not template_path:
                return _dump({"ok": False, "error": "kind='template' needs `template_path`"})
            template = Image.open(str(template_path))
            found = image_ops.find_template(img, template, threshold=threshold, max_results=max_results)
            matches = [{
                "score": m["score"],
                "screen_bbox": _box_to_screen(screen_rect, img, m["bbox"]),
                "screen_center": {"x": _to_screen(screen_rect, img, m["center"][0], m["center"][1])[0],
                                  "y": _to_screen(screen_rect, img, m["center"][0], m["center"][1])[1]},
            } for m in found]
        elif kind == "text":
            if not text:
                return _dump({"ok": False, "error": "kind='text' needs `text`"})
            matches, used_lang = await _find_text(
                text, screen_rect, img, exact, case_sensitive, lang, max_results
            )
        else:
            return _dump({"ok": False, "error": f"unknown kind {kind!r} (use color/template/text)"})
    except Exception as e:
        logger.error(f"screen_find({kind}) failed: {e}")
        return _dump({"ok": False, "error": str(e)})

    result = {
        "ok": True,
        "kind": kind,
        "image_size": {"width": img.width, "height": img.height},
        "screen_rect": list(screen_rect),
        "count": len(matches),
        "matches": matches,
    }
    if kind == "text":
        result["lang"] = used_lang
    if save:
        result["path"] = str(image_ops.save(img, f"find_{kind}"))
    logger.info(f"screen_find({kind}): {len(matches)} match(es)")
    return _dump(result)


TOOL_DEFINITIONS = [
    (
        "screen_find",
        screen_find,
        "Deterministically locate elements and return SCREEN coordinates (no "
        "vision model): kind='color' (blobs of a colour), 'template' (match a "
        "small image) or 'text' (OCR). Use when win_snapshot has no useful UIA "
        "or OCR, or for a pixel-precise target. Click the returned screen_center.",
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "color | template | text"},
                "color": {"type": "array", "items": {"type": "integer"}, "description": "[r,g,b] for kind=color"},
                "template_path": {"type": "string", "description": "Small image to find (kind=template)"},
                "text": {"type": "string", "description": "Label/text to find (kind=text)"},
                "hwnd": {"type": "integer", "description": "Window to search (native pixels)"},
                "rect": {"type": "array", "items": {"type": "integer"}, "description": "[left,top,right,bottom] screen region"},
                "tolerance": {"type": "integer", "description": "Colour tolerance 0-255 (kind=color, default 16)"},
                "min_area": {"type": "integer", "description": "Min blob area (kind=color, default 4)"},
                "max_regions": {"type": "integer", "description": "Max colour blobs (default 32)"},
                "threshold": {"type": "number", "description": "Max mean pixel diff (kind=template, default 30)"},
                "max_results": {"type": "integer", "description": "Max matches (default 8)"},
                "exact": {"type": "boolean", "description": "Exact text match (kind=text)"},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive text match (kind=text)"},
                "lang": {"type": "string", "description": "OCR language or 'auto' (kind=text, default auto)"},
                "save": {"type": "boolean", "description": "Save the captured image (default false)"},
            },
            "required": ["kind"],
        },
    ),
]


GROUP = "locate"


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} search tool(s)")
