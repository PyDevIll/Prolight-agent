"""UI Automation (UIA) control discovery for ProLight-agent.

Wraps pywinauto's UIA backend to expose a window's control tree as plain
JSON-serialisable dicts: ``name``, ``control_type``, ``automation_id``,
``class_name``, rectangle (screen coordinates), enabled/visible state and,
when available, the control's value.

Design notes
------------
* pywinauto is imported lazily, so importing this module has no side effects.
* UIA is COM-based and apartment-threaded. Every call runs on a single
  dedicated worker thread with COM initialised (see ``_run``), which keeps
  COM apartment state consistent and keeps the event loop unblocked.
* Coordinates are screen coordinates in physical pixels: ``lib.winapi`` makes
  the process per-monitor DPI aware on import.
* UIA calls can be slow (and, on misbehaving providers, can block). Every
  call is bounded by ``timeout``; a hung provider degrades this feature but
  does not stall the agent loop.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

# UIA expects an STA apartment; set this before comtypes/pywinauto are imported
# (they read sys.coinit_flags at import time) to avoid pywinauto's
# "Revert to STA COM threading mode" warning.
if sys.platform == "win32" and getattr(sys, "coinit_flags", None) is None:
    sys.coinit_flags = 2  # COINIT_APARTMENTTHREADED

_tls = threading.local()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia")

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_CONTROLS = 400
DEFAULT_TIMEOUT = 20.0


# ── worker-thread plumbing ────────────────────────────────────────────────
def _ensure_com() -> None:
    """Initialise COM once per worker thread (comtypes is apartment-threaded)."""
    if getattr(_tls, "com", False):
        return
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    _tls.com = True


def _desktop():
    d = getattr(_tls, "desktop", None)
    if d is None:
        from pywinauto import Desktop
        d = Desktop(backend="uia")
        _tls.desktop = d
    return d


def _root(hwnd: int):
    spec = _desktop().window(handle=int(hwnd))
    return spec.wrapper_object()


async def _run(fn: Callable, *args, timeout: float = DEFAULT_TIMEOUT, **kwargs):
    loop = asyncio.get_running_loop()

    def _job():
        _ensure_com()
        return fn(*args, **kwargs)

    return await asyncio.wait_for(loop.run_in_executor(_executor, _job), timeout=timeout)


# ── element → dict ────────────────────────────────────────────────────────
def _safe_rect(el) -> Optional[dict]:
    try:
        r = el.rectangle()
    except Exception:
        return None
    if r is None:
        return None
    left, top, right, bottom = int(r.left), int(r.top), int(r.right), int(r.bottom)
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
        "center_x": (left + right) // 2,
        "center_y": (top + bottom) // 2,
        "offscreen": (right - left) <= 0 or (bottom - top) <= 0,
    }


def _value_of(el) -> str:
    try:
        v = el.iface_value.CurrentValue
        if v:
            return str(v)
    except Exception:
        pass
    try:
        v = el.get_value()
        if v:
            return str(v)
    except Exception:
        pass
    return ""


def _control_dict(el, depth: int) -> dict:
    ei = el.element_info
    d = {
        "control_type": (getattr(ei, "control_type", "") or ""),
        "name": (getattr(ei, "name", "") or "")[:120],
        "automation_id": (getattr(ei, "automation_id", "") or ""),
        "class_name": (getattr(ei, "class_name", "") or ""),
        "depth": depth,
        "rect": _safe_rect(el),
    }
    for attr, key in (("is_enabled", "enabled"), ("is_visible", "visible")):
        try:
            d[key] = bool(getattr(el, attr)())
        except Exception:
            pass
    try:
        d["focused"] = bool(el.has_keyboard_focus())
    except Exception:
        pass
    val = _value_of(el)
    if val:
        d["value"] = val[:200]
    return d


def _walk(root_el, max_depth: int, max_controls: int):
    """Breadth-first walk of the control tree, bounded by depth and count."""
    out = []
    queue = [(root_el, 0)]
    while queue and len(out) < max_controls:
        el, depth = queue.pop(0)
        out.append((el, depth))
        if depth >= max_depth:
            continue
        try:
            children = el.children()
        except Exception:
            children = []
        for c in children:
            if len(out) + len(queue) >= max_controls:
                break
            queue.append((c, depth + 1))
    return out


def _matches(d: dict, name: str, control_type: str, automation_id: str, class_name: str) -> bool:
    if name and name.lower() not in (d.get("name") or "").lower():
        return False
    if control_type and control_type.lower() not in (d.get("control_type") or "").lower():
        return False
    if automation_id and automation_id.lower() not in (d.get("automation_id") or "").lower():
        return False
    if class_name and class_name.lower() not in (d.get("class_name") or "").lower():
        return False
    return True


# ── sync implementations (run on the UIA worker thread) ───────────────────
def _enum_sync(hwnd, max_depth, max_controls, control_type, rects_only):
    root = _root(hwnd)
    items = _walk(root, int(max_depth), int(max_controls))
    needle = (control_type or "").strip().lower()
    out = []
    for el, depth in items:
        d = _control_dict(el, depth)
        if needle and needle not in d["control_type"].lower():
            continue
        if rects_only:
            if not d.get("rect"):
                continue
            d = {k: d[k] for k in ("name", "control_type", "automation_id", "depth", "rect") if k in d}
        out.append(d)
    return out


def _find_sync(hwnd, name, control_type, automation_id, class_name, max_depth, max_controls):
    root = _root(hwnd)
    items = _walk(root, int(max_depth), int(max_controls))
    found = []
    for el, depth in items:
        d = _control_dict(el, depth)
        if _matches(d, name, control_type, automation_id, class_name):
            found.append((el, d))
    return found


def _scroll_sync(hwnd, name, control_type, automation_id, class_name, max_depth, max_controls):
    found = _find_sync(hwnd, name, control_type, automation_id, class_name, max_depth, max_controls)
    if not found:
        return None
    el, d = found[0]
    try:
        el.scroll_into_view()
    except Exception:
        pass
    return _control_dict(el, d.get("depth", 0))


def _set_text_sync(hwnd, name, control_type, automation_id, class_name, text, max_depth, max_controls):
    found = _find_sync(hwnd, name, control_type, automation_id, class_name, max_depth, max_controls)
    if not found:
        return {"ok": False, "error": "no matching control"}
    el, d = found[0]
    try:
        el.iface_value.SetValue(text)
        return {"ok": True, "control": d, "method": "ValuePattern"}
    except Exception:
        pass
    try:
        el.set_edit_text(text)
        return {"ok": True, "control": d, "method": "set_edit_text"}
    except Exception as e:
        return {"ok": False, "error": f"cannot set text: {e}", "control": d}


def _get_text_sync(hwnd, max_depth, max_controls, max_chars):
    """Dump a window's accessible text (UIA TextPattern first, then names/values).

    For Chromium documents ``TextPattern.GetText`` returns the whole page text,
    so this reads a long list/thread without OCR.
    """
    root = _root(hwnd)
    raw = []
    for el, depth in _walk(root, int(max_depth), int(max_controls)):
        txt = ""
        try:
            txt = el.iface_text.GetText(-1) or ""
        except Exception:
            txt = ""
        if not txt.strip():
            d = _control_dict(el, depth)
            txt = d.get("value") or d.get("name") or ""
        if txt and txt.strip():
            raw.append(txt.strip())
    # Drop exact duplicates and any text already contained in a longer one
    # (a document's text includes its children's), preserving tree order.
    uniq = list(dict.fromkeys(raw))
    kept = [t for t in uniq if not any(t != o and t in o for o in uniq)]
    text = "\n".join(kept)
    truncated = False
    if max_chars and len(text) > int(max_chars):
        text = text[: int(max_chars)]
        truncated = True
    return {"ok": True, "text": text, "characters": len(text),
            "truncated": truncated, "blocks": len(kept)}


def _element_at_point_sync(x, y):
    """UIA element under a screen point (for click labelling)."""
    _ensure_com()
    el = None
    for backend in ("uia", "win32"):
        try:
            from pywinauto import Desktop

            el = Desktop(backend=backend).from_point(int(x), int(y))
            if el is not None:
                break
        except Exception:
            el = None
    if el is None:
        return {"ok": False, "error": "no element at point"}
    try:
        d = _control_dict(el, 0)
    except Exception as e:
        return {"ok": False, "error": f"element read failed: {e}"}
    d["point"] = {"x": int(x), "y": int(y)}
    try:
        from lib import winapi

        ctrl_hwnd = winapi.window_from_point(int(x), int(y))
        if ctrl_hwnd:
            root = winapi.get_root_window(ctrl_hwnd)
            d["control_hwnd"] = ctrl_hwnd
            d["window"] = {
                "hwnd": root,
                "title": winapi.get_window_text(root),
                "class": winapi.get_class_name(root),
                "process": winapi.get_process_name(winapi.get_window_pid(root)),
            }
    except Exception:
        pass
    d["ok"] = True
    return d


# ── async API used by tools ───────────────────────────────────────────────
async def enum_controls(
    hwnd: int,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_controls: int = DEFAULT_MAX_CONTROLS,
    control_type: str = "",
    rects_only: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    return await _run(
        _enum_sync, int(hwnd), max_depth, max_controls, control_type, rects_only, timeout=timeout
    )


async def get_text(
    hwnd: int,
    max_depth: int = DEFAULT_MAX_DEPTH + 1,
    max_controls: int = DEFAULT_MAX_CONTROLS,
    max_chars: int = 20000,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Return a window's accessible text via UIA TextPattern (no OCR).

    ``{"ok", "text", "characters", "truncated", "blocks"}``.
    """
    return await _run(
        _get_text_sync, int(hwnd), max_depth, max_controls, max_chars, timeout=timeout
    )


