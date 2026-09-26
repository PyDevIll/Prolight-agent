# DISCOVERY PROBE — screen_probe

`screen_probe(x, y, action="hover"|"click"|"scroll", ...)` is a safe BEFORE/AFTER pixel diff to learn what a control does without committing to a state change.
- `hover` — tooltips/highlights, no state change.
- `click` — pass `undo_hotkey="ctrl+z"` to revert the effect.
- `scroll` — tells you whether an area is scrollable; it auto-focuses the window (a wheel needs focus). A change means the area moved.

Use it during app discovery, before clicking anything whose effect is unknown. Ask the user (`ask_user`) before a click that could change state, then probe with `action="click", undo_hotkey=...` only after they agree.
