"""Application launch tools (group: ``apps``).

``app_launch`` starts an executable; ``browser_launch_accessible`` starts a
Chromium-family browser with renderer accessibility forced on a scratch
``--user-data-dir``.

Why the dedicated browser launcher: Chromium only builds its accessibility tree
when it starts with ``--force-renderer-accessibility`` (or when an AT client
connects), and a browser that is already running **silently ignores the flag**
— so switching an existing instance is impossible and a fresh profile is
required. Once launched this way the page content is readable via UIA
(``win_read_text``) with no OCR.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from loguru import logger

from lib import winapi

GROUP = "apps"

# Chromium-family executables, per browser (first existing path wins).
_EXE_DIRS = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "brave": [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ],
    "vivaldi": [
        os.path.expandvars(r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe"),
        r"C:\Program Files\Vivaldi\Application\vivaldi.exe",
    ],
    "opera": [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Opera\opera.exe")],
    "chromium": [os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe")],
}

_PROCESS = {
    "chrome": "chrome", "edge": "msedge", "brave": "brave",
    "vivaldi": "vivaldi", "opera": "opera", "chromium": "chromium",
}


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def _split_args(s: str) -> list:
    """Split a Windows command-line string without mangling backslash paths."""
    return [t.strip('"') for t in re.findall(r'"[^"]*"|\S+', s or "")]


def _find_browser(browser: str) -> str:
    b = (browser or "chrome").strip().lower()
    exe = shutil.which(b + ".exe") or shutil.which(b)
    if exe:
        return exe
    for p in _EXE_DIRS.get(b, []):
        if p and os.path.isfile(p):
            return p
    return ""


def _windows_for(proc_sub: str) -> list:
    return [w for w in winapi.list_windows() if proc_sub in (w.get("process") or "").lower()]


def _has_render_host(hwnd: int) -> bool:
    try:
        return bool(winapi.find_child_by_class(int(hwnd), "Chrome_RenderWidgetHostHWND"))
    except Exception:
        return False


def _area(w: dict) -> int:
    r = w.get("rect") or {}
    return max(0, (r.get("right", 0) - r.get("left", 0))) * \
        max(0, (r.get("bottom", 0) - r.get("top", 0)))


def _pick_browser_window(cands: list):
    """Pick the real browser frame among new windows (not a bubble/popup)."""
    if not cands:
        return None

    def score(w):
        return (1 if _has_render_host(w["hwnd"]) else 0,
                1 if (w.get("title") or "").strip() else 0,
                _area(w))

    return max(cands, key=score)


async def app_launch(path: str, args: str = "", cwd: str = "") -> str:
    """Launch an executable (path, or a name found on PATH).

    Args:
        path: full path to the executable, or its name if on PATH.
        args: extra command-line arguments (quoted paths allowed).
        cwd: working directory (optional).
    """
    exe = path if path and os.path.isfile(path) else (shutil.which(path or "") or "")
    if not exe:
        return _dump({"ok": False, "error": f"executable not found: {path!r}"})
    cmd = [exe] + _split_args(args)
    try:
        p = subprocess.Popen(cmd, cwd=cwd or None, close_fds=True)
    except Exception as e:
        return _dump({"ok": False, "error": f"launch failed: {e}"})
    logger.info(f"app_launch: {cmd[0]} pid={p.pid}")
    return _dump({"ok": True, "executable": exe, "pid": p.pid, "args": cmd[1:]})


async def browser_launch_accessible(
    browser: str = "chrome", url: str = "", profile: str = "",
    extra_args: str = "", wait_seconds: float = 8.0,
) -> str:
    """Launch a Chromium browser with renderer accessibility forced on.

    Uses a scratch ``--user-data-dir`` (a running instance ignores the flag, so
    a fresh profile forces a new process). Returns the new window once found.

    Args:
        browser: chrome | edge | brave | vivaldi | opera | chromium.
        url: page to open.
        profile: user-data-dir (default: a scratch dir in %TEMP%).
        extra_args: extra browser flags (quoted paths allowed).
        wait_seconds: how long to wait for the new window to appear.
    """
    b = (browser or "chrome").strip().lower()
    exe = _find_browser(b)
    if not exe:
        return _dump({"ok": False, "error": f"{b} not found (try app_launch with a full path)"})
    proc_sub = _PROCESS.get(b, b)
    before = {w["hwnd"] for w in _windows_for(proc_sub)}
    udd = profile or os.path.join(tempfile.gettempdir(), f"prolight_{b}_acc")
    cmd = [exe, "--force-renderer-accessibility", "--no-first-run",
           "--no-default-browser-check", "--user-data-dir=" + udd]
    cmd += _split_args(extra_args)
    if url:
        cmd.append(url)
    try:
        p = subprocess.Popen(cmd, close_fds=True)
    except Exception as e:
        return _dump({"ok": False, "error": f"launch failed: {e}"})

    hwnd = 0
    deadline = time.time() + max(2.0, float(wait_seconds or 8.0))
    while time.time() < deadline:
        new = [w for w in _windows_for(proc_sub) if w["hwnd"] not in before]
        if new:
            pick = _pick_browser_window(new)
            if pick:
                hwnd = pick["hwnd"]
                # A real page frame has a render host; keep polling briefly if
                # only auxiliary bubbles have appeared so far.
                if _has_render_host(hwnd):
                    break
        await asyncio.sleep(0.5)
    # Best-effort: give the renderer a moment to expose the page document so
    # the caller can win_read_text immediately.
    documents = 0
    if hwnd and url:
        try:
            from lib import ui_tree
            doc_deadline = time.time() + min(max(2.0, float(wait_seconds or 8.0)), 8.0)
            while time.time() < doc_deadline:
                documents = await ui_tree.count_documents(hwnd)
                if documents > 0:
                    break
                await asyncio.sleep(0.5)
        except Exception:
            pass
    window = None
    if hwnd:
        window = winapi.get_window_info(hwnd) or {}
        window["title"] = winapi.get_window_text(hwnd)
    logger.info(f"browser_launch_accessible: {b} pid={p.pid} hwnd={hwnd or '-'} docs={documents}")
    return _dump({
        "ok": True, "browser": b, "executable": exe, "user_data_dir": udd,
        "pid": p.pid, "window": window, "documents": documents,
        "note": "renderer accessibility forced; read page text with win_read_text",
    })


TOOL_DEFINITIONS = [
    (
        "app_launch",
        app_launch,
        "Launch an executable (full path, or a name found on PATH) with optional "
        "arguments and working directory. Returns the pid.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Executable path or PATH name"},
                "args": {"type": "string", "description": "Extra command-line arguments"},
                "cwd": {"type": "string", "description": "Working directory (optional)"},
            },
            "required": ["path"],
        },
    ),
    (
        "browser_launch_accessible",
        browser_launch_accessible,
        "Launch a Chromium browser (chrome/edge/brave/vivaldi/opera/chromium) with "
        "--force-renderer-accessibility on a scratch --user-data-dir, so its page "
        "content becomes readable via win_read_text (UIA). A running browser ignores "
        "the flag, hence the fresh profile. Returns the new window.",
        {
            "type": "object",
            "properties": {
                "browser": {"type": "string", "description": "chrome | edge | brave | vivaldi | opera | chromium"},
                "url": {"type": "string", "description": "Page to open"},
                "profile": {"type": "string", "description": "user-data-dir (default: scratch dir in %TEMP%)"},
                "extra_args": {"type": "string", "description": "Extra browser flags"},
                "wait_seconds": {"type": "number", "description": "Seconds to wait for the window (default 8)"},
            },
            "required": [],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} app tool(s)")
