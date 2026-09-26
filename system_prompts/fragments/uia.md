# UI AUTOMATION ACTIONS

- `win_click_control(id="c7")` or `win_click_control(hwnd=..., name="OK", control_type="Button")` — click a control by snapshot id or by criteria.
- `win_set_control_text(id="c3", text="...")` — set an Edit/Document value directly (no typing).
- Control discovery lives in `win_snapshot`; ids (`c*`) are stable across snapshots. Use ids rather than re-describing controls.
