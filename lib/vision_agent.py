"""Dedicated vision sub-agent for ProLight-agent.

Why a separate agent?
  * It keeps its **own** message history, so large screenshots never enter the
    main agent's context and never invalidate its DeepSeek prefix cache.
  * It uses a **separate API key** (HELPER by default) to isolate rate limits
    and accounting from the main reasoning model.
  * It holds short-lived **visual memory** ("was this checkbox ticked before?")
    and a registry of *watches* (a labelled region + its last image), so a
    follow-up can compare BEFORE vs AFTER or detect changes with a cheap pixel
    diff.

It looks at small regions (a control's rect, an arbitrary area) rather than
whole windows: cheaper (far fewer image tokens) and far more accurate.
"""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger
from openai import AsyncOpenAI
from PIL import Image

from lib import image_ops
from lib import winapi
from lib.vision_client import DEEPSEEK_BASE_URL, VISION_MODEL, analyze_messages, encode_image
from lib.visual_context_manager import VisualContext, VisualTurn

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
- Do not output pixel coordinates unless explicitly asked; if asked, give one integer pair."""


class VisionAgent:
    """A small, image-aware assistant on its own key and context."""

    def __init__(
        self,
        name: str = "VISION",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_turns: int = 10,
        keep_images: int = 2,
        max_text_tokens: int = 3000,
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
        self.context = VisualContext(
            max_turns=max_turns, keep_images=keep_images, max_text_tokens=max_text_tokens
        )
        self.watches: dict[str, dict] = {}
        logger.info(f"VisionAgent '{name}' initialized (key={'set' if api_key else 'MISSING'})")

    # ── low-level completion ──────────────────────────────────────────────
    async def _summarize(self, prompt: str) -> str:
        resp = await self._client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()

    async def _complete(self, parts, max_tokens: int = 1024) -> str:
        if self.context.needs_compression():
            await self.context.compress(self._summarize)
        messages = self.context.build(self.system_prompt, parts)
        return await analyze_messages(messages, max_tokens=max_tokens, client=self._client)

    async def ask_once(self, image, query: str, max_tokens: int = 1024) -> str:
        """One-shot analysis with no history (used by win_see / vision_analyze)."""
        data_url, _w, _h = encode_image(image)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": query},
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
        remember: bool = True,
        max_tokens: int = 1024,
    ) -> dict:
        """Capture a region (or control) and ask the vision model about it."""
        region = await self._resolve_region(rect=rect, hwnd=hwnd, control=control)
        if not region:
            return {"ok": False, "error": "no region resolved (need rect, hwnd or control+hwnd)"}

        rect_s, hwnd_s = region["rect"], region.get("hwnd")
        src = self._pick_source(source, hwnd_s, rect_s)
        try:
            img = image_ops.grab_region(rect_s, source=src, hwnd=hwnd_s, pad=pad, scale=scale, max_dim=max_dim)
        except Exception as e:
            return {"ok": False, "error": f"capture failed: {e}"}

        path = image_ops.save(img, label or "look")
        data_url, w, h = encode_image(img, max_dim=max_dim)
        parts = [
            {"type": "text", "text": query},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        try:
            answer = await self._complete(parts, max_tokens=max_tokens)
        except Exception as e:
            return {"ok": False, "error": f"vision call failed: {e}", "path": str(path)}

        turn = VisualTurn(
            label=label, query=query, answer=answer,
            image_path=str(path), image_tokens=image_ops.estimate_image_tokens(img),
        )
        if remember:
            self.context.add(turn)
        if label:
            self.watches[label] = {
                "label": label, "rect": rect_s, "hwnd": hwnd_s, "source": src,
                "pad": pad, "scale": scale, "max_dim": max_dim,
                "image_path": str(path), "answer": answer, "query": query,
                "image_tokens": turn.image_tokens,
            }

        logger.info(f"vision look[{label or '-'}] {w}x{h} via {src}: {answer[:80]!r}")
        return {
            "ok": True, "label": label, "rect": rect_s, "source": src,
            "path": str(path), "image_size": {"width": w, "height": h},
            "answer": answer, "context": self.context.stats(),
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
        self.context.add(VisualTurn(
            label=label, query=q, answer=answer,
            image_path=str(path), image_tokens=image_ops.estimate_image_tokens(img),
        ))
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
        self.context.clear()
        self.watches.clear()


_vision_agent: Optional[VisionAgent] = None


def get_vision_agent() -> VisionAgent:
    global _vision_agent
    if _vision_agent is None:
        _vision_agent = VisionAgent()
    return _vision_agent
