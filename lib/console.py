"""Console/stdio encoding bootstrap for ProLight-agent.

Windows consoles default to a legacy code page (CP866/CP1251), while Python's
text streams default to that same locale codec. Cyrillic window titles and
model answers then either crash with UnicodeEncodeError or render as mojibake.

``force_utf8_console()`` makes the whole path UTF-8:
  1. sets the Windows console code page to 65001 (UTF-8),
  2. reconfigures Python's stdin/stdout/stderr to UTF-8.

Call it once, as early as possible (before the first print/log).
"""

from __future__ import annotations

import ctypes
import sys

_UTF8_CP = 65001


def _set_console_code_page() -> None:
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(_UTF8_CP)
        kernel32.SetConsoleCP(_UTF8_CP)
    except Exception:
        # No console attached (e.g. output redirected) — nothing to do.
        pass


def _reconfigure_streams() -> None:
    for name, errors in (("stdout", "replace"), ("stderr", "replace"), ("stdin", "replace")):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors=errors)
        except (AttributeError, ValueError, OSError):
            pass


def force_utf8_console() -> None:
    """Set the Windows console code page and Python streams to UTF-8."""
    _set_console_code_page()
    _reconfigure_streams()
