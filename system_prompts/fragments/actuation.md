# ACTUATION — focus first, then input

## Focus
- `win_focus(hwnd)` — bring a window to the foreground (restores if minimized). Returns `ok` and `keyboard_focus`.
- `win_ensure_foreground(hwnd, click_title=false)` — focus and **verify keyboard delivery** by injecting a benign key; returns `keyboard_delivery: verified|focus_lost|unverified`. Prefer this before typing.
- SendInput only reaches the foreground window, and only the focused control gets keys. Mouse clicks can still work when keyboard focus is missing — check `keyboard_delivery` before typing.
- Pass `hwnd` to `keybd_*` when you know the target: it re-checks focus and refuses loudly instead of typing into the wrong window.

## Mouse
- `mouse_moveto(x, y)` — move the pointer visibly.
- `mouse_click(x, y, button, clicks)` or `mouse_click(id="t3")` — click coordinates or an element's centre. Omit x/y/id to click in place.
- `mouse_down` / `mouse_up` — hold/release a button.
- `mouse_wheel(amount)` — scroll (positive = up); returns `screen_changed` (false = the wheel did nothing, e.g. at the boundary).
- `mouse_drag(x1, y1, x2, y2)` — drag with a button held.
- `mouse_get_pos` — current pointer position.

## Keyboard
- `keybd_type(text, hwnd=...)` — type text into the focused control (layout-independent, supports Cyrillic). Newline=Enter, Tab=Tab.
- `keybd_stroke(key, hwnd=...)` — one key by name (`enter`, `tab`, `esc`, `f5`, `left`, `delete`, ...).
- `keybd_hotkey("ctrl+s", hwnd=...)` — a key combination (preferred over hold/release for shortcuts).
- `keybd_down` / `keybd_up` — genuine key holds.
- `clipboard_set` / `clipboard_get` — copy/paste exact values; paste with `keybd_hotkey("ctrl+v")`. `clipboard_get` caps at `max_chars` (use `full=true` for all); for very long text use `clipboard_save(path=...)` and read it with `fs_read`.

## Standard controls (fallback)
- `win_send_message` — synthetic WM_ messages to a window (no cursor move, no focus change); standard Win32 controls only. Browsers/Electron/custom UIs ignore it — prefer mouse_*/keybd_*.
