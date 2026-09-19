"""Offline OCR for ProLight-agent using Windows.Media.Ocr (WinRT).

Windows ships a built-in OCR engine; this wraps it so labels, buttons and field
values can be located deterministically (by text) without the vision model.

* No external binary and no network: the WinRT engine runs locally.
* Language coverage depends on the OCR language packs installed in Windows
  (``available_languages()`` reports them; ``en-US`` and ``ru`` are common).
* The blocking WinRT work runs on a dedicated worker thread (its own asyncio
  loop), mirroring ``lib.ui_tree``, so it never stalls the agent's event loop.

Requires the modular PyWinRT packages (see requirements.txt):
``winrt-runtime``, ``winrt-Windows.Media.Ocr``, ``winrt-Windows.Graphics.Imaging``,
``winrt-Windows.Storage.Streams``, ``winrt-Windows.Globalization``.
"""

from __future__ import annotations

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PIL import Image
from loguru import logger

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")


def _import_winrt():
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    return BitmapDecoder, OcrEngine, DataWriter, InMemoryRandomAccessStream


def available_languages() -> list[str]:
    """Return the OCR language tags installed in Windows (empty if unavailable)."""
    try:
        _, OcrEngine, _, _ = _import_winrt()
        return [str(l.language_tag) for l in OcrEngine.available_recognizer_languages]
    except Exception as e:  # pragma: no cover - env-dependent
        logger.warning(f"OCR unavailable: {e}")
        return []


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


async def _recognize_async(png: bytes, lang: Optional[str]) -> dict:
    BitmapDecoder, OcrEngine, DataWriter, InMemoryRandomAccessStream = _import_winrt()

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    engine = None
    if lang:
        try:
            from winrt.windows.globalization import Language

            engine = OcrEngine.try_create_from_language(Language(lang))
        except Exception:
            engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError(
            "no OCR engine available — install a Windows OCR language pack "
            f"(available: {available_languages()})"
        )

    result = await engine.recognize_async(bitmap)

    words: list[dict] = []
    lines: list[dict] = []
    for line in result.lines:
        line_words: list[dict] = []
        for word in line.words:
            r = word.bounding_rect
            w = {
                "text": str(word.text),
                "bbox": [
                    int(round(r.x)),
                    int(round(r.y)),
                    int(round(r.x + r.width)),
                    int(round(r.y + r.height)),
                ],
            }
            words.append(w)
            line_words.append(w)
        if line_words:
            x0 = min(w["bbox"][0] for w in line_words)
            y0 = min(w["bbox"][1] for w in line_words)
            x1 = max(w["bbox"][2] for w in line_words)
            y1 = max(w["bbox"][3] for w in line_words)
            lines.append({"text": str(line.text), "bbox": [x0, y0, x1, y1]})

    return {
        "words": words,
        "lines": lines,
        "size": [int(bitmap.pixel_width), int(bitmap.pixel_height)],
    }


def _run_sync(png: bytes, lang: Optional[str]) -> dict:
    return asyncio.run(_recognize_async(png, lang))


async def recognize(
    img: Image.Image, lang: Optional[str] = None, timeout: float = 30.0
) -> dict:
    """OCR an image. Returns ``{words, lines, size}`` (boxes in image pixels).

    Raises RuntimeError if the WinRT OCR engine is not available.
    """
    loop = asyncio.get_running_loop()
    png = _png_bytes(img)
    return await asyncio.wait_for(
        loop.run_in_executor(_executor, _run_sync, png, lang), timeout=timeout
    )


async def recognize_auto(
    img: Image.Image, langs: Optional[list[str]] = None, timeout: float = 30.0
) -> dict:
    """OCR with every available language and keep the richest result.

    Windows OCR is single-language per pass, and the user-profile default may
    not match the UI (e.g. an English default on a Russian page). We run each
    installed language and pick the one that recognised the most words, tagging
    the result with ``lang``. Falls back to the profile default when none work.
    """
    langs = langs or available_languages()
    best = None
    for lg in langs:
        try:
            res = await recognize(img, lg, timeout=timeout)
        except Exception:
            continue
        res["lang"] = lg
        if best is None or len(res.get("words", [])) > len(best.get("words", [])):
            best = res
    if best is None:
        best = await recognize(img, None, timeout=timeout)
        best["lang"] = None
    return best
