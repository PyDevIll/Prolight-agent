"""Dedicated vision sub-agent for ProLight-agent.

Why a separate agent?
  * It uses a **separate API key** (HELPER by default) to isolate rate limits
    and accounting from the main reasoning model.
  * Calls are **one-shot**: each request carries only the system prompt and the
    current crop, so a stale frame can never bias a fresh observation.
  * It keeps a registry of *watches* (a labelled region + its last image), so a
    follow-up can compare BEFORE vs AFTER or detect changes with a cheap pixel
    diff.

It looks at small regions (a control's rect, an arbitrary area) rather than
whole windows: cheaper (far fewer image tokens) and far more accurate.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from loguru import logger
from openai import AsyncOpenAI
from PIL import Image

from lib import image_ops
from lib import winapi
from lib.vision_client import DEEPSEEK_BASE_URL, analyze_messages, encode_image

DEFAULT_SYSTEM_PROMPT = """You are ProLight's visual perception module for a Windows desktop.
You receive small, cropped screenshots - usually a single control or a small region.
Sometimes you receive two images labelled BEFORE and AFTER.

Rules:
- Report ONLY what is visible. Never guess or invent.
- Be concise: one to four sentences, or a short list. No preamble.
- For controls, state the concrete state: checked/unchecked, selected/not selected,
  enabled/disabled, expanded/collapsed, on/off, the text/value, or a count.
- For BEFORE/AFTER, state exactly what changed. If nothing changed, say "no change".
- If the image is too small or blurry to be sure, say "unclear".
- The exact image size in pixels is given in the prompt. Never guess or invent
  the image size or scale; trust the stated numbers.
