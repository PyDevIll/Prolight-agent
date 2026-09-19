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
import numpy as np
from PIL import Image
from loguru import logger
from scipy import ndimage
from scipy.signal import fftconvolve

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


def grab_region_with_meta(
    rect: RectLike,
    source: str = "screen",
    hwnd: Optional[int] = None,
    pad: int = 0,
    scale: float = 1.0,
    max_dim: int = 1600,
) -> tuple[Image.Image, dict]:
    """Like :func:`grab_region`, but also returns the mapping back to screen.

    ``meta`` lets you convert a pixel in the returned (possibly scaled) image
    back to a physical screen coordinate via :func:`map_image_point`, which is
    essential because vision/capture may downscale or zoom the crop.
    """
    left, top, right, bottom = normalize_rect(rect)
    cap_rect = (left - pad, top - pad, right + pad, bottom + pad)
    img = grab_region(rect, source=source, hwnd=hwnd, pad=pad, scale=scale, max_dim=max_dim)
    cw = cap_rect[2] - cap_rect[0]
    ch = cap_rect[3] - cap_rect[1]
    meta = {
        "screen_rect": cap_rect,
        "image_size": {"width": img.width, "height": img.height},
        "scale_x": (cw / img.width) if img.width else 1.0,
        "scale_y": (ch / img.height) if img.height else 1.0,
    }
    return img, meta


def map_image_point(meta: dict, x: float, y: float) -> tuple[int, int]:
    """Map an (x, y) in a captured image back to physical screen pixels."""
    sr = meta["screen_rect"]
    sx = sr[0] + float(x) * meta.get("scale_x", 1.0)
    sy = sr[1] + float(y) * meta.get("scale_y", 1.0)
    return int(round(sx)), int(round(sy))


def _parse_color(color) -> tuple[int, int, int]:
    """Accept [r,g,b] / (r,g,b) / '#RRGGBB' / 'r,g,b'."""
    if isinstance(color, str):
        s = color.strip().lstrip("#")
        if "," in s:
            parts = [int(p) for p in s.split(",")]
        elif len(s) == 6:
            parts = [int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)]
        else:
            raise ValueError(f"Cannot parse color {color!r} (use [r,g,b] or '#RRGGBB')")
    else:
        parts = [int(c) for c in color]
    if len(parts) < 3:
        raise ValueError("color needs 3 components (r,g,b)")
    return parts[0], parts[1], parts[2]


def color_mask(img: Image.Image, color, tolerance: int = 16) -> Image.Image:
    """Binary mask (mode 'L', 255 = match) of pixels within ``tolerance``."""
    r, g, b = _parse_color(color)
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    diff = np.maximum(
        np.maximum(np.abs(arr[:, :, 0] - r), np.abs(arr[:, :, 1] - g)),
        np.abs(arr[:, :, 2] - b),
    )
    mask = (diff <= int(tolerance)).astype(np.uint8) * 255
    return Image.fromarray(mask, mode="L")


def find_color_regions(
    img: Image.Image,
    color,
    tolerance: int = 16,
    min_area: int = 4,
    max_regions: int = 64,
    region: Optional[RectLike] = None,
) -> list[dict]:
    """Deterministically locate connected blobs of a colour (no vision model).

    Vectorised with numpy; connected components via ``scipy.ndimage.label``
    (4-connectivity). Returns blobs sorted by area (largest first) with
    ``bbox``/``center``/``width``/``height``/``area`` in the coordinates of
    ``img`` (or of ``region`` when given, still reported in ``img`` space).
    """
    rgb = img.convert("RGB")
    ox = oy = 0
    if region is not None:
        l, t, r, b = normalize_rect(region)
        l, t = max(0, l), max(0, t)
        r, b = min(rgb.width, r), min(rgb.height, b)
        if r <= l or b <= t:
            return []
        ox, oy = l, t
        rgb = rgb.crop((l, t, r, b))

    rr, gg, bb = _parse_color(color)
    arr = np.asarray(rgb, dtype=np.int16)
    diff = np.maximum(
        np.maximum(np.abs(arr[:, :, 0] - rr), np.abs(arr[:, :, 1] - gg)),
        np.abs(arr[:, :, 2] - bb),
    )
    binary = diff <= int(tolerance)
    if not binary.any():
        return []

    labels, n = ndimage.label(binary)
    if n == 0:
        return []

    ys, xs = np.nonzero(binary)
    lab = labels[ys, xs]
    area = np.bincount(lab, minlength=n + 1)
    sumx = np.bincount(lab, weights=xs, minlength=n + 1)
    sumy = np.bincount(lab, weights=ys, minlength=n + 1)
    objects = ndimage.find_objects(labels)

    results: list[dict] = []
    for label in range(1, n + 1):
        a = int(area[label])
        if a < min_area:
            continue
        sl = objects[label - 1]
        miny, minx = int(sl[0].start), int(sl[1].start)
        maxy, maxx = int(sl[0].stop) - 1, int(sl[1].stop) - 1
        cx = int(round(float(sumx[label]) / a))
        cy = int(round(float(sumy[label]) / a))
        results.append({
            "bbox": [minx + ox, miny + oy, maxx + ox, maxy + oy],
            "center": [cx + ox, cy + oy],
            "width": maxx - minx + 1,
            "height": maxy - miny + 1,
            "area": a,
        })
    results.sort(key=lambda d: d["area"], reverse=True)
    return results[:max_regions]


