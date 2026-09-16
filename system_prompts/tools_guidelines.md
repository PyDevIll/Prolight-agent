## **TOOLS GUIDELINES & BEST PRACTICES**

### Choosing tools
- **Find a window** → `win_list_hwnd` (optionally `process_filter="notepad"`). Confirm with `win_get_info`.
- **Understand a window** → `win_see(hwnd, query)` (capture + vision in one call; ask a specific question).
- **Raw pixels / keep an image** → `win_get_image(hwnd)` then `vision_analyze(path, query)`.
- **Whole screen** → `win_get_screenshot()`.
- **Focus** → `win_focus(hwnd)` (restores if minimized). Verify `foreground: true`.
- **Click a control** → locate its coordinates from an image, then `mouse_click(x, y)`.
- **Enter text** → `win_focus` the window/edit, then `keybd_type(text)`.
- **Shortcuts** → `keybd_hotkey("ctrl+s")`.
- **Exact/long values** → `clipboard_set(text)` then `keybd_hotkey("ctrl+v")`.
- **Scroll** → `mouse_wheel(amount)` (positive = up).
- **Standard background controls** → `win_send_message` (fallback; no focus change).

### Discipline
- `win_focus` before any mouse/keyboard action; verify it took effect.
- After each action, verify with a fresh capture. Do not chain many blind actions.
- If a tool returns an error or empty result, read it and adjust; do not repeat the identical call more than twice.
- Never perform destructive or irreversible actions (delete, send, purchase, submit) without explicit confirmation.

### Efficiency
- Prefer `win_see` over `win_get_image` + `vision_analyze` (one round-trip).
- Ask targeted questions of the vision model rather than "describe everything".
- Batch read-only observations before making a change.

### Learning
- When you figure out how an application behaves, offer to save a concise fact to its interaction guide.
- When you complete (or are taught) a repeatable procedure, offer to save it as a workflow.