- Never output pixel coordinates. Describe positions qualitatively (e.g.
  "top-left", "next to the Save button"). Pixel geometry is obtained
  deterministically by other tools, never from you."""


def _with_size(query: str, width: int, height: int) -> str:
    """Prepend the true image dimensions so the model cannot hallucinate scale."""
    return (
        f"IMAGE SIZE: exactly {width}x{height} pixels (origin at top-left, 0,0).\n"
        f"{query}"
    )


def _locate_prompt(query: str, width: int, height: int) -> str:
    """Ask for element boxes as FRACTIONS (the model is reliable at those)."""
    return (
        f"IMAGE SIZE: exactly {width}x{height} pixels (origin at top-left, 0,0).\n"
        f"{query}\n\n"
        "Return ONLY minified JSON of exactly this shape:\n"
        '{"elements":[{"label":"short name","box":[x0,y0,x1,y1],"confidence":0.0}]}\n'
        "box MUST be FRACTIONS of the image, each 0.0-1.0, origin top-left; "
        "never pixels. Include only the elements asked for. No prose, no code fence."
    )


def _extract_json(text: Optional[str]):
    """Best-effort JSON object extraction from a model answer."""
    if not text:
        return None
    t = re.sub(r"```(?:json)?", "", text, flags=re.I).strip().strip("`").strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _degenerate_snap(approx, snapped) -> bool:
    """True when a snapped box is unusable (collapsed sliver or far too small).

    ``snap_box`` can latch onto a tiny sub-region of a multi-coloured element;
    in that case the model's fractional box is the better answer.
    """
    try:
        aw, ah = approx[2] - approx[0], approx[3] - approx[1]
        sw, sh = snapped[2] - snapped[0], snapped[3] - snapped[1]
    except Exception:
        return True
    if sw <= 2 or sh <= 2:
        return True
    if aw > 0 and ah > 0 and (sw * sh) < 0.25 * (aw * ah):
        return True
    return False


class VisionAgent:
    """A small, image-aware assistant on its own API key (one-shot calls)."""

    def __init__(
        self,
        name: str = "VISION",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        timeout: float = 120.0,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        api_key = (
            os.environ.get(f"DEEPSEEK_API_KEY_{name}")
            or os.environ.get("DEEPSEEK_API_KEY_HELPER")
            or os.environ.get("DEEPSEEK_API_KEY")
            or ""
        )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=timeout,
            max_retries=1,

        )
        self.watches: dict[str, dict] = {}
        logger.info(f"VisionAgent '{name}' initialized (key={'set' if api_key else 'MISSING'})")

    # ── low-level completion ──────────────────────────────────────────────

    async def _complete(self, parts, max_tokens: int = 1024) -> str:
        """One-shot completion: only the system prompt and this request's parts."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": parts},
        ]
        return await analyze_messages(messages, max_tokens=max_tokens, client=self._client)

    async def ask_once(self, image, query: str, max_tokens: int = 1024) -> str:
        """One-shot analysis with no history (used by win_snapshot / vision_look)."""
        data_url, w, h = encode_image(image)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _with_size(query, w, h)},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        return await analyze_messages(messages, max_tokens=max_tokens, client=self._client)

    # ── region resolution / capture ───────────────────────────────────────
    async def _resolve_region(
        self,
        rect=None,
        hwnd: Optional[int] = None,
        control: Optional[dict] = None,
    ) -> Optional[dict]:
        if control and hwnd:
            from lib import ui_tree  # lazy: avoids importing pywinauto unless needed
            matches = await ui_tree.find_controls(int(hwnd), **control)
            if not matches:
                return None
            r = matches[0].get("rect")
            if not r:
                return None
            return {"rect": image_ops.normalize_rect(r), "hwnd": int(hwnd), "control": matches[0]}
        if rect is not None:
            return {"rect": image_ops.normalize_rect(rect), "hwnd": int(hwnd) if hwnd else None}
        if hwnd:
            wr = winapi.get_window_rect(int(hwnd))
            if not wr:
                return None
            return {"rect": (wr["left"], wr["top"], wr["right"], wr["bottom"]), "hwnd": int(hwnd)}
        return None

    def _pick_source(self, source: str, hwnd: Optional[int], rect) -> str:
        if source in ("screen", "window"):
            return source
        # auto: prefer window capture when the region lies inside the window
        if hwnd:
            wr = winapi.get_window_rect(int(hwnd))
            if wr:
                left, top, right, bottom = rect
                if left >= wr["left"] and top >= wr["top"] and right <= wr["right"] and bottom <= wr["bottom"]:
                    return "window"
        return "screen"

    # ── public API ────────────────────────────────────────────────────────
    async def look(
        self,
        *,
        rect=None,
        hwnd: Optional[int] = None,
        control: Optional[dict] = None,
        query: str = "What is shown here? Describe the visible state.",
        label: str = "",
        source: str = "auto",
        pad: int = 0,
        scale: float = 1.0,
        max_dim: int = 1600,
        max_tokens: int = 1024,
    ) -> dict:
        """Capture a region (or control) and ask the vision model about it."""
        # Reuse a previously watched region when only its label is given.
        if rect is None and hwnd is None and control is None and label and label in self.watches:
            w = self.watches[label]
            rect = w.get("rect")
            hwnd = w.get("hwnd")
            source = w.get("source", source)
            pad = w.get("pad", pad)
            scale = w.get("scale", scale)
            max_dim = w.get("max_dim", max_dim)
        region = await self._resolve_region(rect=rect, hwnd=hwnd, control=control)
        if not region:
            return {"ok": False, "error": "no region resolved (need rect, hwnd or control+hwnd)"}

        rect_s, hwnd_s = region["rect"], region.get("hwnd")
        src = self._pick_source(source, hwnd_s, rect_s)
        try:
            img, meta = image_ops.grab_region_with_meta(
                rect_s, source=src, hwnd=hwnd_s, pad=pad, scale=scale, max_dim=max_dim
            )
        except Exception as e:
            return {"ok": False, "error": f"capture failed: {e}"}

        path = image_ops.save(img, label or "look")
        data_url, w, h = encode_image(img, max_dim=max_dim)
        parts = [
            {"type": "text", "text": _with_size(query, w, h)},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        try:
            answer = await self._complete(parts, max_tokens=max_tokens)
        except Exception as e:
            return {"ok": False, "error": f"vision call failed: {e}", "path": str(path)}

        if label:
            self.watches[label] = {
                "label": label, "rect": rect_s, "hwnd": hwnd_s, "source": src,
                "pad": pad, "scale": scale, "max_dim": max_dim, "meta": meta,
                "image_path": str(path), "answer": answer, "query": query,
            }

        logger.info(f"vision look[{label or '-'}] {w}x{h} via {src}: {answer[:80]!r}")
        return {
            "ok": True, "label": label, "rect": rect_s, "source": src,
            "path": str(path), "image_size": {"width": w, "height": h},
            "screen_rect": meta["screen_rect"],
            "answer": answer,
        }

    async def locate(
        self,
        *,
        rect=None,
        hwnd: Optional[int] = None,
        control: Optional[dict] = None,
        query: str = "Locate the main interactive elements.",
        label: str = "",
        source: str = "auto",
        pad: int = 0,
        scale: float = 1.0,
        max_dim: int = 1600,
        snap: bool = True,
        max_tokens: int = 1024,
    ) -> dict:
        """Return APPROXIMATE element boxes, then snap them to exact pixels.

        The model is asked for boxes as *fractions* of the crop (which it does
        reliably); code converts them to pixels and refines each with
        ``image_ops.snap_box``. Boxes are hints — exact geometry still comes
        from UIA/OCR/colour search. Returns ``elements`` with ``box_frac``,
        ``image_bbox``, ``snapped_bbox`` and ``screen_*`` coordinates.
        """
        region = await self._resolve_region(rect=rect, hwnd=hwnd, control=control)
        if not region:
            return {"ok": False, "error": "no region resolved (need rect, hwnd or control+hwnd)"}
        rect_s, hwnd_s = region["rect"], region.get("hwnd")
        src = self._pick_source(source, hwnd_s, rect_s)
        try:
            img, meta = image_ops.grab_region_with_meta(
                rect_s, source=src, hwnd=hwnd_s, pad=pad, scale=scale, max_dim=max_dim
            )
        except Exception as e:
            return {"ok": False, "error": f"capture failed: {e}"}

        path = image_ops.save(img, label or "locate")
        data_url, w, h = encode_image(img, max_dim=max_dim)
        parts = [
            {"type": "text", "text": _locate_prompt(query, w, h)},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        try:
            answer = await self._complete(parts, max_tokens=max_tokens)
        except Exception as e:
            return {"ok": False, "error": f"vision call failed: {e}", "path": str(path)}

        data = _extract_json(answer)
        if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
            return {"ok": False, "error": "could not parse element boxes",
                    "answer": answer, "path": str(path)}

        elements = []
        for el in data["elements"][:32]:
            box = el.get("box") if isinstance(el, dict) else None
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            try:
                fx0, fy0, fx1, fy1 = (float(v) for v in box)
            except (TypeError, ValueError):
                continue
            if max(abs(fx0), abs(fy0), abs(fx1), abs(fy1)) > 1.5:  # 0..100 given
                fx0, fy0, fx1, fy1 = fx0 / 100, fy0 / 100, fx1 / 100, fy1 / 100
            fx0, fx1 = sorted((max(0.0, min(1.0, fx0)), max(0.0, min(1.0, fx1))))
            fy0, fy1 = sorted((max(0.0, min(1.0, fy0)), max(0.0, min(1.0, fy1))))
            ix0, iy0, ix1, iy1 = fx0 * w, fy0 * h, fx1 * w, fy1 * h
            approx = [int(round(ix0)), int(round(iy0)), int(round(ix1)), int(round(iy1))]
            snapped = image_ops.snap_box(img, approx) if snap else approx
            # Reject a degenerate snap (a collapsed sliver or one much smaller
            # than the model's box) — the model's fraction box is more reliable.
            if snap and _degenerate_snap(approx, snapped):
                snapped = approx
            sx0, sy0 = image_ops.map_image_point(meta, snapped[0], snapped[1])
            sx1, sy1 = image_ops.map_image_point(meta, snapped[2], snapped[3])
            elements.append({
                "label": (el.get("label") or "") if isinstance(el, dict) else "",
                "box_frac": [round(fx0, 4), round(fy0, 4), round(fx1, 4), round(fy1, 4)],
                "image_bbox": approx,
                "snapped_bbox": snapped,
                "screen_bbox": [sx0, sy0, sx1, sy1],
                "screen_center": [(sx0 + sx1) // 2, (sy0 + sy1) // 2],
                "confidence": el.get("confidence") if isinstance(el, dict) else None,
                "snapped": snapped != approx,
            })

        logger.info(f"vision locate[{label or '-'}] {w}x{h}: {len(elements)} element(s)")
        return {
            "ok": True, "label": label, "source": src, "path": str(path),
            "image_size": {"width": w, "height": h},
            "screen_rect": meta["screen_rect"],
            "elements": elements, "answer": answer,
        }

    async def compare(self, label: str, query: str = "", max_tokens: int = 1024) -> dict:
        """Re-capture a labelled region and compare it with its last image."""
        watch = self.watches.get(label)
        if not watch:
            return {"ok": False, "error": f"no watch named {label!r} (call vision_look first)"}
        try:
            img = image_ops.grab_region(
                watch["rect"], source=watch["source"], hwnd=watch.get("hwnd"),
                pad=watch.get("pad", 0), scale=watch.get("scale", 1.0),
                max_dim=watch.get("max_dim", 1600),
            )
        except Exception as e:
            return {"ok": False, "error": f"capture failed: {e}"}

        path = image_ops.save(img, f"{label}_after")
        q = query or (
            f"Compare BEFORE and AFTER for region '{label}'. Did anything change? "
            "State the concrete change (checked/unchecked, selected, text/value, count)."
        )
        try:
            before_url, _w, _h = encode_image(watch["image_path"])
            after_url, _w2, _h2 = encode_image(img)
        except Exception as e:
            return {"ok": False, "error": f"encode failed: {e}"}

        diff = None
        try:
            before_img = Image.open(watch["image_path"]).convert("RGB")
            diff = image_ops.pixel_diff(before_img, img)
        except Exception:
            pass

        parts = [
            {"type": "text", "text": q},
            {"type": "text", "text": "BEFORE:"},
            {"type": "image_url", "image_url": {"url": before_url}},
            {"type": "text", "text": "AFTER:"},
            {"type": "image_url", "image_url": {"url": after_url}},
        ]
        try:
            answer = await self._complete(parts, max_tokens=max_tokens)
        except Exception as e:
            return {"ok": False, "error": f"vision call failed: {e}", "pixel_diff": diff}

        watch["image_path"] = str(path)
        watch["answer"] = answer
        logger.info(f"vision compare[{label}]: changed={bool(diff and diff.get('changed'))} -> {answer[:80]!r}")
        return {"ok": True, "label": label, "answer": answer, "pixel_diff": diff, "path": str(path)}

    async def changed(self, label: str, threshold: int = 12) -> dict:
        """Cheap pixel-level check: has the region changed since the last look?

        No LLM call. Use it to decide whether a vision call is even needed.
        """
        watch = self.watches.get(label)
        if not watch:
            return {"ok": False, "error": f"no watch named {label!r} (call vision_look first)"}
        try:
            img = image_ops.grab_region(
                watch["rect"], source=watch["source"], hwnd=watch.get("hwnd"),
                pad=watch.get("pad", 0), scale=watch.get("scale", 1.0),
                max_dim=watch.get("max_dim", 1600),
            )
            before = Image.open(watch["image_path"]).convert("RGB")
        except Exception as e:
            return {"ok": False, "error": f"capture failed: {e}"}
        diff = image_ops.pixel_diff(before, img, threshold=threshold)
        return {"ok": True, "label": label, **diff}

    def watches_list(self) -> list[dict]:
        return [
            {
                "label": w["label"], "rect": w["rect"], "source": w["source"],
                "hwnd": w.get("hwnd"), "image_path": w["image_path"],
                "last_answer": w.get("answer", "")[:200],
            }
            for w in self.watches.values()
        ]

    def forget(self, label: Optional[str] = None) -> int:
        """Drop one watch (or all when label is None). Returns how many were dropped."""
        if label is None:
            n = len(self.watches)
            self.watches.clear()
            return n
        return 1 if self.watches.pop(label, None) is not None else 0

    def reset(self) -> None:
        self.watches.clear()


_vision_agent: Optional[VisionAgent] = None


def get_vision_agent() -> VisionAgent:
    global _vision_agent
    if _vision_agent is None:
        _vision_agent = VisionAgent()
    return _vision_agent
