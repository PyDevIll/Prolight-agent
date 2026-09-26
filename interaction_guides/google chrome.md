# google chrome

Path (this machine): `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe`

## Renderer accessibility (UIA)
- Chromium only builds its accessibility tree when it runs with `--force-renderer-accessibility`, and a browser that is **already running silently ignores the flag**. To interact with web pages smoothly, **relaunch Chrome with the flag** (closing the running instance first is fine): `app_launch(path=..., args="--force-renderer-accessibility <url>")` or Win+R.
- Keep the **default profile** (no `--user-data-dir`) so the user stays logged in.
- Once running with the flag, the page is exposed as a UIA **Document** ~7-8 levels below the window (`Window > Pane > … > Document > Text`). `win_snapshot` reports `chromium accessibility enabled (N document)`, and `win_read_text(hwnd)` returns the page text with **no OCR** (Document subtree).
- If Chrome runs **without** the flag, `win_read_text` falls back to OCR (after a best-effort runtime `WM_GETOBJECT` enable) — relaunch with the flag instead for smooth interaction.
