"""Screen / window capture tools for ProLight-agent.

Tools:
  - win_get_image      : capture a single window to a PNG file
  - win_get_screenshot : capture a monitor (or all monitors) to a PNG file
  - win_see            : capture a window AND analyze it with the vision model
                         in one call (recommended for understanding a window)
"""

import io
import json
from datetime import datetime
from pathlib import Path

import mss
import mss.tools
from PIL import Image
from loguru import logger

from lib import winapi
from lib.vision_agent import get_vision_agent

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _bgra_to_pil(cap: dict) -> Image.Image:
    """Convert a winapi.capture_window() result (BGRA) to an RGB PIL image."""
    img = Image.frombytes("RGBA", (cap["width"], cap["height"]), cap["bgra"])
    b, g, r, _a = img.split()
    return Image.merge("RGB", (r, g, b))


def _downscale(img: Image.Image, max_dim: int) -> Image.Image:
    if max_dim and max(img.size) > max_dim:
        scale = max_dim / max(img.size)
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return img


def _save_png(img: Image.Image, prefix: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    path = SCREENSHOT_DIR / f"{prefix}_{ts}.png"
    img.save(path, format="PNG")
    return path


async def win_get_image(hwnd: int, max_dim: int = 1600, save: bool = True) -> str:
    """Capture a single window's pixels to a PNG file.

    Uses PrintWindow(PW_RENDERFULLCONTENT), so it also works for windows that
    are partially covered. Restores the window if minimized.
    """
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})

    cap = winapi.capture_window(hwnd)
    if not cap:
        return _dump({"ok": False, "error": "Capture failed (PrintWindow returned no pixels)"})

    img = _bgra_to_pil(cap)
    full_w, full_h = img.size
    img = _downscale(img, max_dim)

    if not save:
        return _dump({"ok": True, "width": full_w, "height": full_h, "saved": False})

    path = _save_png(img, f"window_{hwnd}")
    logger.info(f"win_get_image({hwnd}): saved {path.name} ({img.width}x{img.height})")
    return _dump({
        "ok": True,
        "path": str(path),
        "width": img.width,
        "height": img.height,
        "file_size": path.stat().st_size,
        "note": "Pass this path to vision_analyze, or use win_see to capture+analyze in one step.",
    })


async def win_get_screenshot(monitor: int = 1, max_dim: int = 1920, save: bool = True) -> str:
    """Capture a monitor (or all monitors) to a PNG file.

    Args:
        monitor: 0 = all monitors combined, 1 = primary, 2 = secondary, ...
    """
    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            if monitor < 0 or monitor >= len(monitors):
                logger.warning(f"Monitor {monitor} out of range (0..{len(monitors)-1}); using primary")
                monitor = 1 if len(monitors) > 1 else 0
            raw = sct.grab(monitors[monitor])
            img = Image.frombytes("RGB", raw.size, raw.rgb)
    except Exception as e:
        logger.error(f"win_get_screenshot failed: {e}")
        return _dump({"ok": False, "error": str(e)})

    full_w, full_h = img.size
    img = _downscale(img, max_dim)

    if not save:
        return _dump({"ok": True, "width": full_w, "height": full_h, "saved": False})

    path = _save_png(img, f"screen{monitor}")
    logger.info(f"win_get_screenshot({monitor}): saved {path.name} ({img.width}x{img.height})")
    return _dump({
        "ok": True,
        "path": str(path),
        "width": img.width,
        "height": img.height,
        "file_size": path.stat().st_size,
    })


async def win_see(hwnd: int, query: str, max_dim: int = 1600) -> str:
    """Capture a window and analyze it with the vision model in one call.

    This is the preferred way to understand what a window shows. Prefer
    specific questions ("What buttons are in the toolbar?") over vague ones.

    Args:
        hwnd: window handle to look at.
        query: what to find out about the window's contents.
    """
    hwnd = int(hwnd)
    if not winapi.is_window(hwnd):
        return _dump({"ok": False, "error": f"Invalid window handle: {hwnd}"})

    cap = winapi.capture_window(hwnd)
    if not cap:
        return _dump({"ok": False, "error": "Capture failed (PrintWindow returned no pixels)"})

    img = _downscale(_bgra_to_pil(cap), max_dim)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    try:
        description = await get_vision_agent().ask_once(png_bytes, query)
    except Exception as e:
        logger.error(f"win_see vision call failed: {e}")
        return _dump({"ok": False, "error": f"Vision analysis failed: {e}"})

    return _dump({
        "ok": True,
        "hwnd": hwnd,
        "image_size": {"width": img.width, "height": img.height},
        "answer": description,
    })


TOOL_DEFINITIONS = [
    (
        "win_get_image",
        win_get_image,
        "Capture a single window to a PNG file (works even if the window is "
        "partially covered; restores minimized windows). Returns the file path.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle to capture"},
                "max_dim": {"type": "integer", "description": "Max width/height in pixels (default 1600)"},
                "save": {"type": "boolean", "description": "Save to a PNG file (default true)"},
            },
            "required": ["hwnd"],
        },
    ),
    (
        "win_get_screenshot",
        win_get_screenshot,
        "Capture a monitor (0=all, 1=primary, 2=secondary, ...) to a PNG file.",
        {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer", "description": "Monitor index (default 1 = primary)"},
                "max_dim": {"type": "integer", "description": "Max width/height in pixels (default 1920)"},
                "save": {"type": "boolean", "description": "Save to a PNG file (default true)"},
            },
            "required": [],
        },
    ),
    (
        "win_see",
        win_see,
        "Capture a window and analyze it with the vision model in one call. "
        "Use this to understand what a window shows; ask a specific question.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle to look at"},
                "query": {"type": "string", "description": "Specific question about the window's contents"},
                "max_dim": {"type": "integer", "description": "Max width/height in pixels (default 1600)"},
            },
            "required": ["hwnd", "query"],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} capture tool(s)")
