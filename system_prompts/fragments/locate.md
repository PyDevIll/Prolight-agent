# DETERMINISTIC LOCATORS — screen_find

Custom-drawn UIs (Paint, games, editors) expose an almost empty UIA tree, and the vision model's pixel coordinates are **unreliable**. Locate elements deterministically:
- `screen_find(kind="color", color=[r,g,b], hwnd=...)` — connected blobs of a colour (a mark, a swatch, a status LED).
- `screen_find(kind="ink", hwnd=...)` — thin monochrome line-art icons: thresholds luminance and **clusters strokes into button boxes** (`dark=true` for dark ink; `close`/`ink_threshold` to tune). Use this for a toolbar of glyph icons where colour-blob search fragments.
- `screen_find(kind="template", template_path=..., hwnd=...)` — find a small image.
- `screen_find(kind="text", text="Save", hwnd=...)` — OCR the window and find a label/button by its text.
- `screen_tooltip(x, y)` — the cheapest ground truth for an icon with no text: hover and OCR the tooltip.

All return `screen_center` ready for `mouse_click`. Coordinates always come from code/OS (snapshot geometry, UIA, OCR), never from the vision model. Vision boxes (`vision_look(structured=true)`) are **draft hints only** — mark them as "not for clicking" and confirm with the locators above.
