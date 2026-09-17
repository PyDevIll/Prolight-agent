"""Multimodal image analysis via the DeepSeek API (model: deepseek-flash).

DeepSeek's ``deepseek-flash`` accepts OpenAI-style multimodal content
(text + image_url with a base64 data URL). This module handles image
preparation (downscale + JPEG compression to control token cost) and the
API call.

The client is lazy-initialised so importing this module has no side effects.
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Optional, Union

from loguru import logger
from PIL import Image

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
VISION_MODEL = "deepseek-flash"
DEFAULT_MAX_DIM = 1600
DEFAULT_QUALITY = 80

_client = None


def _get_api_key() -> str:
    key = (
        os.environ.get("DEEPSEEK_API_KEY_MASTERMIND")
        or os.environ.get("DEEPSEEK_API_KEY")
        or ""
    )
    if not key:
        raise ValueError(
            "No DeepSeek API key found. Set DEEPSEEK_API_KEY_MASTERMIND "
            "(or DEEPSEEK_API_KEY) in the environment/.env"
        )
    return key


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        _client = AsyncOpenAI(
            api_key=_get_api_key(),
            base_url=DEEPSEEK_BASE_URL,
            timeout=120.0,
            max_retries=1,
        )
    return _client


def _load_image(image: Union[str, Path, bytes, Image.Image]) -> Image.Image:
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, (bytes, bytearray)):
        img = Image.open(io.BytesIO(image))
    else:
        img = Image.open(str(image))
    img.load()
    return img


def encode_image(
    image: Union[str, Path, bytes],
    max_dim: int = DEFAULT_MAX_DIM,
    quality: int = DEFAULT_QUALITY,
) -> tuple[str, int, int]:
    """Prepare an image as a base64 data URL.

    Downscales so the longest side is <= max_dim, flattens to RGB and
    encodes as JPEG (quality) to keep payload/token cost low.

    Returns (data_url, width, height).
    """
    img = _load_image(image)

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    if max_dim and max(img.size) > max_dim:
        scale = max_dim / max(img.size)
        new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}", img.width, img.height


async def analyze_image(
    image: Union[str, Path, bytes],
    query: str,
    max_tokens: int = 2048,
    max_dim: int = DEFAULT_MAX_DIM,
    quality: int = DEFAULT_QUALITY,
    client=None,
) -> str:
    """Send one image plus a question to the multimodal model.

    Args:
        image: path to an image file, raw image bytes, or a PIL image.
        query: the question/instruction about the image.
        max_tokens: generous budget — the model spends tokens on reasoning
            before emitting ``content`` (too small a budget yields empty text).

    Returns:
        The model's textual answer (``content``).

    Raises:
        ValueError: if no API key is configured.
        RuntimeError: if the API returns no usable content.
    """
    data_url, width, height = encode_image(image, max_dim=max_dim, quality=quality)
    logger.debug(f"vision: analyzing image {width}x{height}, query={query[:80]!r}")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": query},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    return await analyze_messages(messages, max_tokens=max_tokens, client=client)


def _extract_content(message) -> str:
    """Return the assistant text, falling back to reasoning_content if needed."""
    content = (getattr(message, "content", "") or "").strip()
    if content:
        return content
    reasoning = getattr(message, "reasoning_content", "") or ""
    if reasoning:
        # Budget was consumed by reasoning; surface it rather than nothing.
        logger.warning("vision: empty content, returning reasoning_content")
        return reasoning.strip()
    raise RuntimeError("Vision model returned no content")


async def analyze_messages(
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.2,
    model: str = VISION_MODEL,
    client=None,
) -> str:
    """Run a multimodal chat completion and return the assistant text.

    Pass ``client`` to use a specific AsyncOpenAI client (e.g. the vision
    sub-agent's own client/key); otherwise the shared vision client is used.
    """
    client = client or _get_client()
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    content = _extract_content(response.choices[0].message)
    logger.debug(f"vision: got {len(content)} chars")
    return content
