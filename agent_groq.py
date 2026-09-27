"""Groq provider for ProLight-agent.

Runs the MASTERMIND reasoning loop and the vision sub-agent on Groq
(``AsyncGroq``), while the HELPER (context compression) can stay on DeepSeek.
Activated with ``PROLIGHT_LLM=groq`` (see ``app.start_app``).

Why a separate module (mirrors ``agent.py`` for DeepSeek):
  * Groq's free/developer tier is rate-limited hard (RPM/RPD/TPM/TPD), so a
    client-side :class:`GroqRateLimiter` smooths requests and honours 429
    ``retry-after`` before the server does.
  * The context budget must fit Groq's tiny TPM, not DeepSeek's 300K — so the
    ``ContextPool`` is re-budgeted here.

Model: ``qwen/qwen3.8-27b`` (text+image input, tools + reasoning), override with
``GROQ_MODEL``. Defaults for the limits are the documented free tier
(30 RPM / 1K RPD / 8K TPM / 200K TPD); override with ``GROQ_RPM`` / ``GROQ_RPD``
/ ``GROQ_TPM`` / ``GROQ_TPD``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

from loguru import logger

from agent import Agent, construct_history
from context_manager import ContextPool, count_tokens
from tool_registry import get_registry

GROQ_MODEL = "qwen/qwen3.8-27b"

# Free-tier defaults (override via env).
GROQ_RPM = 30
GROQ_RPD = 1000
GROQ_TPM = 8000
GROQ_TPD = 200000

# Context/output sizing must respect the tiny TPM (prompt + completion count).
GROQ_MAX_TOKENS = 5000          # ContextPool budget (assembled prompt layers)
GROQ_MAX_OUTPUT_TOKENS = 1500   # per completion
GROQ_MAX_ATTEMPTS = 6           # 429/5xx retry budget
GROQ_IMAGE_TOKEN_EST = 1100     # rough tokens per image for the limiter


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(float(os.environ.get(name, default))))
    except Exception:
        return int(default)


def provider_from_env() -> str:
    """Which LLM provider to use: ``groq`` or ``deepseek`` (default)."""
    return (os.environ.get("PROLIGHT_LLM")
            or os.environ.get("LLM_PROVIDER")
            or "deepseek").strip().lower()


# ── rate limiting ─────────────────────────────────────────────────────────
class GroqRateLimiter:
    """A sliding-window client-side limiter for RPM/RPD/TPM/TPD.

    Best-effort smoothing *in addition to* the server's enforcement: it reserves
    an estimated token budget per call and adjusts it with the real
    ``usage.total_tokens`` afterwards. A single request larger than ``tpm`` can
    never fit, so it is allowed through (the server may still 429).
    """

    def __init__(self, rpm: int = GROQ_RPM, rpd: int = GROQ_RPD,
                 tpm: int = GROQ_TPM, tpd: int = GROQ_TPD) -> None:
        self.rpm = int(rpm)
        self.rpd = int(rpd)
        self.tpm = int(tpm)
        self.tpd = int(tpd)
        self._events: list[list] = []   # [timestamp, tokens]
        self._lock = asyncio.Lock()

    def _compute_wait(self, tokens: int, now: float) -> float:
        e60 = [e for e in self._events if now - e[0] <= 60.0]
        e24 = [e for e in self._events if now - e[0] <= 86400.0]
        wait = 0.0
        if len(e60) >= self.rpm:
            wait = max(wait, 60.0 - (now - e60[0][0]) + 0.05)
        if len(e24) >= self.rpd:
            wait = max(wait, 86400.0 - (now - e24[0][0]) + 0.05)
        tok60 = sum(e[1] for e in e60)
        if tokens <= self.tpm and tok60 + tokens > self.tpm:
            need, acc = tok60 + tokens - self.tpm, 0
            for ts, tk in e60:
                acc += tk
                if acc >= need:
                    wait = max(wait, 60.0 - (now - ts) + 0.05)
                    break
        tok24 = sum(e[1] for e in e24)
        if tokens <= self.tpd and tok24 + tokens > self.tpd:
            need, acc = tok24 + tokens - self.tpd, 0
            for ts, tk in e24:
                acc += tk
                if acc >= need:
                    wait = max(wait, 86400.0 - (now - ts) + 0.05)
                    break
        return wait

    async def acquire(self, tokens: int = 0):
        """Block until a slot is available; returns the reservation handle."""
        tokens = max(0, int(tokens or 0))
        if tokens > self.tpm:
            logger.warning(
                f"Groq request ~{tokens} tokens exceeds TPM {self.tpm} — "
                "it cannot fit one minute; sending anyway (expect 429). "
                "Reduce context/tool scope or raise GROQ_TPM."
            )
        while True:
            async with self._lock:
                now = time.time()
                self._events = [e for e in self._events if now - e[0] <= 86400.0]
                wait = self._compute_wait(tokens, now)
                if wait <= 0:
                    ev = [now, tokens]
                    self._events.append(ev)
                    return ev
            await asyncio.sleep(min(max(wait, 0.1), 120.0))

    async def settle(self, ev, tokens: int) -> None:
        """Replace a reservation's estimate with the real token usage."""
        async with self._lock:
            if ev is not None:
                ev[1] = max(0, int(tokens or 0))

    def snapshot(self) -> dict:
        now = time.time()
        e60 = [e for e in self._events if now - e[0] <= 60.0]
        e24 = [e for e in self._events if now - e[0] <= 86400.0]
        return {"rpm_used": len(e60), "rpd_used": len(e24),
                "tpm_used": sum(e[1] for e in e60), "tpd_used": sum(e[1] for e in e24),
                "rpm": self.rpm, "rpd": self.rpd, "tpm": self.tpm, "tpd": self.tpd}


