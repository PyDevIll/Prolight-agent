# google chrome

## Renderer accessibility (UIA)
- Chromium keeps its renderer accessibility tree **off** until it starts with `--force-renderer-accessibility` (or an AT client connects). Chrome is single-instance per profile, so a **running** browser silently ignores the flag — you must launch a fresh instance on a scratch `--user-data-dir`. Use `browser_launch_accessible(browser="chrome", url=..., profile=...)` (adds `--force-renderer-accessibility` + a scratch profile and returns the main page window, not a bubble).
- Once launched that way, page content is exposed as a UIA **Document** ~7-8 levels below the window (`Window > Pane > … > Document > Text`), deeper than the snapshot's default walk. ProLight reaches it: `win_snapshot` reports `chromium accessibility enabled (N document)`, and `win_read_text(hwnd)` returns the page text with **no OCR** (it targets the Document subtree and its `Text`/`Hyperlink` descendants).
- `win_read_text` on an already-running, non-flagged Chrome (no Document) falls back to OCR — prefer relaunching via `browser_launch_accessible`.
