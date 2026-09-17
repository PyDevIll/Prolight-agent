"""Image capture, cropping, saving and comparison for ProLight-agent.

Used by the vision sub-agent to look at small regions (a control, a cell, a
badge) instead of whole windows. Cropping is both cheaper (far fewer image
tokens) and more accurate (the model sees the detail at full resolution).

Two capture sources are supported:
  * ``screen`` — grab the live screen with mss (fast; shows whatever is on top);
  * ``window`` — grab a window with PrintWindow (works while occluded) and crop
    inside it.
All rectangles are in screen coordinates (physical pixels); ``window`` capture
converts them to window-local coordinates internally.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, Union

import mss
from PIL import Image, ImageChops
from loguru import logger

from lib import winapi

VISION_DIR = Path(__file__).resolve().parent.parent / "data" / "vision"
VISION_DIR.mkdir(parents=True, exist_ok=True)

RectLike = Union[dict, Sequence[int]]


def normalize_rect(rect: RectLike) -> tuple[int, int, int, int]:
    """Accept a {left,top,right,bottom} dict or a 4-item sequence."""
    if isinstance(rect, dict):
        return int(rect["left"]), int(rect["top"]), int(rect["right"]), int(rect["bottom"])
    left, top, right, bottom = (int(v) for v in rect)
    return left, top, right, bottom


def bgra_to_pil(cap: dict) -> Image.Image:
    """Convert a ``winapi.capture_window()`` result (BGRA) to an RGB image."""
    img = Image.frombytes("RGBA", (cap["width"], cap["height"]), cap["bgra"])
    b, g, r, _a = img.split()
    return Image.merge("RGB", (r, g, b))


def downscale(img: Image.Image, max_dim: int) -> Image.Image:
    if max_dim and max(img.size) > max_dim:
        scale = max_dim / max(img.size)
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return img


def grab_screen(rect: Optional[RectLike] = None, monitor: int = 1) -> Image.Image:
    """Grab a screen rect (or a whole monitor) with mss."""
    with mss.mss() as sct:
        if rect is None:
            mon = sct.monitors[monitor]
        else:
            left, top, right, bottom = normalize_rect(rect)
            mon = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def grab_window(hwnd: int) -> Optional[Image.Image]:
    """Grab a whole window with PrintWindow (works while occluded)."""
    cap = winapi.capture_window(int(hwnd))
    if not cap:
        return None
    return bgra_to_pil(cap)


def crop(
    img: Image.Image,
    rect: RectLike,
    pad: int = 0,
    scale: float = 1.0,
    max_dim: int = 1600,
) -> Image.Image:
    """Crop a rect out of an image (coords in that image's own space)."""
    left, top, right, bottom = normalize_rect(rect)
    left -= pad
    top -= pad
    right += pad
    bottom += pad
    left = max(0, left)
    top = max(0, top)
    right = min(img.width, right)
    bottom = min(img.height, bottom)
    if right <= left or bottom <= top:
        raise ValueError(f"empty crop region {rect}")
    out = img.crop((left, top, right, bottom))
    if scale and scale != 1.0:
        out = out.resize(
            (max(1, int(out.width * scale)), max(1, int(out.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return downscale(out, max_dim)


def grab_region(
    rect: RectLike,
    source: str = "screen",
    hwnd: Optional[int] = None,
    pad: int = 0,
    scale: float = 1.0,
    max_dim: int = 1600,
) -> Image.Image:
    """Capture a screen-space rect from the live screen or from a window."""
    if source == "window":
        if not hwnd:
            raise ValueError("source='window' requires hwnd")
        win_img = grab_window(int(hwnd))
        if win_img is None:
            raise RuntimeError("window capture failed (PrintWindow returned no pixels)")
        wr = winapi.get_window_rect(int(hwnd))
        if not wr:
            raise RuntimeError("cannot read window rect")
        left, top, right, bottom = normalize_rect(rect)
        local = (left - wr["left"], top - wr["top"], right - wr["left"], bottom - wr["top"])
        return crop(win_img, local, pad=pad, scale=scale, max_dim=max_dim)

    left, top, right, bottom = normalize_rect(rect)
    img = grab_screen((left - pad, top - pad, right + pad, bottom + pad))
    if scale and scale != 1.0:
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return downscale(img, max_dim)


def save(img: Image.Image, label: str = "crop") -> Path:
    """Save an image to data/vision/ and return the path."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (label or "crop"))[:40] or "crop"
    path = VISION_DIR / f"{safe}_{ts}.png"
    img.save(path, format="PNG")
    return path


def estimate_image_tokens(img: Image.Image) -> int:
    """Rough token estimate for an image (used only for context accounting)."""
    w, h = img.size
    return max(1, (w * h) // 750)


def pixel_diff(a: Image.Image, b: Image.Image, threshold: int = 12) -> dict:
    """Cheap perceptual change detection between two images (no LLM call).

    Returns the number/ratio of pixels whose grayscale difference exceeds
    ``threshold`` and the bounding box of the changed area.
    """
    same_size = a.size == b.size
    if not same_size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    gray = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    hist = gray.histogram()
    total = a.size[0] * a.size[1]
    changed = sum(hist[threshold + 1:])
    mask = gray.point(lambda p: 255 if p > threshold else 0)
    bbox = mask.getbbox()
    return {
        "same_size": same_size,
        "changed_pixels": changed,
        "total_pixels": total,
        "changed_ratio": round(changed / total, 6) if total else 0.0,
        "bbox": bbox,
        "changed": changed > 0,
    }
