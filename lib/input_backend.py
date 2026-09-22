"""Input injection backend for ProLight-agent.

Two mechanisms:

1. **SendInput** (primary) — injects events into the system input queue.
   Requires the target window to be foreground. Mouse movement uses
   SetCursorPos (physical pixels, multi-monitor safe); buttons, wheel and
   keys use SendInput. Text is typed with KEYEVENTF_UNICODE so any script
   (including Cyrillic) is entered regardless of the active keyboard layout.

2. **WM_ messages** (fallback) — PostMessage to a specific HWND. Works for
   some standard Win32 controls in the background, but is ignored by
   browsers/Electron/1C/custom-drawn UIs. Coordinates are CLIENT coordinates.

All functions are synchronous. ``move_to`` is async (it paces itself without
blocking the event loop).
"""

from __future__ import annotations

import asyncio
import ctypes
import time
from ctypes import wintypes
from typing import Iterable, Optional

from lib import overlay

user32 = ctypes.WinDLL("user32", use_last_error=True)


def _overlay(fn, *args, **kwargs) -> None:
    """Fire-and-forget overlay feedback; never let it break input injection."""
    try:
        fn(*args, **kwargs)
    except Exception:
        pass

# ── SendInput structures ──────────────────────────────────────────────────
ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

WHEEL_DELTA = 120
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL


# ── Virtual-key names ─────────────────────────────────────────────────────
VK_NAMES: dict[str, int] = {
    "backspace": 0x08, "back": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "pause": 0x13, "capslock": 0x14, "caps": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "separator": 0x6C, "subtract": 0x6D,
    "decimal": 0x6E, "divide": 0x6F,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "numlock": 0x90, "scrolllock": 0x91, "scroll": 0x91,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3, "lalt": 0xA4, "ralt": 0xA5,
    "semicolon": 0xBA, "equals": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "backtick": 0xC0, "grave": 0xC0,
    "lbracket": 0xDB, "backslash": 0xDC, "rbracket": 0xDD, "quote": 0xDE,
}

# Keys that must be sent with KEYEVENTF_EXTENDEDKEY.
EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # pgup/pgdn/end/home/arrows
    0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5,  # ins/del/win/apps/divide/numlock/rctrl/ralt
}

MODIFIER_VKS = {
    "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "alt": 0x12, "lalt": 0xA4, "ralt": 0xA5,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
}

_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    "x1": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
    "x2": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
}

# WM_ fallback constants
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010


# ── Low-level helpers ─────────────────────────────────────────────────────
def _send(inputs: list[INPUT]) -> int:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        err = ctypes.get_last_error()
        raise OSError(f"SendInput sent {sent}/{n} events (GetLastError={err})")
    return sent


def _mouse_event(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx, dy, data, flags, 0, 0)
    _send([inp])


def _key_event(vk: int = 0, scan: int = 0, flags: int = 0) -> None:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    _send([inp])


# ── Mouse ─────────────────────────────────────────────────────────────────
def get_cursor_pos() -> dict:
    from lib.winapi import get_cursor_pos as _get
    return _get()


def set_cursor_pos(x: int, y: int) -> bool:
    return bool(user32.SetCursorPos(int(x), int(y)))


def _ease(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)  # smoothstep


async def move_to(x: int, y: int, duration: float = 0.25, steps: Optional[int] = None) -> dict:
    """Move the pointer to (x, y) along an eased path so the user can follow."""
    start = get_cursor_pos()
    sx, sy = start["x"], start["y"]
    x, y = int(x), int(y)

    if duration <= 0 or (sx == x and sy == y):
        set_cursor_pos(x, y)
        return {"x": x, "y": y}

    if not steps:
        steps = max(8, min(60, int(duration * 120)))
    delay = duration / steps

    for i in range(1, steps + 1):
        t = _ease(i / steps)
        cx = round(sx + (x - sx) * t)
        cy = round(sy + (y - sy) * t)
        set_cursor_pos(cx, cy)
        await asyncio.sleep(delay)

    set_cursor_pos(x, y)
    return {"x": x, "y": y}


def mouse_down(button: str = "left") -> None:
    down, _up, data = _BUTTONS[_norm_button(button)]
    _mouse_event(down, data=data)
    pos = get_cursor_pos()
    _overlay(overlay.flash_click, pos["x"], pos["y"], button, 0.5)


def mouse_up(button: str = "left") -> None:
    _down, up, data = _BUTTONS[_norm_button(button)]
    _mouse_event(up, data=data)
    pos = get_cursor_pos()
    _overlay(overlay.flash_click, pos["x"], pos["y"], button, 0.5)


def click(button: str = "left", clicks: int = 1, interval: float = 0.05) -> None:
    down, up, data = _BUTTONS[_norm_button(button)]
    for i in range(max(1, int(clicks))):
        _mouse_event(down, data=data)
        _mouse_event(up, data=data)
        if i < clicks - 1:
            time.sleep(interval)
    pos = get_cursor_pos()
    _overlay(overlay.flash_click, pos["x"], pos["y"], button, 0.6)


def wheel(amount: int, horizontal: bool = False) -> None:
    """Scroll by ``amount`` wheel notches (positive = up/right)."""
    flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    _mouse_event(flags, data=int(amount) * WHEEL_DELTA)
    _overlay(overlay.flash_keys, f"wheel {'h' if horizontal else 'v'} {int(amount):+d}")


