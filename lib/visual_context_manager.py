"""Visual context manager for the ProLight vision sub-agent.

The main agent's ``ContextPool`` is text-only and large; feeding it every
screenshot would bloat it and invalidate its DeepSeek prefix cache. The vision
sub-agent therefore keeps its own, much smaller, image-aware context:

  * only the last ``keep_images`` turns carry actual images;
  * older turns are kept as text (question + answer) and, once they exceed
    ``max_turns`` (or the text budget), are summarised into a short ``memory``
    paragraph;
  * a token budget bounds the text part of the context.

This mirrors the layered idea of ``context_manager.ContextPool`` but is
specialised for short-lived, image-heavy perceptual observations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

import tiktoken
from loguru import logger

DEFAULT_MAX_TURNS = 10
DEFAULT_KEEP_IMAGES = 2
DEFAULT_MAX_TEXT_TOKENS = 3000

_tokenizer = None


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        try:
            _tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception as e:  # pragma: no cover
            logger.warning(f"tiktoken unavailable, using char/3: {e}")
            _tokenizer = None
    return _tokenizer


def _count(text: str) -> int:
    tok = _get_tokenizer()
    if tok:
        return len(tok.encode_ordinary(text or ""))
    return max(1, len(text or "") // 3)


@dataclass
class VisualTurn:
    label: str = ""
    query: str = ""
    answer: str = ""
    image_path: str = ""
    image_tokens: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class VisualContext:
    """Rolling, image-aware context for the vision sub-agent."""

    def __init__(
        self,
        max_turns: int = DEFAULT_MAX_TURNS,
        keep_images: int = DEFAULT_KEEP_IMAGES,
        max_text_tokens: int = DEFAULT_MAX_TEXT_TOKENS,
    ) -> None:
        self.max_turns = int(max_turns)
        self.keep_images = max(0, int(keep_images))
        self.max_text_tokens = int(max_text_tokens)
        self.turns: list[VisualTurn] = []
        self.memory: str = ""

    # ── accounting ────────────────────────────────────────────────────────
    def _text_tokens(self) -> int:
        total = _count(self.memory)
        for t in self.turns:
            total += _count(t.label) + _count(t.query) + _count(t.answer)
        return total

    def _image_tokens(self) -> int:
        if self.keep_images <= 0:
            return 0
        return sum(t.image_tokens for t in self.turns[-self.keep_images:])

    def stats(self) -> dict:
        return {
            "turns": len(self.turns),
            "text_tokens": self._text_tokens(),
            "image_tokens": self._image_tokens(),
            "memory_chars": len(self.memory),
            "max_turns": self.max_turns,
            "keep_images": self.keep_images,
        }

    def needs_compression(self) -> bool:
        return len(self.turns) > self.max_turns or self._text_tokens() > self.max_text_tokens

    # ── mutation ──────────────────────────────────────────────────────────
    def add(self, turn: VisualTurn) -> None:
        self.turns.append(turn)

    def clear(self) -> None:
        self.turns.clear()
        self.memory = ""

    # ── message building ──────────────────────────────────────────────────
    def _label_text(self, turn: VisualTurn) -> str:
        prefix = f"[{turn.label}] " if turn.label else ""
        return prefix + turn.query

    def build(self, system_prompt: str, final_user_content) -> list[dict]:
        """Compose the message list for the next API call.

        ``final_user_content`` is the content of the new user message: either a
        plain string or a list of OpenAI content parts (text + image_url).
        """
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        if self.memory:
            messages.append({"role": "assistant", "content": "[Visual memory]\n" + self.memory})

        recent = self.turns[-self.keep_images:] if self.keep_images > 0 else []
        for turn in recent:
            parts: list[dict] = [{"type": "text", "text": self._label_text(turn)}]
            if turn.image_path:
                try:
                    from lib.vision_client import encode_image
                    data_url, _w, _h = encode_image(turn.image_path)
                    parts.append({"type": "image_url", "image_url": {"url": data_url}})
                except Exception as e:
                    logger.warning(f"visual ctx: cannot reload {turn.image_path}: {e}")
            messages.append({"role": "user", "content": parts})
            messages.append({"role": "assistant", "content": turn.answer})

        messages.append({"role": "user", "content": final_user_content})
        return messages

    async def compress(self, summarize: Callable[[str], Awaitable[str]]) -> bool:
        """Summarise the oldest turns into ``memory`` and drop them.

        ``summarize`` is an async ``text -> text`` callable supplied by the
        agent (so this module stays transport-agnostic).
        """
        keep = self.keep_images
        if len(self.turns) <= max(1, keep):
            return False

        old = self.turns[:-keep] if keep > 0 else list(self.turns)
        self.turns = self.turns[-keep:] if keep > 0 else []

        transcript = "\n".join(
            f"- {t.label or 'view'}: Q={t.query} A={t.answer}" for t in old
        )
        prompt = (
            "Compress these earlier visual observations into a short factual memory "
            "(max 6 bullet lines). Keep concrete, lasting states (checked/unchecked, "
            "selected, values, what is where). Drop anything transient.\n\n"
            + transcript
        )
        try:
            summary = await summarize(prompt)
        except Exception as e:
            logger.error(f"visual context compression failed: {e}")
            summary = transcript[:800]

        self.memory = (self.memory + "\n" + summary).strip() if self.memory else summary
        logger.info(f"visual context compressed: {len(old)} turn(s) -> memory")
        return True
