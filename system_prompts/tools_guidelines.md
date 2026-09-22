## **TOOLS GUIDELINES & BEST PRACTICES**

### The core loop: snapshot → act → changes
1. **Find the window** → `win_list_hwnd` (optionally `process_filter="chrome"`).
2. **Snapshot it once** → `win_snapshot(hwnd=...)` (or no args for the foreground window). This one call returns identity, focus, menu, UIA controls and OCR text, each with an id (`c7`/`t3`/`m2`).
3. **Act by id** → `win_click_control(id="c7")`, `win_set_control_text(id="c3", text=...)`, `mouse_click(id="t3")`, or type into it.
4. **Verify cheaply** → `win_changes()` returns only the delta (added/removed/changed elements + pixel bbox). No new snapshot needed. `label` accepts the `snapshot_id` from step 2 as well as your own name.

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
- **Read a page/control's text** → `win_read_text(hwnd=...)` (UIA TextPattern, no OCR; reads Chromium lists/threads).
- **Semantic look / verify** → `vision_look(rect=... | hwnd=... , query=..., label=...)`; `vision_compare(label, pixel_only=true)` first for a cheap check. A later `vision_look(label=...)` reuses the watched region.
- **Structured element search** → `vision_look(..., structured=true)` returns approximate boxes (fractions of the crop, snapped to pixels) — hints only; verify before clicking.
- **Enter text** → focus/verify, then `keybd_type(text, hwnd=...)`.
- **Shortcuts** → `keybd_hotkey("ctrl+s", hwnd=...)`.
- **Exact/long values** → `clipboard_set(text)` then `keybd_hotkey("ctrl+v")`; read back with `clipboard_get` (or `clipboard_save` + `fs_read` for long text).
- **Scroll** → `mouse_wheel(amount)` (positive = up); check `screen_changed` (false = nothing scrolled).
- **Standard background controls** → `win_send_message` (fallback; no focus change).
- **Learn an app** → `load_interaction_guide(hwnd=...)`; if missing, discover (`win_snapshot` + `vision_look` + `screen_probe(hover)`) and `save_interaction_guide`.
- **Discover what a control does** → `screen_probe(x, y, action="hover")` (safe pixel diff); for a click use `action="click", undo_hotkey="ctrl+z"`; use `action="scroll"` to test if an area is scrollable.
- **Ask the user** → `ask_user(question, options=[...])` before an uncertain or state-changing step. To ask *about* a screen area, pass `highlight=[...]` (+ `highlight_label=...`) so it stays outlined until they answer.
- **Show the user a screen area** → `highlight_area(regions=[...], label="...")` draws labelled box(es) on screen (visible to the user, never in your captures); clear with `highlight_clear(token)`.
- **Find/follow a procedure** → `find_workflow(query=...)`, then `load_workflow(name)`; save with `save_workflow`.
- **Learn from the user** → `start_learning_session(label=...)`, let them perform the task, then `stop_learning_session()` and summarize into a workflow.
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
- Vision calls are one-shot (no memory): reuse a `label`/watch or restate the needed context in `query`; use `vision_compare(pixel_only=true)` for change checks instead of a second look.
- Batch read-only observations before making a change.

### Learning
- Before the first real interaction with an app, `load_interaction_guide`; if none exists, do a discovery pass and save a general guide.
- When you figure out how an application behaves, record it (`save_interaction_guide` / `note_fact`).
- Before a task, `find_workflow`/`load_workflow`; when you complete (or are taught) a repeatable procedure, save it as a workflow.
- Ask the user (`ask_user`) rather than guessing, and confirm before state-changing clicks.
