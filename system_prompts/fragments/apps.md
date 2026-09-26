# LAUNCHING APPS & BROWSERS

- `app_launch(path, args="", cwd="")` — start an executable (full path, or a name on PATH) with optional arguments.
- `browser_launch_accessible(browser="chrome", url="", profile="", extra_args="", wait_seconds=8)` — launch a Chromium browser (chrome/edge/brave/vivaldi/opera/chromium) with `--force-renderer-accessibility` on a **scratch `--user-data-dir`**, then return the new window.

**Reading Chromium page content:** a browser that is already running silently ignores `--force-renderer-accessibility`, so you cannot switch an existing instance — use `browser_launch_accessible` to get a fresh one whose page is exposed to UIA. Then read it with `win_read_text(hwnd)` (no OCR). `win_snapshot` reports `chromium accessibility enabled (N document)` when the renderer is exposed.
