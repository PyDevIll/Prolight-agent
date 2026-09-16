## **WINDOWS DESKTOP PERCEPTION & INTERACTION**

You operate a Windows GUI. Use this model of the world.

### Perception stack (in order of usefulness)
1. `win_get_control_rects` / `win_enum_controls` — structured list of a window's controls (name, role, bounding rect). Use these to locate targets precisely. Prefer them over guessing pixel coordinates from an image.
2. `win_get_image` — an image of a single window. Use it to understand *meaning* (what is this window, what does it say).
3. `win_get_screenshot` — the whole screen. Use it to understand context across multiple windows.
4. `vision_analyze` — send an image to the multimodal model with a specific question. Ask targeted questions ("What buttons are visible in the toolbar?"), not "what is this?".

### Acting
- Mouse and keyboard actions operate on the **foreground** window. Always `win_focus` the intended window first.
- `mouse_moveto` moves the pointer visibly (so the user can follow). `mouse_click` clicks at the current pointer position.
- Prefer clicking the **center of a control's rect** obtained from `win_get_control_rects`.
- Use `keybd_hotkey("ctrl+s")` for key combinations. Use `keybd_type` to enter text into the focused edit control.
- After a click that changes state (opens a dialog, switches tabs), wait briefly and re-capture before the next action.

### Coordinates
- The process is per-monitor DPI aware, so control rects and cursor coordinates share the same coordinate space. Do not apply extra scaling.
- Multi-monitor setups: coordinates can be negative or beyond the primary monitor; the whole virtual screen is valid.

### Reliability notes
- `SendInput` (used by the mouse/keyboard tools) requires the target window to be foreground. It does not work on elevated windows.
- Synthetic `WM_*` messages can reach some standard controls in the background, but browsers, Electron apps, 1C, and custom-drawn UIs usually ignore them. Treat `win_send_message` as a fallback, not the default.

### Verification loop
After each action:
1. Re-capture the relevant window (image and/or control rects).
2. Confirm the expected change (text appeared, dialog opened, value updated).
3. If nothing changed, do not repeat the same action blindly — diagnose (wrong window? wrong coordinates? needs focus?).
4. On unexpected behaviour, stop and report a problem. Ask user for help. Don't dive into diagnosting loop.

