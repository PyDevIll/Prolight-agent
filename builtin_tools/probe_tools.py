"""Reversible probe tool for ProLight-agent.

``screen_probe`` hovers (or clicks) a point and reports what changed, using a
pixel diff (no vision by default). It is the non-destructive primitive for
learning what a control does during an app discovery pass.

Tool:
  - screen_probe : BEFORE → hover/click → AFTER → pixel diff (+ optional vision,
                   optional undo for clicks)
"""

import asyncio
import json

from PIL import Image
from loguru import logger

from lib import image_ops, winapi
from lib import input_backend as ib


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _grab(screen_rect, hwnd):
    """Capture the whole window (if given) or the screen rect; return (img, meta)."""
    if hwnd:
        img = image_ops.grab_window(int(hwnd))
        if img is None:
            return None, None
        wr = winapi.get_window_rect(int(hwnd))
        if not wr:
            return None, None
        sr = (wr["left"], wr["top"], wr["right"], wr["bottom"])
    else:
        sr = image_ops.normalize_rect(screen_rect)
        img = image_ops.grab_screen(sr)
    meta = {
        "screen_rect": list(sr),
        "scale_x": (sr[2] - sr[0]) / img.width if img.width else 1.0,
        "scale_y": (sr[3] - sr[1]) / img.height if img.height else 1.0,
    }
    return img, meta


async def screen_probe(
    x: int,
    y: int,
    action: str = "hover",
    hwnd: int = None,
    rect: list = None,
    radius: int = 150,
    amount: int = 3,
    horizontal: bool = False,
    focus: bool = None,
    settle: float = 0.8,
    undo: bool = False,
    undo_hotkey: str = "",
    ask_vision: bool = False,
    query: str = "What is shown in this region? Describe the control and its state.",
    save: bool = False,
) -> str:
    """Probe a point (hover / click / scroll) and report what changed.

    Captures the region BEFORE, performs the action, captures AFTER and diffs
    them — a safe way to discover what a control does. Use ``action="scroll"``
    to test whether an area is scrollable (a change means it moved), and
    ``action="click"`` with ``undo_hotkey`` (e.g. "ctrl+z") to revert a click.

    Args:
        x, y: screen point to probe.
        action: "hover" (default), "click" or "scroll".
        hwnd: window to capture (default: the foreground window).
        rect: [left,top,right,bottom] screen region to capture (overrides hwnd).
        radius: fallback capture radius around (x,y) when there is no window.
        amount: wheel notches for action="scroll" (positive = up; default 3).
        horizontal: scroll horizontally instead of vertically (action="scroll").
        focus: focus the window first (default: auto — on for click/scroll, off
            for hover). A wheel needs the target window focused to take effect.
        settle: seconds to wait after the action before the AFTER capture.
        undo: for "scroll", wheel back by the same amount after the capture.
        undo_hotkey: if set and action="click", send this hotkey to undo.
        ask_vision: also ask the vision model about the AFTER image.
        query: vision question (with ask_vision).
        save: save BEFORE/AFTER images to data/vision/.
    """
    action = (action or "hover").lower().strip()
    if action not in ("hover", "click", "scroll"):
        return _dump({"ok": False, "error": "action must be 'hover', 'click' or 'scroll'"})

    x, y = int(x), int(y)
    if rect is None and hwnd is None:
        hwnd = winapi.get_foreground_window() or None
    if rect is None and hwnd is None:
        rect = [x - int(radius), y - int(radius), x + int(radius), y + int(radius)]

    # A wheel (and a reliable click) needs the target window focused; hover does
    # not, so leave focus alone for hover unless explicitly asked.
    if focus is None:
        focus = action in ("click", "scroll")
    if focus and hwnd:
        try:
            winapi.focus_window(int(hwnd))
            await asyncio.sleep(0.15)
        except Exception:
            pass

    before, meta = _grab(rect, hwnd)
    if before is None:
        return _dump({"ok": False, "error": "capture failed"})

    before_path = after_path = None
    if save:
        before_path = str(image_ops.save(before, "probe_before"))

    await ib.move_to(x, y, duration=0.2)
    if action == "click":
        ib.click("left")
    elif action == "scroll":
        ib.wheel(int(amount), horizontal=bool(horizontal))
    await asyncio.sleep(max(0.0, float(settle)))

    after, _ = _grab(rect, hwnd)
    if after is None:
        return _dump({"ok": False, "error": "after-capture failed", "before_path": before_path})

    if save:
        after_path = str(image_ops.save(after, "probe_after"))

    diff = image_ops.pixel_diff(before, after)
    if diff.get("bbox") and meta:
        b = diff["bbox"]
        x0, y0 = image_ops.map_image_point(meta, b[0], b[1])
        x1, y1 = image_ops.map_image_point(meta, b[2], b[3])
        diff["screen_bbox"] = [x0, y0, x1, y1]

    undone = False
    if action == "click" and undo_hotkey:
        try:
            ib.hotkey(undo_hotkey)
            undone = True
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"screen_probe undo failed: {e}")
    elif action == "scroll" and undo:
        try:
            ib.wheel(-int(amount), horizontal=bool(horizontal))
            undone = True
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"screen_probe scroll-undo failed: {e}")

    vision = None
    if ask_vision:
        try:
            from lib.vision_agent import get_vision_agent

            vision = await get_vision_agent().ask_once(after, query)
        except Exception as e:
            vision = {"ok": False, "error": str(e)}

    result = {
        "ok": True, "action": action, "probed_at": {"x": x, "y": y},
        "changed": diff.get("changed"), "pixel_diff": diff,
        "undone": undone, "before_path": before_path, "after_path": after_path,
    }
    if action == "scroll":
        result["scroll"] = {"amount": int(amount), "horizontal": bool(horizontal)}
    if vision is not None:
        result["vision"] = vision
    logger.info(f"screen_probe {action} ({x},{y}): changed={diff.get('changed')} undone={undone}")
    return _dump(result)


