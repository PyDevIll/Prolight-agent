"""On-screen action overlay for ProLight-agent.

A small, self-contained Win32 overlay engine that draws feedback the *human*
can see but the agent's own screen captures cannot:

  * a frame around every captured region (debug aid, ambient),
  * a persistent bottom-right status HUD (thinking / tool / waiting / done),
  * transient click rings and key/typing toasts,
  * deliberate ``highlight`` boxes the agent can pop up (e.g. to show the user
    which area it is asking about).

Overlays are *layered* windows (``WS_EX_LAYERED | WS_EX_TRANSPARENT``) so they
are click-through and never swallow the agent's own mouse clicks, rendered with
``UpdateLayeredWindow`` (per-pixel alpha). Layered windows cannot be excluded
from capture via ``SetWindowDisplayAffinity``, so invisibility is guaranteed by
a *capture gate*: :func:`begin_capture` / :func:`end_capture` hide the overlays
that intersect a grab for the few milliseconds the grab takes. The gate is fast
because the overlay thread runs a real ``GetMessage`` pump (the wake message is
handled immediately).

Overlays never steal focus and never appear in the taskbar
(``WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST``).

Ambient feedback (frames, HUD, click/key toasts) is gated by the
``PROLIGHT_OVERLAY`` environment variable (default on). Deliberate
:func:`highlight` calls always draw, even when ambient feedback is off.

Everything is non-blocking: public calls enqueue a command for the dedicated
overlay thread and return immediately.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
from ctypes import wintypes
from typing import Optional

from loguru import logger

OVERLAY_ENV = "PROLIGHT_OVERLAY"

# ── Win32 constants ───────────────────────────────────────────────────────
WS_POPUP = 0x80000000

WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

WDA_EXCLUDEFROMCAPTURE = 0x00000011

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1

DIB_RGB_COLORS = 0
BI_RGB = 0

MONITOR_DEFAULTTONEAREST = 0x00000002

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_APP = 0x8000
WM_OVERLAY_WAKE = WM_APP + 1

_EXSTYLE = (
    WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
)

_TIMER_ID = 1
_TIMER_MS = 40
_HWND_MESSAGE = -3

_CLASS_NAME = "ProLightOverlayWindow"
_FRAME_COLOR = (255, 170, 0)
_HIGHLIGHT_COLOR = (0, 200, 255)
_BG = (18, 20, 26)

_KIND_COLORS = {
    "thinking": (90, 160, 255),
    "tool": (255, 180, 40),
    "waiting": (255, 90, 90),
    "done": (70, 200, 110),
    "keys": (255, 205, 80),
    "info": (150, 150, 160),
}

_BUTTON_COLORS = {
    "left": (255, 120, 40),
    "right": (80, 150, 255),
    "middle": (90, 210, 120),
    "x1": (200, 120, 255),
    "x2": (200, 120, 255),
}

# ── ctypes layer ──────────────────────────────────────────────────────────
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", ctypes.c_void_p),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

_user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
_user32.RegisterClassExW.restype = ctypes.c_ushort

_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p, wintypes.HINSTANCE, ctypes.c_void_p,
]
_user32.CreateWindowExW.restype = wintypes.HWND

_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = LRESULT

_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.DestroyWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL

_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int

_user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
    wintypes.HDC, ctypes.POINTER(POINT), wintypes.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]
_user32.UpdateLayeredWindow.restype = wintypes.BOOL

_user32.GetMessageW.argtypes = [
    ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
_user32.GetMessageW.restype = ctypes.c_int
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_user32.PostQuitMessage.restype = None
_user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
_user32.SetTimer.restype = ctypes.c_size_t
_user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
_user32.TranslateMessage.restype = wintypes.BOOL
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
_user32.DispatchMessageW.restype = LRESULT

_user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
_user32.MonitorFromPoint.restype = ctypes.c_void_p
_user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL

_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD,
]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL

_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def _wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_DESTROY:
        _state.win_rect.pop(hwnd, None)
        return 0
    return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ── Engine state ──────────────────────────────────────────────────────────
def _env_enabled() -> bool:
    raw = os.environ.get(OVERLAY_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


class _State:
    def __init__(self) -> None:
        self.enabled = _env_enabled()
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.ready = threading.Event()
        self.ctl = 0
        self.q: "queue.Queue" = queue.Queue()
        self.stop = False
        self.class_registered = False
        self.wndproc_ref = None
        self.hinstance = None
        self.affinity_ok: Optional[bool] = None
        self.windows: dict = {}          # hwnd -> kind
        self.win_rect: dict = {}         # hwnd -> (x, y, w, h)
        self.transient: dict = {}        # hwnd -> expire monotonic (or None = sticky)
        self.hidden: set = set()         # hwnds hidden by the capture gate
        self.hud = 0
        self.hud_rect: Optional[tuple] = None
        self.toast = 0
        self.highlights: dict = {}       # token -> [hwnd, ...]
        self.token_seq = 0


_state = _State()


# ── Thread plumbing ───────────────────────────────────────────────────────
def _ensure_thread() -> None:
    with _state.lock:
        if _state.thread is not None and _state.thread.is_alive():
            return
        _state.stop = False
        _state.ready.clear()
        _state.ctl = 0
        _state.thread = threading.Thread(
            target=_loop, name="prolight-overlay", daemon=True
        )
        _state.thread.start()
        _state.ready.wait(timeout=1.0)


def _enqueue(fn) -> None:
    _ensure_thread()
    _state.q.put(fn)
    if _state.ctl:
        _user32.PostMessageW(_state.ctl, WM_OVERLAY_WAKE, 0, 0)


def _drain_queue() -> None:
    while True:
        try:
            fn = _state.q.get_nowait()
        except queue.Empty:
            break
        try:
            fn()
        except Exception as e:
            logger.debug(f"overlay command failed: {e}")


def _loop() -> None:
    try:
        _register_class()
        _state.ctl = _user32.CreateWindowExW(
            0, _CLASS_NAME, "", 0, 0, 0, 0, 0,
            _HWND_MESSAGE, None, _state.hinstance, None,
        )
        if _state.ctl:
            _user32.SetTimer(_state.ctl, _TIMER_ID, _TIMER_MS, None)
    except Exception as e:  # pragma: no cover - environment dependent
        logger.warning(f"overlay: thread init failed: {e}")
    _state.ready.set()

    msg = MSG()
    while True:
        r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r in (0, -1):
            break
        if msg.hwnd == _state.ctl and msg.message == WM_OVERLAY_WAKE:
            _drain_queue()
        elif msg.message == WM_TIMER:
            _expire()
        else:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        if _state.stop and not _state.windows:
            _user32.PostQuitMessage(0)


def _expire() -> None:
    now = time.monotonic()
    for hwnd, exp in list(_state.transient.items()):
        if exp is not None and now >= exp:
            _destroy(hwnd)


# ── Window helpers ────────────────────────────────────────────────────────
def _register_class() -> None:
    if _state.class_registered:
        return
    _state.hinstance = _kernel32.GetModuleHandleW(None)
    proc = WNDPROC(_wnd_proc)
    _state.wndproc_ref = proc
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = 0
    wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    wc.hInstance = _state.hinstance
    wc.hbrBackground = None
    wc.lpszClassName = _CLASS_NAME
    if not _user32.RegisterClassExW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err != 1410:  # already registered (hot reload) — fine
            raise OSError(f"RegisterClassExW failed ({err})")
    _state.class_registered = True


def _create_window(x: int, y: int, w: int, h: int, img) -> int:
    hwnd = _user32.CreateWindowExW(
        _EXSTYLE, _CLASS_NAME, "", WS_POPUP,
        int(x), int(y), max(1, int(w)), max(1, int(h)),
        None, None, _state.hinstance, None,
    )
    if not hwnd:
        raise OSError(f"CreateWindowExW failed ({ctypes.get_last_error()})")
    try:
        ok = bool(_user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        ok = False
    if _state.affinity_ok is None:
        _state.affinity_ok = ok
        logger.debug(
            f"overlay: SetWindowDisplayAffinity -> {ok} "
            f"(layered windows normally need the capture gate)"
        )
    _present(hwnd, img, x, y)
    if not _state.hidden:
        _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    _state.windows[hwnd] = "window"
    _state.win_rect[hwnd] = (int(x), int(y), img.width, img.height)
    return hwnd


def _present(hwnd: int, img, x: int, y: int) -> None:
    import numpy as np

    w, h = img.size
    arr = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    alpha = arr[:, :, 3].astype(np.uint16)
    pm = np.empty((h, w, 4), dtype=np.uint8)
    pm[:, :, 0] = (arr[:, :, 2].astype(np.uint16) * alpha // 255).astype(np.uint8)
    pm[:, :, 1] = (arr[:, :, 1].astype(np.uint16) * alpha // 255).astype(np.uint8)
    pm[:, :, 2] = (arr[:, :, 0].astype(np.uint16) * alpha // 255).astype(np.uint8)
    pm[:, :, 3] = arr[:, :, 3]
    data = pm.tobytes()

    hdc_screen = _user32.GetDC(None)
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = BI_RGB
    bmi.biSizeImage = w * h * 4
    ppv = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(
        hdc_mem, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(ppv), None, 0
    )
    old = None
    try:
        if not hbmp or not ppv:
            return
        ctypes.memmove(ppv, data, len(data))
        old = _gdi32.SelectObject(hdc_mem, hbmp)
        size = SIZE(w, h)
        src = POINT(0, 0)
        dst = POINT(int(x), int(y))
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        _user32.UpdateLayeredWindow(
            hwnd, hdc_screen, ctypes.byref(dst), ctypes.byref(size),
            hdc_mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA,
        )
    finally:
        if old:
            _gdi32.SelectObject(hdc_mem, old)
        if hbmp:
            _gdi32.DeleteObject(hbmp)
        if hdc_mem:
            _gdi32.DeleteDC(hdc_mem)
        if hdc_screen:
            _user32.ReleaseDC(None, hdc_screen)


def _update_window(hwnd: int, x: int, y: int, w: int, h: int, img) -> None:
    _present(hwnd, img, x, y)
    _state.win_rect[hwnd] = (int(x), int(y), img.width, img.height)
    if not _state.hidden:
        _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def _destroy(hwnd: int) -> None:
    if hwnd and _user32.IsWindow(hwnd):
        _user32.DestroyWindow(hwnd)
    _state.windows.pop(hwnd, None)
    _state.win_rect.pop(hwnd, None)
    _state.transient.pop(hwnd, None)
    _state.hidden.discard(hwnd)
    if _state.hud == hwnd:
        _state.hud = 0
        _state.hud_rect = None
    if _state.toast == hwnd:
        _state.toast = 0
    for token, lst in list(_state.highlights.items()):
        if hwnd in lst:
            lst.remove(hwnd)
            if not lst:
                _state.highlights.pop(token, None)


def _hide_intersecting(rect: Optional[tuple]) -> None:
    for hwnd, (x, y, w, h) in list(_state.win_rect.items()):
        if rect is not None and not _intersects((x, y, x + w, y + h), rect):
            continue
        if _user32.IsWindow(hwnd):
            _user32.ShowWindow(hwnd, SW_HIDE)
            _state.hidden.add(hwnd)


def _restore_hidden() -> None:
    for hwnd in list(_state.hidden):
        if _user32.IsWindow(hwnd):
            _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    _state.hidden.clear()


def _intersects(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


# ── Geometry ──────────────────────────────────────────────────────────────
def _work_area() -> RECT:
    pt = POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    mon = _user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and _user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return mi.rcWork
    return RECT(0, 0, 1920, 1080)


def _bottom_right(w: int, h: int, margin: int = 16) -> tuple:
    wa = _work_area()
    return int(wa.right - w - margin), int(wa.bottom - h - margin)


def _toast_pos(w: int, h: int) -> tuple:
    if _state.hud_rect:
        hx, hy, hw, hh = _state.hud_rect
        return int(hx + hw - w), int(hy - h - 8)
    return _bottom_right(w, h)


# ── Rendering (PIL, lazy imports) ─────────────────────────────────────────
_FONT_CACHE: dict = {}


def _load_font(size: int):
    from PIL import ImageFont

    size = max(8, int(size))
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    font = None
    for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def _round_rect(draw, box, radius, **kw) -> None:
    try:
        draw.rounded_rectangle(box, radius=radius, **kw)
    except AttributeError:  # very old Pillow
        draw.rectangle(box, **kw)


def _rgba(value, alpha: int = 255) -> tuple:
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 6:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)
        if "," in s:
            return tuple(int(p) for p in s.split(",")[:3]) + (alpha,)
        return (255, 255, 255, alpha)
    parts = tuple(int(v) for v in value)
    if len(parts) == 3:
        return parts + (alpha,)
    return parts[:4]


def _norm_rect(rect) -> Optional[tuple]:
    try:
        if isinstance(rect, dict):
            l = rect.get("left", rect.get("x", 0))
            t = rect.get("top", rect.get("y", 0))
            r = rect.get("right", rect.get("x2"))
            b = rect.get("bottom", rect.get("y2"))
            if r is None or b is None:
                return None
            l, t, r, b = int(l), int(t), int(r), int(b)
        else:
            l, t, r, b = (int(v) for v in rect)
    except (TypeError, ValueError):
        return None
    if r <= l or b <= t:
        return None
    return l, t, r, b


def _render_frame(rect, color, thickness, label=None, fill_alpha: int = 0):
    from PIL import Image, ImageDraw

    l, t, r, b = rect
    w, h = max(1, r - l), max(1, b - t)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    th = max(1, int(thickness))
    box = [th // 2, th // 2, w - 1 - th // 2, h - 1 - th // 2]
    if fill_alpha:
        _round_rect(draw, box, radius=6, fill=_rgba(color, fill_alpha))
    _round_rect(draw, box, radius=6, outline=_rgba(color, 235), width=th)
    if label:
        font = _load_font(13)
        pad = 6
        bb = draw.textbbox((0, 0), str(label), font=font)
        tw, tht = bb[2] - bb[0], bb[3] - bb[1]
        x0, y0 = th + 2, th + 2
        x1, y1 = x0 + tw + 2 * pad, y0 + tht + 2 * pad
        _round_rect(draw, [x0, y0, x1, y1], radius=5, fill=_rgba(color, 235))
        draw.text((x0 + pad, y0 + pad - bb[1]), str(label), font=font, fill=(20, 20, 24, 255))
    return img


def _render_ring(radius: int, color, thickness: int):
    from PIL import Image, ImageDraw

    R = max(6, int(radius))
    th = max(1, int(thickness))
    pad = 6
    size = 2 * R + 2 * pad
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    outline = _rgba(color, 235)
    draw.ellipse([th // 2, th // 2, size - 1 - th // 2, size - 1 - th // 2],
                 outline=outline, width=th)
    c = size // 2
    cr = max(2, th)
    draw.ellipse([c - cr, c - cr, c + cr, c + cr], fill=outline)
    return img


def _render_chip(text: str, accent, font_size: int = 16):
    from PIL import Image, ImageDraw

    text = str(text)
    if len(text) > 60:
        text = text[:57] + "..."
    font = _load_font(font_size)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bb = tmp.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    padx, pady, dot = 14, 9, 9
    W = padx * 2 + dot + 8 + tw
    H = pady * 2 + max(th, dot)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    acc = _rgba(accent, 255)
    _round_rect(draw, [0, 0, W - 1, H - 1], radius=10, fill=_BG + (218,), outline=acc, width=2)
    cy = H // 2
    draw.ellipse([padx, cy - dot // 2, padx + dot, cy + dot // 2], fill=acc)
    draw.text((padx + dot + 8, cy), text, font=font, fill=(240, 242, 246, 255), anchor="lm")
    return img


# ── Command implementations (run on the overlay thread) ───────────────────
def _cmd_flash_rect(rect, color, thickness, label, duration) -> None:
    img = _render_frame(rect, color, thickness, label=label)
    hwnd = _create_window(rect[0], rect[1], img.width, img.height, img)
    _state.windows[hwnd] = "rect"
    _state.transient[hwnd] = time.monotonic() + max(0.15, float(duration))


def _cmd_click(x, y, button, duration, radius, thickness) -> None:
    color = _BUTTON_COLORS.get(str(button).lower(), (255, 120, 40))
    img = _render_ring(radius, color, thickness)
    hwnd = _create_window(x - img.width // 2, y - img.height // 2,
                          img.width, img.height, img)
    _state.windows[hwnd] = "click"
    _state.transient[hwnd] = time.monotonic() + max(0.15, float(duration))


def _cmd_toast(text, kind, duration, sticky) -> None:
    img = _render_chip(text, _KIND_COLORS.get(kind, _KIND_COLORS["info"]))
    x, y = _toast_pos(img.width, img.height)
    if _state.toast and _user32.IsWindow(_state.toast):
        hwnd = _state.toast
        _update_window(hwnd, x, y, img.width, img.height, img)
    else:
        hwnd = _create_window(x, y, img.width, img.height, img)
        _state.toast = hwnd
    _state.windows[hwnd] = "toast"
    _state.transient[hwnd] = None if sticky else time.monotonic() + max(0.3, float(duration))


def _cmd_hud(text, kind) -> None:
    if not text:
        if _state.hud:
            _destroy(_state.hud)
        return
    img = _render_chip(text, _KIND_COLORS.get(kind, _KIND_COLORS["info"]))
    x, y = _bottom_right(img.width, img.height)
    if _state.hud and _user32.IsWindow(_state.hud):
        hwnd = _state.hud
        _update_window(hwnd, x, y, img.width, img.height, img)
    else:
        hwnd = _create_window(x, y, img.width, img.height, img)
        _state.hud = hwnd
    _state.windows[hwnd] = "hud"
    _state.hud_rect = (x, y, img.width, img.height)


def _cmd_highlight(token, regions, color, thickness, fill_alpha, duration) -> None:
    for hwnd in list(_state.highlights.get(token, [])):
        _destroy(hwnd)
    handles: list = []
    _state.highlights[token] = handles
    for rect, label in regions:
        try:
            img = _render_frame(rect, color, thickness, label=label, fill_alpha=fill_alpha)
            hwnd = _create_window(rect[0], rect[1], img.width, img.height, img)
        except Exception as e:
            logger.debug(f"overlay: highlight region failed: {e}")
            continue
        _state.windows[hwnd] = "highlight"
        if duration:
            _state.transient[hwnd] = time.monotonic() + float(duration)
        handles.append(hwnd)


def _cmd_clear_highlights(token) -> None:
    if token is None:
        tokens = list(_state.highlights.keys())
    else:
        tokens = [token]
    for tok in tokens:
        for hwnd in list(_state.highlights.get(tok, [])):
            _destroy(hwnd)
        _state.highlights.pop(tok, None)


def _cmd_clear_toast() -> None:
    if _state.toast:
        _destroy(_state.toast)


# ── Public API ────────────────────────────────────────────────────────────
def is_enabled() -> bool:
    return bool(_state.enabled)


def set_enabled(value: bool) -> None:
    _state.enabled = bool(value)


def begin_capture(rect=None) -> None:
    """Hide overlays intersecting ``rect`` for the duration of a capture.

    A no-op when there are no overlays or when the OS already excludes them
    from capture (``SetWindowDisplayAffinity`` succeeded).
    """
    if _state.affinity_ok is True or not _state.windows:
        return
    r = _norm_rect(rect) if rect is not None else None
    done = threading.Event()

    def _cmd() -> None:
        _hide_intersecting(r)
        done.set()

    _enqueue(_cmd)
    done.wait(timeout=0.3)


def end_capture() -> None:
    if _state.affinity_ok is True:
        return
    done = threading.Event()

    def _cmd() -> None:
        _restore_hidden()
        done.set()

    _enqueue(_cmd)
    done.wait(timeout=0.3)


def flash_rect(rect, color=_FRAME_COLOR, thickness: int = 3,
               duration: float = 0.5, label: Optional[str] = None) -> None:
    """Flash a frame around a screen rect (ambient; no-op when disabled)."""
    if not _state.enabled:
        return
    r = _norm_rect(rect)
    if r is None:
        return
    _enqueue(lambda: _cmd_flash_rect(r, color, thickness, label, duration))


def flash_click(x: int, y: int, button: str = "left", duration: float = 0.6,
                radius: int = 26, thickness: int = 3) -> None:
    """Draw a click ring at a screen point (ambient; no-op when disabled)."""
    if not _state.enabled:
        return
    _enqueue(lambda: _cmd_click(int(x), int(y), button, duration, radius, thickness))


def flash_keys(text: str, duration: float = 1.1, kind: str = "keys") -> None:
    """Show a short bottom-right toast for a key/typing action (ambient)."""
    if not _state.enabled or not text:
        return
    _enqueue(lambda: _cmd_toast(str(text), kind, duration, False))


def set_status(text: Optional[str], kind: str = "info") -> None:
    """Set (or clear) the persistent bottom-right status HUD (ambient)."""
    if not _state.enabled and text:
        return
    _enqueue(lambda: _cmd_hud(text, kind))


def notify(text: str, kind: str = "info", duration: float = 3.0, sticky: bool = False) -> None:
    """Show a bottom-right notification toast (ambient; no-op when disabled)."""
    if not _state.enabled or not text:
        return
    _enqueue(lambda: _cmd_toast(str(text), kind, duration, sticky))


def clear_toast() -> None:
    """Clear the current toast (used to dismiss a sticky notification)."""
    _enqueue(_cmd_clear_toast)


def highlight(regions, color=_HIGHLIGHT_COLOR, thickness: int = 3,
              fill_alpha: int = 45, persist: bool = True,
              duration: Optional[float] = None, label: Optional[str] = None) -> Optional[int]:
    """Draw deliberate highlight box(es). Works even when ambient overlay is off.

    ``regions`` is a region or a list of regions; each region is a
    ``[left, top, right, bottom]`` sequence or a dict with ``rect`` / ``hwnd`` /
    ``point`` (+ ``radius``) / ``bbox`` and an optional ``label``. Returns a
    token usable with :func:`clear_highlights`.
    """
    items = regions if isinstance(regions, list) else [regions]
    # A bare 4-item sequence is a single region, not a list of regions.
    if isinstance(regions, (list, tuple)) and len(regions) == 4 and all(
        isinstance(v, (int, float)) for v in regions
    ):
        items = [regions]
    norm = []
    for item in items:
        parsed = _normalize_region(item, label)
        if parsed:
            norm.append(parsed)
    if not norm:
        return None
    with _state.lock:
        _state.token_seq += 1
        token = _state.token_seq
    dur = None if persist else (duration if duration is not None else 4.0)
    _enqueue(lambda: _cmd_highlight(token, norm, color, thickness, fill_alpha, dur))
    return token


def clear_highlights(token: Optional[int] = None) -> None:
    """Clear a specific highlight token, or all highlights when ``token`` is None."""
    _enqueue(lambda: _cmd_clear_highlights(token))


def _normalize_region(item, default_label=None):
    label = default_label
    rect = None
    if isinstance(item, dict):
        label = item.get("label", default_label)
        if "rect" in item:
            rect = item["rect"]
        elif "bbox" in item:
            rect = item["bbox"]
        elif "hwnd" in item:
            try:
                from lib import winapi

                wr = winapi.get_window_rect(int(item["hwnd"]))
                if wr:
                    rect = [wr["left"], wr["top"], wr["right"], wr["bottom"]]
            except Exception:
                rect = None
        elif "point" in item:
            px, py = item["point"]
            rad = int(item.get("radius", 40))
            rect = [int(px) - rad, int(py) - rad, int(px) + rad, int(py) + rad]
    else:
        rect = item
    r = _norm_rect(rect)
    if r is None:
        return None
    return r, label