async def element_at_point(x: int, y: int, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Return the UIA control under a screen point (name/type/rect)."""
    return await _run(_element_at_point_sync, int(x), int(y), timeout=timeout)


def element_at_point_sync(x: int, y: int, timeout: float = 2.0) -> dict:
    """Blocking variant usable from a non-async thread (e.g. the input tracker)."""
    try:
        return _executor.submit(_element_at_point_sync, int(x), int(y)).result(timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def find_controls(
    hwnd: int,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    max_depth: int = DEFAULT_MAX_DEPTH + 1,
    max_controls: int = DEFAULT_MAX_CONTROLS,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    found = await _run(
        _find_sync, int(hwnd), name, control_type, automation_id, class_name,
        max_depth, max_controls, timeout=timeout,
    )
    return [d for _el, d in found]


async def scroll_into_view(
    hwnd: int,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    max_depth: int = DEFAULT_MAX_DEPTH + 1,
    max_controls: int = DEFAULT_MAX_CONTROLS,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[dict]:
    return await _run(
        _scroll_sync, int(hwnd), name, control_type, automation_id, class_name,
        max_depth, max_controls, timeout=timeout,
    )


async def set_control_text(
    hwnd: int,
    text: str,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    max_depth: int = DEFAULT_MAX_DEPTH + 1,
    max_controls: int = DEFAULT_MAX_CONTROLS,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    return await _run(
        _set_text_sync, int(hwnd), name, control_type, automation_id, class_name,
        text, max_depth, max_controls, timeout=timeout,
    )


async def wait_for_control(
    hwnd: int,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    class_name: str = "",
    timeout: float = 10.0,
    interval: float = 0.5,
    max_depth: int = DEFAULT_MAX_DEPTH + 1,
    max_controls: int = DEFAULT_MAX_CONTROLS,
) -> Optional[dict]:
    """Poll until a matching control exists, or the timeout elapses."""
    deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout))
    while True:
        found = await find_controls(
            hwnd, name=name, control_type=control_type, automation_id=automation_id,
            class_name=class_name, max_depth=max_depth, max_controls=max_controls,
        )
        if found:
            return found[0]
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(max(0.05, float(interval)))