async def screen_tooltip(
    x: int,
    y: int,
    wait: float = 1.2,
    radius: int = 340,
    scan: int = 8,
    max_scan: int = 2,
    lang: str = "auto",
    save: bool = False,
) -> str:
    """Hover a point and read its tooltip via OCR (no vision call).

    The cheapest deterministic source of truth for an icon/button that has no
    accessible name: move the cursor to ``(x, y)``, wait for the tooltip, OCR the
    area around the cursor and return the text line nearest the cursor with its
    screen rect. Re-tries at ``x±scan`` (up to ``max_scan`` times) when nothing is
    found. Restores the original cursor position.

    Args:
        x, y: screen point to hover.
        wait: seconds to wait for the tooltip to appear (default 1.2).
        radius: size of the OCR box to the lower-right of the cursor (default 340).
        scan: horizontal step for the not-found retry (default 8 px).
        max_scan: number of retry steps left/right (default 2 each).
        lang: OCR language, or "auto" for all installed.
        save: save the captured image to data/vision/.
    """
    from lib import ocr

    x, y = int(x), int(y)
    origin = winapi.get_cursor_pos() or {}
    ox, oy = origin.get("x"), origin.get("y")
    size = winapi.get_screen_size()

    def _region(cx: int) -> list:
        left = max(0, cx - 40)
        top = max(0, y - 8)
        right = min(int(size.get("width") or 10_000), left + int(radius))
        bottom = min(int(size.get("height") or 10_000), top + int(radius))
        return [left, top, right, bottom]

    attempts = [x]
    for i in range(1, int(max_scan) + 1):
        attempts += [x - int(scan) * i, x + int(scan) * i]

    best_lines: list = []
    scanned_at = None
    result_lang = None
    try:
        for cx in attempts:
            try:
                ib.move_to(int(cx), y, duration=0.15)
            except Exception:
                winapi.set_cursor_pos(int(cx), y)
            await asyncio.sleep(max(0.15, float(wait)))
            rect = _region(int(cx))
            if rect[2] - rect[0] < 8 or rect[3] - rect[1] < 8:
                continue
            img = image_ops.grab_screen(rect)
            if img is None:
                continue
            try:
                res = await ocr.recognize_auto(img, None if lang == "auto" else [lang])
            except Exception as e:
                logger.debug(f"screen_tooltip OCR failed: {e}")
                continue
            result_lang = res.get("lang")
            lines = []
            for ln in res.get("lines", []):
                b = ln.get("bbox")
                text = (ln.get("text") or "").strip()
                if not b or not text:
                    continue
                box = [rect[0] + b[0], rect[1] + b[1], rect[0] + b[2], rect[1] + b[3]]
                dx = max(0, max(box[0] - cx, cx - box[2]))
                dy = max(0, max(box[1] - y, y - box[3]))
                lines.append({"text": text, "rect": box, "dist": int((dx * dx + dy * dy) ** 0.5)})
            lines.sort(key=lambda l: l["dist"])
            if save:
                image_ops.save(img, "tooltip")
            if lines:
                if not best_lines or lines[0]["dist"] < best_lines[0]["dist"]:
                    best_lines = lines
                    scanned_at = {"x": int(cx), "y": y}
                if lines[0]["dist"] <= 80:
                    break
            else:
                break
    finally:
        if ox is not None and oy is not None:
            try:
                winapi.set_cursor_pos(int(ox), int(oy))
            except Exception:
                pass

    found = bool(best_lines) and best_lines[0]["dist"] <= 80
    logger.info(f"screen_tooltip ({x},{y}): found={found} "
                f"text={(best_lines[0]['text'] if found else '')!r}")
    return _dump({
        "ok": True, "found": found,
        "text": best_lines[0]["text"] if found else "",
        "rect": best_lines[0]["rect"] if found else None,
        "lines": best_lines[:8],
        "scanned_at": scanned_at, "lang": result_lang,
        "hint": None if found else "no tooltip text found near the point — the control may have no tooltip",
    })


