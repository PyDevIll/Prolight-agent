# Google Chrome — interaction guide

## Purpose / overview
- Web browser that hosts arbitrary web apps (pages, web messengers, web mail). The same
  `chrome.exe` process hosts many different apps — identify the actual app from the page
  title / URL, not from the process. For a specific web app use its own guide (e.g.
  `max.md`) and pass an explicit `name` to `load_interaction_guide`.

## Window & focus
- Find with `win_list_hwnd(process_filter="chrome")`. Title = `"<page title> - Google Chrome"`.
- `win_ensure_foreground(hwnd)` usually gives real keyboard focus (unlike some custom apps).
- Process `chrome.exe`; window class `Chrome_WidgetWin_1`.

## Main areas
- **Tab strip** (top): one tab per open page.
- **Address / omnibox bar**: back/forward, reload, URL, and the extensions area.
- Optional **bookmarks bar** under the address bar.
- **Page body**: everything here is drawn by the web page itself.
- Window controls at the top-right.

## UI Automation
- Web content exposes NOTHING via UIA (`win_snapshot` reports `uia.coverage: empty`) unless
  accessibility is on. Use the **OCR text** in `win_snapshot` to read and locate page labels.

## Key controls & what they do (approximate, maximized)
- Address bar: a wide field near the top (~y 60); `Ctrl+L` focuses it.
- New tab: the "+" at the right end of the tab strip.
- Tab titles are readable from a screenshot; switch tabs with `Ctrl+<n>` / `Ctrl+Tab`.

## Shortcuts
- `Ctrl+L` / `F6` — focus the address bar. `Ctrl+T` new tab; `Ctrl+W` close tab.
- `Ctrl+Tab` / `Ctrl+Shift+Tab` — next/prev tab. `Ctrl+<n>` — go directly to tab n.
- `Ctrl+F` find in page; `Ctrl+R` reload; `Esc` stop loading / dismiss overlays.

## Reading app state
- The address bar shows the current URL; the tab title shows the page. Read them from a
  screenshot / OCR. A spinner on the tab means "loading".

## Gotchas
- Do **not** switch tabs by clicking tab pixels — use `Ctrl+<n>` / `Ctrl+Tab` (needs
  keyboard focus).
- On this machine the window is maximized with rect left/top = (-8,-8); sizes differ per
  machine.
- Snapshots may be downscaled: `win_snapshot` reports `image.image_size`, `screen_rect`
  and `scale` — use returned screen coordinates, never compute them by hand.

## Last verified
- 2026-09-20
