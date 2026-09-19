## **TOOLS GUIDELINES & BEST PRACTICES**

### The core loop: snapshot → act → changes
1. **Find the window** → `win_list_hwnd` (optionally `process_filter="chrome"`).
2. **Snapshot it once** → `win_snapshot(hwnd=...)` (or no args for the foreground window). This one call returns identity, focus, menu, UIA controls and OCR text, each with an id (`c7`/`t3`/`m2`).
3. **Act by id** → `win_click_control(id="c7")`, `win_set_control_text(id="c3", text=...)`, `mouse_click(id="t3")`, or type into it.
4. **Verify cheaply** → `win_changes()` returns only the delta (added/removed/changed elements + pixel bbox). No new snapshot needed.

Do **not** call many separate perception tools — the snapshot already contains them.

### Choosing tools
- **Understand a window** → `win_snapshot(hwnd=...)`. Add `vision_query="..."` only if you need a semantic description (opt-in; kept out of your context).
- **Act by id** → prefer the ids from the snapshot.
- **Click a control** → `win_click_control(id="c7")`; if you have no snapshot, `win_click_control(hwnd=..., name="OK", control_type="Button")`.
- **Set a field directly** → `win_set_control_text(id="c3", text="...")` (UIA ValuePattern, no typing).
- **Wait for a dialog/control** → `win_changes(wait_for="Save", timeout=10)`.
- **Focus + verify keyboard** → `win_ensure_foreground(hwnd)` (returns `keyboard_delivery`). `win_focus(hwnd)` alone only guarantees foreground.
- **Locate a colour/blob** → `screen_find(kind="color", color=[r,g,b], hwnd=...)`; click `screen_center`.
- **Locate an icon** → `screen_find(kind="template", template_path=..., hwnd=...)`.
- **Locate a label/button by text** → `screen_find(kind="text", text="Save", hwnd=...)` (OCR, deterministic).
- **Semantic look / verify** → `vision_look(rect=... | hwnd=... , query=..., label=...)`; `vision_compare(label, pixel_only=true)` first for a cheap check.
- **Structured element search** → `vision_look(..., structured=true)` returns approximate boxes (fractions of the crop, snapped to pixels) — hints only; verify before clicking.
- **Enter text** → focus/verify, then `keybd_type(text, hwnd=...)`.
- **Shortcuts** → `keybd_hotkey("ctrl+s", hwnd=...)`.
- **Exact/long values** → `clipboard_set(text)` then `keybd_hotkey("ctrl+v")`.
- **Scroll** → `mouse_wheel(amount)` (positive = up).
- **Standard background controls** → `win_send_message` (fallback; no focus change).
- **Editing files:** always `fs_read` first, then use `fs_aedit` or `fs_edit_blocks` with `dry_run=True` to preview changes, then apply without `dry_run`.

### Discipline
- Focus first: `win_ensure_foreground(hwnd)` and check `keyboard_delivery` (mouse clicks can work while keyboard silently does not).
- Pass `hwnd` to `keybd_*` when you know the target: it re-checks focus and refuses loudly instead of typing into the wrong window.
- When UIA/OCR expose nothing useful (`uia.coverage` poor/empty), locate with `screen_find` rather than trusting vision pixel coordinates.
- After each action, verify with `win_changes()` (cheap) before the next step. Do not chain many blind actions.
- If a tool returns an error or empty result, read it and adjust; do not repeat the identical call more than twice.
- Never perform destructive or irreversible actions (delete, send, purchase, submit) without explicit confirmation.

### Efficiency
- One `win_snapshot` beats several perception calls; then `win_changes` is the cheap loop.
- Ask targeted questions of the vision model rather than "describe everything".
- Batch read-only observations before making a change.

### Learning
- When you figure out how an application behaves, offer to save a concise fact to its interaction guide.
- When you complete (or are taught) a repeatable procedure, offer to save it as a workflow.
