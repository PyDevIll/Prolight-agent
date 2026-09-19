## **TOOLS GUIDELINES & BEST PRACTICES**

### Choosing tools
- **Find a window** → `win_list_hwnd` (optionally `process_filter="notepad"`). Confirm with `win_get_info`.
- **Understand a window** → `win_see(hwnd, query)` (capture + vision in one call; ask a specific question).
- **Raw pixels / keep an image** → `win_get_image(hwnd)` then `vision_analyze(path, query)`.
- **Whole screen** → `win_get_screenshot()`.
- **Focus** → `win_focus(hwnd)` (restores if minimized). Verify `foreground: true` **and** `keyboard_focus: true`. If `keyboard_focus` is false, use `win_ensure_foreground(hwnd, click_title=true)`.
- **Check what will receive keys** → `win_get_foreground()`.
- **List controls** → `win_enum_controls(hwnd)` (tree) or `win_get_control_rects(hwnd)` (flat rects).
- **Find a control by name** → `win_find_controls(hwnd, name="OK", control_type="Button")`.
- **Click a control** → `win_click_control(hwnd, name="OK")` (UIA). If UIA finds nothing, locate coordinates from an image and `mouse_click(x, y)`.
- **Wait for a control** → `win_wait_for(hwnd, name="...")` (dialogs, loading lists).
- **Set a field directly** → `win_set_control_text(hwnd, text="...", name="...")` (UIA ValuePattern, no typing).
- **Verify a control's state** → `vision_look(hwnd=..., control_type="CheckBox", query="Is it ticked?", label="cb1")`.
- **Verify a change** → `vision_changed("cb1")` (cheap, no vision call), then `vision_compare("cb1")` if it changed.
- **Look at a region** → `vision_look(rect=[left,top,right,bottom], query="...", label="...")` (crop tightly; use `scale` to zoom).
- **Locate a solid-colour element (no vision)** → `screen_find_color(color=[r,g,b], hwnd=...)`; click the returned `screen_center`. Deterministic — prefer this for palette swatches, coloured marks, status dots.
- **Locate a small icon/template** → `screen_find_template(template_path=..., hwnd=...)`.
- **Enter text** → `win_focus` the window/edit, then `keybd_type(text, hwnd=...)`.
- **Shortcuts** → `keybd_hotkey("ctrl+s", hwnd=...)`.
- **Exact/long values** → `clipboard_set(text)` then `keybd_hotkey("ctrl+v")`.
- **Scroll** → `mouse_wheel(amount)` (positive = up).
- **Standard background controls** → `win_send_message` (fallback; no focus change).

### Discipline
- `win_focus` before any mouse/keyboard action; verify `keyboard_focus` took effect (foreground alone is not enough for keyboard).
- Pass `hwnd` to `keybd_*` when you know the target: it re-checks focus and refuses loudly instead of typing into the wrong window.
- When UIA exposes nothing (custom-drawn apps), locate elements with `screen_find_color`/`screen_find_template` rather than trusting vision pixel coordinates.
- After each action, verify: `vision_changed(label)` first (cheap), then `vision_compare(label)` to describe the change. Do not chain many blind actions.
- If a tool returns an error or empty result, read it and adjust; do not repeat the identical call more than twice.
- Never perform destructive or irreversible actions (delete, send, purchase, submit) without explicit confirmation.

### Efficiency
- Prefer `win_see` over `win_get_image` + `vision_analyze` (one round-trip).
- Ask targeted questions of the vision model rather than "describe everything".
- Batch read-only observations before making a change.

### Learning
- When you figure out how an application behaves, offer to save a concise fact to its interaction guide.
- When you complete (or are taught) a repeatable procedure, offer to save it as a workflow.
