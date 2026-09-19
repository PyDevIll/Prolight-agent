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
import time
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

MF_BYPOSITION = 0x00000400

# Accessibility / UI Automation activation (used to wake Chromium's a11y tree).
WM_GETOBJECT = 0x003D
UiaRootObjectId = -25  # 0xFFFFFFE7
OBJID_CLIENT = -4  # 0xFFFFFFFC
SMTO_ABORTIFHUNG = 0x0002
SMTO_BLOCK = 0x0001


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


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
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

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
user32.AllowSetForegroundWindow.restype = wintypes.BOOL

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetMenu.argtypes = [wintypes.HWND]
user32.GetMenu.restype = wintypes.HMENU
user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
user32.GetMenuItemCount.restype = ctypes.c_int
user32.GetMenuItemRect.argtypes = [wintypes.HWND, wintypes.HMENU, wintypes.UINT, ctypes.POINTER(RECT)]
user32.GetMenuItemRect.restype = wintypes.BOOL
user32.GetMenuStringW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT]
user32.GetMenuStringW.restype = ctypes.c_int

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
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM

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
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# DPI-awareness APIs
user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]  # DPI_AWARENESS_CONTEXT
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.SetProcessDPIAware.argtypes = []
user32.SetProcessDPIAware.restype = wintypes.BOOL

# dwmapi
if dwmapi is not None:
    dwmapi.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
    ]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long  # HRESULT

# shcore (load once; absent before Win8.1)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    shcore.SetProcessDpiAwareness.restype = ctypes.c_long  # HRESULT
except OSError:  # pragma: no cover
    shcore = None


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
        if shcore is not None and shcore.SetProcessDpiAwareness(2) == 0:  # S_OK
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


def get_client_rect_screen(hwnd: int) -> Optional[dict]:
    """Client area rectangle in screen coordinates (physical pixels)."""
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return {
        "left": origin.x,
        "top": origin.y,
        "right": origin.x + rect.right,
        "bottom": origin.y + rect.bottom,
        "width": rect.right,
        "height": rect.bottom,
    }


def get_menu_item_rect(hwnd: int, index: int = 0) -> Optional[dict]:
    """Screen-coordinate rect of a menu-bar item (index 0 = first item).

    Returns None if the window has no menu or the index is invalid.
    """
    hmenu = user32.GetMenu(hwnd)
    if not hmenu:
        return None
    count = user32.GetMenuItemCount(hmenu)
    if count <= 0 or index < 0 or index >= count:
        return None
    rect = RECT()
    if not user32.GetMenuItemRect(hwnd, hmenu, index, ctypes.byref(rect)):
        return None
    return {
        "left": rect.left,
        "top": rect.top,
        "right": rect.right,
        "bottom": rect.bottom,
        "width": rect.right - rect.left,
        "height": rect.bottom - rect.top,
        "center_x": (rect.left + rect.right) // 2,
        "center_y": (rect.top + rect.bottom) // 2,
        "count": count,
    }


def get_menu_items(hwnd: int) -> list[dict]:
    """List the window's menu-bar items with their text and screen rects.

    Note: the menu bar is NON-client area, so its coordinates lie above
    ``get_client_rect_screen()``. Use these rects to click menu items.
    """
    hmenu = user32.GetMenu(hwnd)
    if not hmenu:
        return []
    count = user32.GetMenuItemCount(hmenu)
    items: list[dict] = []
    for i in range(max(0, count)):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetMenuStringW(hmenu, i, buf, 256, MF_BYPOSITION)
        items.append({
            "index": i,
            "text": buf.value.replace("&", ""),
            "rect": get_menu_item_rect(hwnd, i),
        })
    return items


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


def find_child_by_class(hwnd: int, class_substring: str) -> Optional[int]:
    """Return the first descendant window whose class contains ``class_substring``."""
    needle = (class_substring or "").lower()
    found = {"hwnd": 0}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(child, _lparam):
        if needle and needle in (get_class_name(child) or "").lower():
            found["hwnd"] = int(child)
            return False
        return True

    user32.EnumChildWindows(hwnd, _cb, 0)
    return found["hwnd"] or None


