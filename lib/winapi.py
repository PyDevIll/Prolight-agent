"""Low-level Windows API wrappers for ProLight-agent (ctypes).

Pure-ctypes layer over user32/gdi32/kernel32/dwmapi:
  - per-monitor DPI awareness
  - window enumeration and metadata (incl. process name)
  - window focus / restore
  - window pixel capture via PrintWindow (works for occluded windows)
  - cursor position helpers

No third-party dependencies. Coordinates are physical pixels once
``ensure_dpi_awareness()`` has been called (done automatically on import).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
except OSError:  # pragma: no cover
    dwmapi = None

# ── Constants ─────────────────────────────────────────────────────────────
PW_RENDERFULLCONTENT = 0x00000002

SW_RESTORE = 9
SW_SHOW = 5

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

DIB_RGB_COLORS = 0
BI_RGB = 0

DWMWA_CLOAKED = 14


# ── Structs ───────────────────────────────────────────────────────────────
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


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


# ── Prototypes ────────────────────────────────────────────────────────────
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL

user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND

user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    wintypes.LPVOID, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


# ── DPI awareness ─────────────────────────────────────────────────────────
_dpi_aware = False


def ensure_dpi_awareness() -> bool:
    """Make this process per-monitor DPI aware (v2 if available).

    Must run before any window/DC is created. Idempotent.
    Returns True if any awareness level was set successfully.
    """
    global _dpi_aware
    if _dpi_aware:
        return True

    # 1) Per-monitor v2 (Win10 1703+): DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    try:
        ctx = ctypes.c_void_p(-4)
        if user32.SetProcessDpiAwarenessContext(ctx):
            _dpi_aware = True
            return True
    except (AttributeError, OSError):
        pass

    # 2) Per-monitor (Win8.1+): PROCESS_PER_MONITOR_DPI_AWARE = 2
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        if shcore.SetProcessDpiAwareness(2) == 0:  # S_OK
            _dpi_aware = True
            return True
    except (AttributeError, OSError):
        pass

    # 3) System DPI aware (Vista+)
    try:
        if user32.SetProcessDPIAware():
            _dpi_aware = True
            return True
    except (AttributeError, OSError):
        pass

    return False


ensure_dpi_awareness()


# ── Helpers ───────────────────────────────────────────────────────────────
def get_window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def get_process_name(pid: int) -> str:
    """Return the executable base name for a PID, or '' on failure."""
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def get_window_rect(hwnd: int) -> Optional[dict]:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return {
        "left": rect.left,
        "top": rect.top,
        "right": rect.right,
        "bottom": rect.bottom,
        "width": rect.right - rect.left,
        "height": rect.bottom - rect.top,
    }


def is_cloaked(hwnd: int) -> bool:
    """True for cloaked windows (hidden UWP ghost windows, other desktops)."""
    if dwmapi is None:
        return False
    value = wintypes.DWORD(0)
    try:
        res = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), wintypes.DWORD(DWMWA_CLOAKED),
            ctypes.byref(value), ctypes.sizeof(value),
        )
        return res == 0 and value.value != 0
    except OSError:
        return False


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


# ── Window enumeration ────────────────────────────────────────────────────
def get_window_info(hwnd: int) -> dict:
    """Return a metadata dict for a single window handle."""
    pid = get_window_pid(hwnd)
    return {
        "hwnd": int(hwnd),
        "title": get_window_text(hwnd),
        "class": get_class_name(hwnd),
        "pid": pid,
        "process": get_process_name(pid),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "minimized": bool(user32.IsIconic(hwnd)),
        "maximized": bool(user32.IsZoomed(hwnd)),
        "foreground": int(hwnd) == get_foreground_window(),
        "cloaked": is_cloaked(hwnd),
        "rect": get_window_rect(hwnd),
    }


def list_windows(visible_only: bool = True, titled_only: bool = True) -> list[dict]:
    """Enumerate top-level windows, newest/top-most first is not guaranteed.

    Args:
        visible_only: skip invisible windows.
        titled_only: skip windows with an empty title.
    """
    results: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        if is_cloaked(hwnd):
            return True
        title = get_window_text(hwnd)
        if titled_only and not title:
            return True
        info = get_window_info(hwnd)
        info["title"] = title
        results.append(info)
        return True

    user32.EnumWindows(_cb, 0)
    return results


# ── Focus ─────────────────────────────────────────────────────────────────
def focus_window(hwnd: int) -> dict:
    """Restore (if needed), bring to foreground and focus a window.

    Returns a dict describing the outcome. Windows enforces foreground-lock
    rules, so success is verified via GetForegroundWindow().
    """
    if not is_window(hwnd):
        return {"ok": False, "error": f"Invalid window handle: {hwnd}"}

    was_minimized = bool(user32.IsIconic(hwnd))
    if was_minimized:
        user32.ShowWindow(hwnd, SW_RESTORE)

    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetActiveWindow(hwnd)
    user32.SetFocus(hwnd)

    fg = get_foreground_window()
    ok = (fg == int(hwnd))
    if not ok:
        # Retry once after a restore/show, common when focus was stolen.
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetForegroundWindow(hwnd)
        fg = get_foreground_window()
        ok = (fg == int(hwnd))

    return {
        "ok": ok,
        "hwnd": int(hwnd),
        "foreground_hwnd": fg,
        "was_minimized": was_minimized,
        "title": get_window_text(hwnd),
    }


# ── Capture ───────────────────────────────────────────────────────────────
def capture_window(hwnd: int, client_only: bool = False) -> Optional[dict]:
    """Capture a window's pixels via PrintWindow (PW_RENDERFULLCONTENT).

    Works for occluded windows; restores minimized windows first.
    Returns {width, height, bgra: bytes} or None on failure.
    """
    if not is_window(hwnd):
        return None

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    rect = get_window_rect(hwnd)
    if not rect or rect["width"] <= 0 or rect["height"] <= 0:
        return None

    width, height = rect["width"], rect["height"]

    hdc_window = user32.GetWindowDC(hwnd)
    if not hdc_window:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    if not hdc_mem or not hbmp:
        user32.ReleaseDC(hwnd, hdc_window)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        if hbmp:
            gdi32.DeleteObject(hbmp)
        return None

    old_obj = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        flags = PW_RENDERFULLCONTENT
        if not user32.PrintWindow(hwnd, hdc_mem, flags):
            # Fallback to legacy behaviour
            user32.PrintWindow(hwnd, hdc_mem, 0)

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = width
        bmi.biHeight = -height  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = BI_RGB

        buflen = width * height * 4
        buffer = ctypes.create_string_buffer(buflen)
        scanned = gdi32.GetDIBits(
            hdc_mem, hbmp, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS
        )
        if scanned == 0:
            return None
        return {"width": width, "height": height, "bgra": buffer.raw}
    finally:
        gdi32.SelectObject(hdc_mem, old_obj)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)


# ── Cursor ────────────────────────────────────────────────────────────────
def get_cursor_pos() -> dict:
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return {"x": pt.x, "y": pt.y}


def set_cursor_pos(x: int, y: int) -> bool:
    return bool(user32.SetCursorPos(int(x), int(y)))