def _norm_button(button: str) -> str:
    b = (button or "left").strip().lower()
    aliases = {"l": "left", "r": "right", "m": "middle", "1": "left", "2": "right", "3": "middle"}
    b = aliases.get(b, b)
    if b not in _BUTTONS:
        raise ValueError(f"Unknown mouse button: {button!r} (use left/right/middle/x1/x2)")
    return b


# ── Keyboard ──────────────────────────────────────────────────────────────
def key_name_to_vk(key: str) -> int:
    """Resolve a key name ('enter', 'a', 'f5', 'ctrl') to a virtual-key code."""
    k = str(key).strip()
    low = k.lower()
    if low in VK_NAMES:
        return VK_NAMES[low]
    if len(k) == 1:
        ch = k.upper()
        if ("A" <= ch <= "Z") or ("0" <= ch <= "9"):
            return ord(ch)
    raise ValueError(f"Unknown key name: {key!r}")


def key_down(key: str) -> int:
    vk = key_name_to_vk(key)
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
    _key_event(vk=vk, flags=flags)
    return vk


def key_up(key: str) -> int:
    vk = key_name_to_vk(key)
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
    _key_event(vk=vk, flags=flags | KEYEVENTF_KEYUP)
    return vk


def key_stroke(key: str) -> int:
    vk = key_name_to_vk(key)
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
    _key_event(vk=vk, flags=flags)
    _key_event(vk=vk, flags=flags | KEYEVENTF_KEYUP)
    _overlay(overlay.flash_keys, str(key))
    return vk


def hotkey(combo: str) -> list[int]:
    """Press a key combination, e.g. 'ctrl+s', 'ctrl+shift+esc', 'alt+f4'."""
    parts = [p.strip().lower() for p in str(combo).replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty hotkey")
    key = parts[-1]
    mods = parts[:-1]

    mod_vks: list[int] = []
    for m in mods:
        if m not in MODIFIER_VKS:
            raise ValueError(f"Unknown modifier: {m!r}")
        mod_vks.append(MODIFIER_VKS[m])

    key_vk = key_name_to_vk(key)
    key_flags = KEYEVENTF_EXTENDEDKEY if key_vk in EXTENDED_VKS else 0

    for vk in mod_vks:
        _key_event(vk=vk)
    _key_event(vk=key_vk, flags=key_flags)
    _key_event(vk=key_vk, flags=key_flags | KEYEVENTF_KEYUP)
    for vk in reversed(mod_vks):
        _key_event(vk=vk, flags=KEYEVENTF_KEYUP)

    _overlay(overlay.flash_keys, str(combo))
    return mod_vks + [key_vk]


def type_text(text: str, interval: float = 0.01) -> int:
    """Type text using Unicode injection (layout-independent, supports Cyrillic).

    Newlines become Enter; tabs become Tab. Returns the number of code units sent.
    """
    count = 0
    for ch in str(text):
        if ch == "\r":
            continue
        if ch == "\n":
            key_stroke("enter")
            count += 1
            continue
        if ch == "\t":
            key_stroke("tab")
            count += 1
            continue

        units = ch.encode("utf-16-le")
        for i in range(0, len(units), 2):
            scan = int.from_bytes(units[i:i + 2], "little")
            _key_event(scan=scan, flags=KEYEVENTF_UNICODE)
            _key_event(scan=scan, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
            count += 1
        if interval > 0:
            time.sleep(interval)
    snippet = str(text).replace("\r", "").replace("\n", "\\n").replace("\t", "\\t")
    if len(snippet) > 40:
        snippet = snippet[:37] + "..."
    _overlay(overlay.flash_keys, f'type "{snippet}"' if snippet else "type")
    return count


# ── WM_ message fallback ──────────────────────────────────────────────────
def _makelparam(low: int, high: int) -> int:
    return (int(high) << 16) | (int(low) & 0xFFFF)


def wm_click(hwnd: int, x: int, y: int, button: str = "left") -> None:
    """Post a click to a window's CLIENT coordinates (no focus change)."""
    b = _norm_button(button)
    lparam = _makelparam(int(x), int(y))
    if b == "left":
        down, up, mk = WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON
    elif b == "right":
        down, up, mk = WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON
    elif b == "middle":
        down, up, mk = WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON
    else:
        raise ValueError("WM_ fallback supports left/right/middle only")
    user32.PostMessageW(int(hwnd), down, mk, lparam)
    user32.PostMessageW(int(hwnd), up, 0, lparam)


def wm_key(hwnd: int, key: str) -> None:
    """Post a key press/release to a window (no focus change)."""
    vk = key_name_to_vk(key)
    scan = user32.MapVirtualKeyW(vk, 0)
    lparam = 1 | (scan << 16)
    if vk in EXTENDED_VKS:
        lparam |= (1 << 24)
    user32.PostMessageW(int(hwnd), WM_KEYDOWN, vk, lparam)
    user32.PostMessageW(int(hwnd), WM_KEYUP, vk, lparam | (1 << 30) | (1 << 31))


def wm_char(hwnd: int, char: str) -> None:
    """Post a WM_CHAR for the first character (types without focus)."""
    if not char:
        return
    user32.PostMessageW(int(hwnd), WM_CHAR, ord(char[0]), 1)
