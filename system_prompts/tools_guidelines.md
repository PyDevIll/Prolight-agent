## **TOOLS GUIDELINES & BEST PRACTICES**

### Choosing tools (current set)
- **Find a window** → `win_list_hwnd` (optionally with `process_filter`, e.g. `"notepad"`). Then `win_get_info` to confirm you have the right one.
- **Understand a window** → `win_see(hwnd, query)` — capture + vision in one call. Ask a specific question.
- **Get raw pixels / keep an image** → `win_get_image(hwnd)` returns a path; then `vision_analyze(path, query)`.
- **See the whole screen** → `win_get_screenshot()`.
- **Focus a window** → `win_focus(hwnd)` (restores if minimized). Verify with `win_get_info` (`foreground: true`).

### Discipline
- Start every task by listing windows; never assume an HWND.
- Confirm the window title/process before acting on it.
- If a tool returns an error or empty result, read it carefully and adjust; do not retry the identical call more than twice.
- Read-only observations are cheap enough; batch several lookups before making a change.

### Efficiency
- Prefer `win_see` over `win_get_image` + `vision_analyze` (one round-trip).
- Ask targeted questions of the vision model rather than "describe everything".
- Keep images small: the tools downscale automatically; don't request oversized captures.

### Learning
- When you figure out how an application behaves, offer to save a concise fact to its interaction guide.
- When you complete (or are taught) a repeatable procedure, offer to save it as a workflow.
