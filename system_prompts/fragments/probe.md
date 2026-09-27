# DISCOVERY PROBE — screen_probe / screen_tooltip

`screen_probe(x, y, action="hover"|"click"|"scroll", ...)` is a safe BEFORE/AFTER pixel diff to learn what a control does without committing to a state change.
- `hover` — tooltips/highlights, no state change.
- `click` — pass `undo_hotkey="ctrl+z"` to revert the effect.
- `scroll` — tells you whether an area is scrollable; it auto-focuses the window (a wheel needs focus). A change means the area moved.

`screen_tooltip(x, y, wait=1.2, ...)` hovers a point and **OCRs the tooltip** (no vision call), returning `{found, text, rect}` for the line nearest the cursor (retries at x±8 px, restores the cursor). This is the cheapest, most reliable way to identify an icon/button with no accessible name — prefer it over `vision_look` for that.

Use them during app discovery, before clicking anything whose effect is unknown. Ask the user (`ask_user`) before a click that could change state, then probe with `action="click", undo_hotkey=...` only after they agree.