# ── Focus ─────────────────────────────────────────────────────────────────
def _get_window_thread_id(hwnd: int) -> int:
    """Thread id that owns a window (0 on failure)."""
    if not hwnd:
        return 0
    return int(user32.GetWindowThreadProcessId(hwnd, None) or 0)


def get_thread_focus_info(thread_id: int) -> dict:
    """Return the active/focus/caret HWNDs of a thread (via GetGUIThreadInfo).

    ``hwnd_focus`` is the window that will actually receive keyboard input;
    it can be a child of the target window (or 0 when the thread has no focus).
    """
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not thread_id or not user32.GetGUIThreadInfo(int(thread_id), ctypes.byref(info)):
        return {"ok": False, "hwnd_active": 0, "hwnd_focus": 0, "hwnd_caret": 0}
    return {
        "ok": True,
        "hwnd_active": int(info.hwndActive or 0),
        "hwnd_focus": int(info.hwndFocus or 0),
        "hwnd_caret": int(info.hwndCaret or 0),
    }


def focus_window(hwnd: int, retries: int = 3) -> dict:
    """Restore (if needed), bring to foreground and focus a window.

    Windows enforces foreground-lock rules: a process that is not the active
    application may be refused permission to change the foreground window.
    The canonical workaround is to temporarily attach this thread's input
    queue to the foreground (and target) thread's queue, call
    SetForegroundWindow, then detach.

    Success is verified two ways, because being "foreground" is NOT enough for
    keyboard input to arrive:
      * ``ok``             — GetForegroundWindow() == hwnd;
      * ``keyboard_focus`` — the target thread's focus window (GetGUIThreadInfo)
        belongs to that thread (None if it could not be determined).

    Retries the whole sequence a few times, since the first attempt is
    sometimes ignored right after the window is restored.
    """
    hwnd = int(hwnd)
    if not is_window(hwnd):
        return {"ok": False, "error": f"Invalid window handle: {hwnd}"}

    was_minimized = bool(user32.IsIconic(hwnd))
    if was_minimized:
        user32.ShowWindow(hwnd, SW_RESTORE)

    # Best-effort: allow us to set the foreground window (ASFW_ANY = 0xFFFFFFFF).
    try:
        user32.AllowSetForegroundWindow(0xFFFFFFFF)
    except OSError:
        pass

    target_thread = _get_window_thread_id(hwnd)
    cur_thread = int(kernel32.GetCurrentThreadId())
    fg = get_foreground_window()
    keyboard_focus = None
    attempts = 0

    for attempt in range(1, max(1, int(retries)) + 1):
        attempts = attempt
        fg = get_foreground_window()
        if fg == hwnd and keyboard_focus is True:
            break

        fg_thread = _get_window_thread_id(fg)
        attached: list[int] = []
        for other in (fg_thread, target_thread):
            if other and other != cur_thread and other not in attached:
                if user32.AttachThreadInput(cur_thread, other, True):
                    attached.append(other)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            for other in attached:
                user32.AttachThreadInput(cur_thread, other, False)

        fg = get_foreground_window()
        info = get_thread_focus_info(target_thread)
        if info["ok"]:
            focus_hwnd = info["hwnd_focus"]
            keyboard_focus = bool(focus_hwnd) and _get_window_thread_id(focus_hwnd) == target_thread
        else:
            keyboard_focus = None

        if fg == hwnd and keyboard_focus is True:
            break
        time.sleep(0.05)

    info = get_thread_focus_info(target_thread)
    if info["ok"]:
        focus_hwnd = info["hwnd_focus"]
        keyboard_focus = bool(focus_hwnd) and _get_window_thread_id(focus_hwnd) == target_thread
    return {
        "ok": fg == hwnd,
        "hwnd": hwnd,
        "foreground_hwnd": fg,
        "keyboard_focus": keyboard_focus,
        "focus_hwnd": info.get("hwnd_focus", 0),
        "target_thread": target_thread,
        "attempts": attempts,
        "was_minimized": was_minimized,
        "title": get_window_text(hwnd),
        "method": "attach-thread-input",
    }