TOOL_DEFINITIONS = [
    (
        "screen_probe",
        screen_probe,
        "Probe a point and report what changed (BEFORE/AFTER pixel diff) — a safe "
        "way to discover what a control does. action='hover' (tooltips/highlights), "
        "'click' (use undo_hotkey, e.g. ctrl+z, to revert), or 'scroll' to test "
        "whether an area is scrollable. Optionally ask the vision model.",
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Screen X to probe"},
                "y": {"type": "integer", "description": "Screen Y to probe"},
                "action": {"type": "string", "description": "hover (default) | click | scroll"},
                "hwnd": {"type": "integer", "description": "Window to capture (default: foreground)"},
                "rect": {"type": "array", "items": {"type": "integer"}, "description": "[left,top,right,bottom] region to capture"},
                "radius": {"type": "integer", "description": "Fallback capture radius around the point (default 150)"},
                "amount": {"type": "integer", "description": "Wheel notches for action=scroll (positive = up; default 3)"},
                "horizontal": {"type": "boolean", "description": "Scroll horizontally instead (action=scroll)"},
                "focus": {"type": "boolean", "description": "Focus the window first (default auto: on for click/scroll, off for hover)"},
                "settle": {"type": "number", "description": "Seconds to wait after the action (default 0.8)"},
                "undo": {"type": "boolean", "description": "For action=scroll, wheel back after the capture"},
                "undo_hotkey": {"type": "string", "description": "Hotkey to undo a click, e.g. ctrl+z"},
                "ask_vision": {"type": "boolean", "description": "Also ask the vision model about the AFTER image"},
                "query": {"type": "string", "description": "Vision question (with ask_vision)"},
                "save": {"type": "boolean", "description": "Save BEFORE/AFTER images (default false)"},
            },
            "required": ["x", "y"],
        },
    ),
    (
        "screen_tooltip",
        screen_tooltip,
        "Hover a point and read its tooltip via OCR (no vision call). The cheapest "
        "deterministic way to identify an icon/button that has no accessible name: "
        "returns {found, text, rect} for the line nearest the cursor, retrying at "
        "x±8 px when nothing is found, and restores the cursor.",
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Screen X to hover"},
                "y": {"type": "integer", "description": "Screen Y to hover"},
                "wait": {"type": "number", "description": "Seconds to wait for the tooltip (default 1.2)"},
                "radius": {"type": "integer", "description": "OCR box size lower-right of the cursor (default 340)"},
                "scan": {"type": "integer", "description": "Retry step in px (default 8)"},
                "max_scan": {"type": "integer", "description": "Retry steps left/right (default 2)"},
                "lang": {"type": "string", "description": "OCR language or 'auto' (default auto)"},
                "save": {"type": "boolean", "description": "Save the captured image (default false)"},
            },
            "required": ["x", "y"],
        },
    ),
]


GROUP = "probe"


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} probe tool(s)")