def find_template(
    img: Image.Image,
    template,
    region: Optional[RectLike] = None,
    threshold: float = 30.0,
    max_results: int = 8,
    max_positions: int = 40000,
) -> list[dict]:
    """Locate a template image inside ``img`` (numpy FFT, exact refinement).

    Uses FFT-based sum-of-squared-differences to score every offset in one pass,
    then computes the exact mean absolute pixel difference at the best
    candidates. ``threshold`` is the max mean absolute difference to accept
    (0 = identical); lower is stricter. ``max_positions`` is kept for API
    compatibility and no longer bounds the search. Returns matches with
    ``score`` and ``bbox``/``center`` in ``img`` coordinates.
    """
    base = img.convert("RGB")
    ox = oy = 0
    if region is not None:
        l, t, r, b = normalize_rect(region)
        l, t = max(0, l), max(0, t)
        r, b = min(base.width, r), min(base.height, b)
        if r <= l or b <= t:
            return []
        ox, oy = l, t
        base = base.crop((l, t, r, b))

    tmpl = template.convert("RGB") if isinstance(template, Image.Image) else Image.open(str(template)).convert("RGB")
    W, H = base.size
    tw, th = tmpl.size
    if tw < 1 or th < 1 or tw > W or th > H:
        return []

    base_a = np.asarray(base, dtype=np.float64)
    tmpl_a = np.asarray(tmpl, dtype=np.float64)

    # SSD(x,y) = sum(window^2) - 2*corr + sum(template^2).
    # Window energy via a summed-area table (exact, O(N)); correlation via FFT.
    sq = (base_a ** 2).sum(axis=2)
    integral = np.pad(sq.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    local_energy = (
        integral[th:, tw:] - integral[:-th, tw:]
        - integral[th:, :-tw] + integral[:-th, :-tw]
    )
    base32 = base_a.astype(np.float32)
    tmpl32 = tmpl_a.astype(np.float32)
    corr = np.zeros_like(local_energy, dtype=np.float32)
    for c in range(3):
        corr += fftconvolve(base32[:, :, c], tmpl32[::-1, ::-1, c], mode="valid")
    ssd = local_energy - 2.0 * corr + float((tmpl_a ** 2).sum())
    np.clip(ssd, 0.0, None, out=ssd)

    flat = ssd.ravel()
    k = min(flat.size, max(16, int(max_results) * 8))
    idx = np.argpartition(flat, k - 1)[:k]
    idx = idx[np.argsort(flat[idx])]

    ow = W - tw + 1
    results: list[dict] = []
    seen: list[tuple[int, int]] = []
    for i in idx:
        y, x = divmod(int(i), ow)
        if any(abs(x - a) < tw and abs(y - b) < th for a, b in seen):
            continue
        win = base_a[y:y + th, x:x + tw, :]
        mad = float(np.abs(win - tmpl_a).mean())
        if mad > threshold:
            continue
        seen.append((x, y))
        results.append({
            "score": round(mad, 2),
            "bbox": [x + ox, y + oy, x + tw + ox, y + th + oy],
            "center": [x + tw // 2 + ox, y + th // 2 + oy],
        })
        if len(results) >= max_results:
            break
    return results


def _clamp_box(box, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0 = max(0, min(w - 1, x0))
    y0 = max(0, min(h - 1, y0))
    x1 = max(x0 + 1, min(w, x1))
    y1 = max(y0 + 1, min(h, y1))
    return x0, y0, x1, y1


def _pick_component(labels, n, box, w, h, min_area):
    """Choose the component that best matches ``box``: prefer those not touching
    the search-window border (the background usually does), then the largest
    overlap with the approximate box, then the nearest centroid."""
    x0, y0, x1, y1 = box
    boxmask = np.zeros((h, w), dtype=bool)
    boxmask[y0:y1, x0:x1] = True
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    best, best_key = None, None
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        comp = labels[sl] == i
        area = int(comp.sum())
        if area < min_area:
            continue
        ys, xs = sl
        touches = xs.start <= 0 or ys.start <= 0 or xs.stop >= w or ys.stop >= h
        overlap = int(np.logical_and(comp, boxmask[sl]).sum())
        rcx, rcy = (xs.start + xs.stop) / 2.0, (ys.start + ys.stop) / 2.0
        d = (rcx - cx) ** 2 + (rcy - cy) ** 2
        key = (1 if touches else 0, -overlap, d)
        if best_key is None or key < best_key:
            best_key, best = key, (xs.start, ys.start, xs.stop, ys.stop)
    return best


def _snap_color(arr, box, tolerance, min_area) -> Optional[tuple[int, int, int, int]]:
    """Tight bbox of the component nearest ``box`` whose colour matches the
    median colour of the box's *central* region (avoids sampling background)."""
    x0, y0, x1, y1 = box
    h, w = arr.shape[:2]
    cx0 = x0 + (x1 - x0) // 5
    cx1 = x1 - (x1 - x0) // 5
    cy0 = y0 + (y1 - y0) // 5
    cy1 = y1 - (y1 - y0) // 5
    patch = arr[cy0:cy1, cx0:cx1].reshape(-1, 3)
    if patch.size == 0:
        return None
    color = np.median(patch, axis=0)
    tol = int(tolerance) if tolerance is not None else 40
    mask = np.max(np.abs(arr - color), axis=2) <= tol
    if not mask.any():
        return None
    labels, n = ndimage.label(mask)
    if n == 0:
        return None
    return _pick_component(labels, n, box, w, h, min_area)


def _snap_edge(arr, box) -> Optional[tuple[int, int, int, int]]:
    """Tight bbox of the edge component overlapping ``box`` most (fallback when
    the element is multi-coloured / has no dominant fill)."""
    x0, y0, x1, y1 = box
    gray = (arr[:, :, 0] * 299 + arr[:, :, 1] * 587 + arr[:, :, 2] * 114) // 1000
    gy, gx = np.gradient(gray.astype(np.float32))
    mag = np.hypot(gx, gy)
    thr = float(mag.mean() + 2.0 * mag.std())
    mask = mag > thr
    if not mask.any():
        return None
    mask = ndimage.binary_dilation(mask, iterations=1)
    labels, n = ndimage.label(mask)
    if n == 0:
        return None
    return _pick_component(labels, n, box, arr.shape[1], arr.shape[0], 1)


def snap_box(
    img: Image.Image,
    approx_bbox,
    pad: int = 8,
    method: str = "auto",
    tolerance: Optional[int] = None,
    min_area: int = 4,
) -> list[int]:
    """Snap an approximate box to the tight bounding box of the element.

    The vision model is good at *fractions* but not at exact pixels, so a
    fractional box is converted to an approximate pixel box and then refined
    here deterministically: search a padded window around ``approx_bbox`` and
    return the tight box of the colour/edge component nearest it.

    ``method``: "auto" (colour then edge), "color", or "edge". Returns
    ``[x0,y0,x1,y1]`` in ``img`` coordinates; falls back to the clamped
    approximate box when nothing can be snapped.
    """
    rgb = img.convert("RGB")
    W, H = rgb.size
    ax0, ay0, ax1, ay1 = _clamp_box(approx_bbox, W, H)
    wx0, wy0 = max(0, ax0 - int(pad)), max(0, ay0 - int(pad))
    wx1, wy1 = min(W, ax1 + int(pad)), min(H, ay1 + int(pad))
    arr = np.asarray(rgb.crop((wx0, wy0, wx1, wy1)), dtype=np.int16)
    box = (ax0 - wx0, ay0 - wy0, ax1 - wx0, ay1 - wy0)

    snapped = None
    if method in ("auto", "color"):
        snapped = _snap_color(arr, box, tolerance, min_area)
    if snapped is None and method in ("auto", "edge"):
        snapped = _snap_edge(arr, box)
    if snapped is None:
        return [ax0, ay0, ax1, ay1]
    return [snapped[0] + wx0, snapped[1] + wy0, snapped[2] + wx0, snapped[3] + wy0]


def pixel_diff(a: Image.Image, b: Image.Image, threshold: int = 12) -> dict:
    """Cheap perceptual change detection between two images (no LLM call).

    Returns the number/ratio of pixels whose grayscale difference exceeds
    ``threshold`` and the bounding box of the changed area.
    """
    same_size = a.size == b.size
    if not same_size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    aa = np.asarray(a.convert("RGB"), dtype=np.int32)
    bb = np.asarray(b.convert("RGB"), dtype=np.int32)
    d = np.abs(aa - bb)
    # Luma weights match PIL's convert("L") so the threshold keeps its meaning.
    # int32 is required: 255*587 would overflow int16.
    gray = (d[:, :, 0] * 299 + d[:, :, 1] * 587 + d[:, :, 2] * 114) // 1000
    mask = gray > int(threshold)
    changed = int(mask.sum())
    total = int(mask.size)
    bbox = None
    if changed:
        ys, xs = np.nonzero(mask)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return {
        "same_size": same_size,
        "changed_pixels": changed,
        "total_pixels": total,
        "changed_ratio": round(changed / total, 6) if total else 0.0,
        "bbox": bbox,
        "changed": changed > 0,
    }
