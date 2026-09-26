"""Application launch tools (group: ``apps``).

``app_launch`` starts an executable (full path, or a name found on PATH).

**Chromium / web pages:** page content only reaches UIA when the browser runs
with ``--force-renderer-accessibility``, and a browser that is *already running*
silently ignores the flag. To make Chrome readable you must relaunch it (it is
fine to close the running instance first), e.g.::

    app_launch(r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
               args="--force-renderer-accessibility https://example.com/")

Do **not** pass a separate ``--user-data-dir``: the default profile keeps the
user's logins. Then read the page with ``win_read_text`` (UIA, no OCR).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from loguru import logger

GROUP = "apps"


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def _split_args(s: str) -> list:
    """Split a Windows command-line string without mangling backslash paths."""
    return [t.strip('"') for t in re.findall(r'"[^"]*"|\S+', s or "")]


async def app_launch(path: str, args: str = "", cwd: str = "") -> str:
    """Launch an executable (full path, or a name found on PATH).

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


TOOL_DEFINITIONS = [
    (
        "app_launch",
        app_launch,
        "Launch an executable (full path, or a name found on PATH) with optional "
        "arguments and working directory. Returns the pid. For a UIA-readable "
        "Chromium page, pass args='--force-renderer-accessibility <url>' and do "
        "NOT set a separate user-data-dir (keep the user's default profile); a "
        "running browser must be closed first because it ignores the flag.",
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
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} app tool(s)")
