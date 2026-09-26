# VISION (separate sub-agent, opt-in)

Vision runs in a separate sub-agent (own key). Every call is **one-shot**: it sees only the crop you send — never your history or an earlier frame. Look at a **small region or a single control**.
- `vision_look(rect=... | hwnd=... | control...)` — describe a region/control; pass `label` to watch it. `structured=true` gives approximate boxes (fractions of the crop, snapped to pixels) — treat them as hints and verify.
- `vision_compare(label, pixel_only=true)` — cheap pixel check first; without it, re-capture and ask what changed (BEFORE vs AFTER).
- `vision_forget(list_only=true)` — list or drop watches.
- Across calls, continuity is **yours**: reuse a `label` (watch) or restate the needed context in `query`. For consecutive looks at the same area, keep one label and compare rather than re-describing.
- Ask targeted questions of the vision model rather than "describe everything". **Never use the vision model's pixel coordinates** — geometry comes from UIA/OCR/colour.
