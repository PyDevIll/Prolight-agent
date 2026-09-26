# LAUNCHING APPS

- `app_launch(path, args="", cwd="")` — start an executable (full path, or a name on PATH) with optional arguments.

**Reading a Chromium page:** page content reaches UIA only when the browser runs with `--force-renderer-accessibility`, and a browser that is **already running silently ignores the flag**. So relaunch it (closing the running instance first is fine) — via Win+R or:

`app_launch(path="...\\chrome.exe", args="--force-renderer-accessibility <url>")`

Keep the **default profile** (do *not* pass `--user-data-dir`) so the user stays logged in. Then read the page with `win_read_text(hwnd)` (no OCR); `win_snapshot` reports `chromium accessibility enabled (N document)` once the renderer is exposed.
