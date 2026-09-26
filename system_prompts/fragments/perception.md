# PERCEPTION — snapshot once, then track changes

1. `win_list_hwnd` — enumerate top-level windows (HWND, title, process, rect). Use it to find the window you need.
2. `win_snapshot(hwnd=...)` — **the main perception call.** One call returns the window identity, keyboard focus, menu-bar items, UI Automation controls and OCR text, each with a short **id** (`c7`/`t3`/`m2`). Defaults to the foreground window. It reports `uia.coverage` and, for Chromium apps with an empty tree, tries to enable accessibility.
3. `win_changes(label=...)` — **only what changed** since the last snapshot: added/removed/changed elements (with ids) and a pixel-diff bbox. Use it after an action to verify the effect. `label` may be your own name or the `snapshot_id` that `win_snapshot` returned. With `wait_for="Save"` it polls until that element/text appears.
4. `win_read_text(hwnd=...)` — read a window's text via UI Automation TextPattern (no OCR); good for reading a Chromium page/list/thread. Falls back to OCR if the tree is empty.

**Act by id.** After `win_snapshot`, pass the element id to `win_click_control(id="c7")`, `win_set_control_text(id="c3", text="...")` or `mouse_click(id="t3")` — no need to re-describe the element.

Do **not** call many separate perception tools — one `win_snapshot` already contains them.

## Coordinates
- The process is per-monitor DPI aware, so window rectangles and cursor coordinates share one space. Do not rescale.
- Snapshot images may be downscaled; the snapshot reports `image.image_size`, `screen_rect` and `scale`, and every locator returns SCREEN coordinates directly. Never compute screen pixels from an image by hand.
- Multi-monitor: coordinates may be negative or beyond the primary monitor; the whole virtual screen is valid.

## Reliability
- Elevated (administrator) windows cannot be interacted with from this process (UIPI). If a task needs admin, tell the user.
- UI Automation is the most reliable locator for native Win32 controls. Browsers, Electron, 1C and custom-drawn UIs often expose a poor or empty tree (`uia.coverage` = poor/empty) — fall back to OCR (snapshot text / `screen_find` text) or colour/template search.
- Chromium accessibility is often off; try `win_read_text` (it makes an enable attempt) before the snapshot OCR.
- Snapshots are relatively expensive — snapshot the window you care about, then use `win_changes`.
- If a tool returns an error or an empty result, read it and adjust; do not repeat the identical call more than twice.
- Batch read-only observations before making a change.