_limiter: Optional[GroqRateLimiter] = None


def get_groq_limiter() -> GroqRateLimiter:
    """Shared limiter (MASTERMIND and vision both use the one GROQ_API_KEY)."""
    global _limiter
    if _limiter is None:
        _limiter = GroqRateLimiter(
            rpm=_env_int("GROQ_RPM", GROQ_RPM), rpd=_env_int("GROQ_RPD", GROQ_RPD),
            tpm=_env_int("GROQ_TPM", GROQ_TPM), tpd=_env_int("GROQ_TPD", GROQ_TPD))
        logger.info(f"Groq rate limiter: {_limiter.rpm} RPM / {_limiter.rpd} RPD / "
                    f"{_limiter.tpm} TPM / {_limiter.tpd} TPD")
    return _limiter


def _retry_after(exc, default: float = 5.0) -> float:
    """Seconds to wait from a 429 ``retry-after`` header, else ``default``."""
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers:
            v = headers.get("retry-after") or headers.get("Retry-After")
            if v is not None:
                return max(0.5, float(str(v).strip().rstrip("s")))
    except Exception:
        pass
    return default


def _usage_tokens(resp) -> int:
    usage = getattr(resp, "usage", None)
    total = getattr(usage, "total_tokens", 0) if usage is not None else 0
    try:
        return int(total or 0)
    except Exception:
        return 0


def _message_text(message) -> str:
    """Assistant text, falling back to reasoning (Groq exposes ``reasoning``)."""
    content = (getattr(message, "content", "") or "").strip()
    if content:
        return content
    reasoning = (getattr(message, "reasoning_content", "")
                 or getattr(message, "reasoning", "") or "")
    if reasoning:
        logger.warning("Groq: empty content, returning reasoning")
        return reasoning.strip()
    raise RuntimeError("Groq model returned no content")