def verify_keyboard(hwnd: int, probe_key: str = "shift", settle: float = 0.05,
                    retries: int = 3) -> dict:
    """Focus ``hwnd`` and probe that keyboard input is actually delivered.

    Injects a *benign* key (a modifier by default — no text, no command) and
    re-reads the target thread's keyboard focus. Returns ``keyboard_delivery``:

      * ``verified``   — target still holds keyboard focus after an injected key;
      * ``focus_lost`` — it lost focus (keys would go elsewhere);
      * ``unverified`` — focus could not be determined / injection failed.

    Note: this proves focus *survives* an injected key, not that a specific
    application received it — pair it with a UIA/pixel check for the latter.
    """
    focus = focus_window(hwnd, retries=retries)
    result = {
        **focus,
        "mouse_ok": bool(focus.get("ok")),
        "keyboard_ok": focus.get("keyboard_focus") is True,
        "probe_key": probe_key,
    }
    if not focus.get("ok") or focus.get("keyboard_focus") is False:
        result["keyboard_delivery"] = "focus_lost" if focus.get("ok") else "unverified"
        return result

    target_thread = focus.get("target_thread") or _get_window_thread_id(hwnd)
    before = get_thread_focus_info(target_thread)
    try:
        from lib import input_backend as ib  # lazy: avoid import cycle

        ib.key_stroke(probe_key)
    except Exception as e:
        result["keyboard_delivery"] = "unverified"
        result["error"] = str(e)
        return result

    time.sleep(settle)
    after = get_thread_focus_info(target_thread)
    result["focus_before"] = before.get("hwnd_focus", 0)
    result["focus_after"] = after.get("hwnd_focus", 0)
    result["caret_hwnd"] = after.get("hwnd_caret", 0)
    if after.get("ok") and after.get("hwnd_focus") and \
            _get_window_thread_id(after["hwnd_focus"]) == target_thread:
        result["keyboard_delivery"] = "verified"
    else:
        result["keyboard_delivery"] = "focus_lost"
    return result


# ── Accessibility / UIA activation ────────────────────────────────────────
def send_getobject(hwnd: int, objid: int = UiaRootObjectId, timeout_ms: int = 1000) -> dict:
    """Send WM_GETOBJECT to a window to wake its accessibility provider.

    Chromium/Electron keep their accessibility tree off until an assistive
    client requests the UIA root object; sending this can turn it on at runtime.
    Returns ``{ok, hwnd, objid}``.
    """
    hwnd = int(hwnd)
    if not is_window(hwnd):
        return {"ok": False, "error": f"Invalid window handle: {hwnd}"}
    out = ctypes.c_size_t(0)
    try:
        ret = user32.SendMessageTimeoutW(
            hwnd, WM_GETOBJECT, 0, int(objid),
            SMTO_ABORTIFHUNG | SMTO_BLOCK, int(timeout_ms), ctypes.byref(out),
        )
        return {"ok": True, "hwnd": hwnd, "objid": int(objid), "result": int(ret or 0)}
    except Exception as e:  # pragma: no cover - env-dependent
        return {"ok": False, "hwnd": hwnd, "objid": int(objid), "error": str(e)}


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
        # PW_RENDERFULLCONTENT handles occluded/DWM-composited windows.
        # Fall back to the legacy flag, but fail cleanly if BOTH fail —
        # otherwise GetDIBits would hand back an uninitialized bitmap.
        if not user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT):
            if not user32.PrintWindow(hwnd, hdc_mem, 0):
                return None

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = width
        bmi.biHeight = -height  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = BI_RGB
        bmi.biSizeImage = width * height * 4

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
