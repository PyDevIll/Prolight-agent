# Google Chrome (chrome.exe) — interaction guide

## Window / focus
- Find with win_list_hwnd(process_filter="chrome"). Title = "<page title> - Google Chrome".
- win_focus usually gives real keyboard focus for Chrome (unlike MS Paint). If shortcuts
  misbehave, click once inside the page.

## UI Automation
- Web content exposes NOTHING via UIA (win_snapshot reports `uia.coverage: empty`; no
  Edit/Document controls) because Chrome accessibility is disabled. You cannot click web
  elements by name. win_snapshot still returns the page's OCR **text** — use it to read
  labels and locate them.
- Locate elements from screenshots. Ask the vision model for FRACTIONS of a crop (never
  absolute px), then VERIFY by re-capturing the region. `win_snapshot(vision_query=...,
  structured=true)` already returns approximate boxes mapped to SCREEN coordinates.

## Coordinates
- Window is maximised: rect left/top = (-8,-8), size 1936x1066.
- win_snapshot's image is DOWNSCALED (e.g. 1600 wide for this window); it reports
  `image.image_size`, `image.screen_rect` and `image.scale` so any image point maps back.
  Prefer the screen coordinates returned by locators/vision over computing them by hand.

## Handy shortcuts (need keyboard focus on the Chrome window)
- Ctrl+L or F6 : focus the address bar (type URL, Enter to navigate).
- Ctrl+T new tab; Ctrl+W close tab; Ctrl+Tab / Ctrl+Shift+Tab next/prev tab.
- Ctrl+<n> : switch DIRECTLY to tab n  (reliable — no need to click tab pixels).
- Ctrl+F find in page; Ctrl+R reload; Esc stop loading.

## Tips
- Read tab titles from a screenshot, but switch tabs with Ctrl+<n>.
- To verify typed text, re-capture the field and ask "read the text verbatim".
- Enter submits in most web chat / AI inputs; Shift+Enter = newline.
- Escape dismisses most overlays/menus.