# ── MASTERMIND on Groq ─────────────────────────────────────────────────────
class GroqAgent(Agent):
    """The main reasoning agent, backed by Groq's OpenAI-compatible API.

    Reuses every loop/memory/tool mechanism from :class:`agent.Agent`; only the
    client, model, context budget and request path (rate-limited) differ.
    """

    def __init__(self, name: str = "MASTERMIND", system_prompt: str = "",
                 base_prompts: Optional[list] = None, last_memory: Optional[list] = None,
                 use_tools: bool = True, save_history: bool = True, **kwargs) -> None:
        kwargs.pop("base_url", None)
        super().__init__(name=name, system_prompt=system_prompt, base_prompts=base_prompts,
                         last_memory=last_memory, use_tools=use_tools,
                         save_history=save_history)

        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY is not set (required for the Groq provider)")

        from groq import AsyncGroq

        self.model = os.environ.get("GROQ_MODEL", GROQ_MODEL)
        self._max_output_tokens = _env_int("GROQ_MAX_OUTPUT_TOKENS", GROQ_MAX_OUTPUT_TOKENS)
        self._context_tokens = _env_int("GROQ_MAX_TOKENS", GROQ_MAX_TOKENS)
        self._client = AsyncGroq(
            api_key=api_key,
            timeout=float(os.environ.get("GROQ_TIMEOUT", "120")),
            max_retries=0,  # we retry ourselves (honouring retry-after)
        )
        # Re-budget the context pool for Groq's TPM, keeping any last memory.
        self.messages = ContextPool(max_tokens=self._context_tokens)
        if self._last_memory:
            self.messages.assign_messages(construct_history(self._last_memory))
        self._refresh_base_prompt_tokens()
        self._limiter = get_groq_limiter()

        logger.info(f"GroqAgent '{name}' on {self.model} "
                    f"(ctx {self._context_tokens} tok, max_out {self._max_output_tokens})")

    @staticmethod
    def _estimate_tokens(messages: list[dict], tools: list[dict]) -> int:
        total = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                total += count_tokens(c)
            elif isinstance(c, list):
                total += count_tokens(json.dumps(c, ensure_ascii=False))
            if m.get("tool_calls"):
                total += count_tokens(json.dumps(m["tool_calls"], ensure_ascii=False))
        if tools:
            total += count_tokens(json.dumps(tools, ensure_ascii=False))
        return total

    @staticmethod
    def _normalize_reasoning(response_dict: dict) -> None:
        """Groq may return ``reasoning``; the loop reads ``reasoning_content``."""
        for choice in response_dict.get("choices") or []:
            msg = choice.get("message") or {}
            if not msg.get("reasoning_content") and msg.get("reasoning"):
                msg["reasoning_content"] = msg["reasoning"]

    async def _chat(self, messages: list[dict], tools: list[dict], est: int) -> object:
        from groq import RateLimitError, APIStatusError, APIConnectionError, APITimeoutError

        max_out = self._max_output_tokens
        attempt = 0
        while True:
            attempt += 1
            ev = await self._limiter.acquire(est + max_out)
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools or None,
                    max_tokens=max_out,
                    temperature=1.0,
                )
            except RateLimitError as e:
                await self._limiter.settle(ev, 0)
                if attempt >= GROQ_MAX_ATTEMPTS:
                    raise
                wait = _retry_after(e, default=min(60.0, 2.0 ** attempt))
                logger.warning(f"Groq 429 (attempt {attempt}/{GROQ_MAX_ATTEMPTS}) — "
                               f"sleeping {wait:.1f}s")
                await asyncio.sleep(wait)
                continue
            except (APIConnectionError, APITimeoutError) as e:
                await self._limiter.settle(ev, 0)
                if attempt >= GROQ_MAX_ATTEMPTS:
                    raise
                wait = min(30.0, 2.0 ** attempt)
                logger.warning(f"Groq connection error (attempt {attempt}): {e} — "
                               f"sleeping {wait:.1f}s")
                await asyncio.sleep(wait)
                continue
            except APIStatusError as e:
                await self._limiter.settle(ev, 0)
                if getattr(e, "status_code", 500) >= 500 and attempt < GROQ_MAX_ATTEMPTS:
                    wait = min(30.0, 2.0 ** attempt)
                    logger.warning(f"Groq {getattr(e, 'status_code', '?')} (attempt {attempt}) — "
                                   f"sleeping {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                raise
            await self._limiter.settle(ev, _usage_tokens(resp) or (est + max_out))
            return resp

    async def llm_request(self, enable_reasoning) -> dict:
        """One rate-limited Groq completion over the layered context."""
        messages = self._build_messages_for_llm()
        registry = get_registry()
        tools = registry.get_openai_tools(groups=self._active_groups) if self._use_tools else []
        est = self._estimate_tokens(messages, tools)
        logger.info(f"Groq request: {len(messages)} msgs, ~{est} tokens, {len(tools)} tools "
                    f"(limiter {self._limiter.snapshot()})")
        if est + self._max_output_tokens > self._limiter.tpm:
            logger.warning(
                f"Groq request ~{est} input tokens + {self._max_output_tokens} output "
                f"exceeds TPM {self._limiter.tpm}; lower GROQ_MAX_TOKENS or tool scope.")

        try:
            response = await self._chat(messages, tools, est)
        except Exception as e:
            logger.exception(f"Groq API call failed: {e}")
            with open("failed_messages.json", "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=2, default=str)
            raise

        if response is None or not getattr(response, "choices", None):
            raise ValueError(f"Invalid Groq response: {response}")
        response_dict = response.model_dump()
        if not response_dict.get("choices"):
            raise ValueError(f"Invalid Groq response: {response_dict}")
        self._normalize_reasoning(response_dict)
        return response_dict


# ── vision sub-agent on Groq ───────────────────────────────────────────────
from lib.vision_agent import (  # noqa: E402  (import after Agent to avoid cycles)
    DEFAULT_SYSTEM_PROMPT, VisionAgent, _with_size,
)
from lib.vision_client import encode_image  # noqa: E402


class GroqVisionAgent(VisionAgent):
    """The vision sub-agent on Groq's multimodal ``qwen/qwen3.8-27b``.

    Same watch/compare/locate machinery as :class:`lib.vision_agent.VisionAgent`;
    only the client/model and the (rate-limited) completion path differ.
    """

    def __init__(self, name: str = "VISION", system_prompt: str = DEFAULT_SYSTEM_PROMPT,
                 timeout: float = 120.0, model: Optional[str] = None) -> None:
        self.name = name
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY is not set (required for Groq vision)")

        from groq import AsyncGroq

        self.model = model or os.environ.get("GROQ_MODEL", GROQ_MODEL)
        self._client = AsyncGroq(api_key=api_key, timeout=float(timeout), max_retries=0)
        self._limiter = get_groq_limiter()
        self._max_tokens = _env_int("GROQ_VISION_MAX_TOKENS", 1024)
        self._image_token_est = _env_int("GROQ_IMAGE_TOKENS", GROQ_IMAGE_TOKEN_EST)
        self.watches: dict[str, dict] = {}
        logger.info(f"GroqVisionAgent '{name}' on {self.model} "
                    f"({'key set' if api_key else 'MISSING key'})")

    def _estimate_vision_tokens(self, messages: list[dict]) -> int:
        total = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                total += count_tokens(c)
            elif isinstance(c, list):
                for part in c:
                    if part.get("type") == "text":
                        total += count_tokens(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        total += self._image_token_est
        return total

    async def _vision_chat(self, messages: list[dict], max_tokens: Optional[int] = None) -> str:
        from groq import RateLimitError, APIStatusError, APIConnectionError, APITimeoutError

        max_tokens = int(max_tokens or self._max_tokens)
        est = self._estimate_vision_tokens(messages) + max_tokens
        attempt = 0
        while True:
            attempt += 1
            ev = await self._limiter.acquire(est)
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model, messages=messages,
                    max_tokens=max_tokens, temperature=0.2,
                )
            except RateLimitError as e:
                await self._limiter.settle(ev, 0)
                if attempt >= GROQ_MAX_ATTEMPTS:
                    raise
                wait = _retry_after(e, default=min(60.0, 2.0 ** attempt))
                logger.warning(f"Groq vision 429 (attempt {attempt}) — sleeping {wait:.1f}s")
                await asyncio.sleep(wait)
                continue
            except (APIConnectionError, APITimeoutError) as e:
                await self._limiter.settle(ev, 0)
                if attempt >= GROQ_MAX_ATTEMPTS:
                    raise
                wait = min(30.0, 2.0 ** attempt)
                logger.warning(f"Groq vision connection error (attempt {attempt}): {e}")
                await asyncio.sleep(wait)
                continue
            except APIStatusError as e:
                await self._limiter.settle(ev, 0)
                if getattr(e, "status_code", 500) >= 500 and attempt < GROQ_MAX_ATTEMPTS:
                    await asyncio.sleep(min(30.0, 2.0 ** attempt))
                    continue
                raise
            await self._limiter.settle(ev, _usage_tokens(resp) or est)
            return _message_text(resp.choices[0].message)

    async def _complete(self, parts, max_tokens: int = 1024) -> str:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": parts},
        ]
        return await self._vision_chat(messages, max_tokens)

    async def ask_once(self, image, query: str, max_tokens: int = 1024) -> str:
        data_url, w, h = encode_image(image)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": _with_size(query, w, h)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ]
        return await self._vision_chat(messages, max_tokens)


def install_groq_vision(model: Optional[str] = None) -> GroqVisionAgent:
    """Make ``lib.vision_agent.get_vision_agent()`` return a Groq vision agent."""
    from lib import vision_agent as va_mod
    inst = GroqVisionAgent(model=model)
    va_mod._vision_agent = inst
    logger.info("Vision sub-agent installed on Groq")
    return inst


def build_mastermind(use_tools: bool = True, save_history: bool = True) -> GroqAgent:
    """Construct the Groq MASTERMIND (kept here so callers need not import Agent)."""
    return GroqAgent(use_tools=use_tools, save_history=save_history)
