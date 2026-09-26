# ON-SCREEN HIGHLIGHT (deliberate, user-visible)

- `highlight_area(regions=[...], label=..., color=..., persist=..., duration=...)` — draw labelled box(es) the human can see (they are excluded from your own captures). Each region is a screen `[left, top, right, bottom]` or an object with `rect`/`hwnd`/`point`/`bbox`.
- `highlight_clear(token)` — remove boxes drawn earlier.

Use it to point at the exact area you mean, e.g. while asking the user to confirm a region.
