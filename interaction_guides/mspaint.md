# MS Paint (mspaint.exe) — interaction guide

Applies to: Windows 11 Paint (window class `MSPaintApp`, process `mspaint.exe`).

## Window / focus
- Find it with `win_list_hwnd(process_filter="paint")`. Title looks like "Безымянный - Paint".
- Usually maximized; the window rect starts at (-8, -8).
- **Keyboard focus gotcha:** `win_focus` (AttachThreadInput) reports `foreground: true`
  but keyboard `SendInput` may NOT reach Paint. If shortcuts (Ctrl+Z, Ctrl+V, ...) do
  nothing, click the TITLE BAR once with the mouse to really raise the window — then the
  keyboard works. Mouse clicks work even when the keyboard does not, so the failure is
  silent. (Real title-bar click at approx (960, 10).)

## UI Automation
- The ribbon exposes NO usable controls: `win_snapshot` reports `uia.coverage` poor and
  lists only panes + the status bar. Tool buttons and the colour palette cannot be clicked
  by name.
- **Active-tool readout (reliable):** the canvas pane's accessible name is
  `Использование инструмента <TOOL> на Холст` — e.g. "...Кисть на Холст" (Brush),
  "...Заливка цветом на Холст" (Fill). win_snapshot includes named non-actionable controls,
  so read the pane's name from the snapshot's `uia.controls` after clicking a tool.

## Ribbon coordinates (screen px, maximized on this machine)
- Tools group "Инструменты", top row: Карандаш ~ (354,72), **Заливка (Fill) ~ (374,72)**,
  Текст ~ (394,72). Bottom row: Ластик, Пипетка, Масштаб (~y 103).
- **Кисть (Brush) split button ~ (460,70)** — click the icon part; ~ (470,70) is the
  dropdown arrow (opens the brush gallery instead of selecting the tool).
- Colour palette "Цвета": swatches ~ x 893..1075, top row y ~ 64-78, bottom row y ~ 82-96.
  **Bright-red swatch (#ED1C24) ~ (958,70)**; white swatch (row2 col1) ~ (902,90).
- "Цвет 1" (Colour 1) swatch ~ (828,70); "Цвет 2" ~ (855,70).
- Status-bar zoom buttons: "Мельче" ~ (1786,1038), "Крупнее" ~ (1908,1038).

## Canvas <-> screen calibration
- The status bar's first field shows the cursor position over the image in image pixels
  (`"<x>, <y> пкс"`). Move the mouse and read it to derive the affine mapping.
- At 100% zoom the mapping is 1:1: **screen = image_px + (5, 144)**, i.e. the image
  origin (0,0) sits at screen (5, 144). Canvas image is 1152x648 at 100%.

## Locating things deterministically (PREFER over the vision model)
- The vision model is unreliable for pixel coordinates (inconsistent between calls;
  hallucinates crop sizes). Prefer:
  - `screen_find(kind="color", color=[0,0,0], rect=<canvas>, tolerance=80, min_area=40)`
    → exact bounding boxes of the black hand-drawn shapes (each hollow rectangle outline
    is a single connected blob).
  - `screen_find(kind="color", color=[237,28,36], tolerance=90, min_area=4)` → locate and
    VERIFY red brush marks.
- Verify a drawn mark with a colour search (deterministic), not by asking vision.

## Drawing
- Select Brush (Кисть); set Colour 1 by LEFT-clicking a palette swatch (right-click sets
  Colour 2). A single brush click leaves a ~7-9 px dot.
- To fill/verify, the Fill tool is at ~ (374,72); the Fill tool on an enclosed region
  paints only that region (clicking a background gap would flood the whole canvas).
- Undo = Ctrl+Z (only works after the window truly has keyboard focus — see above).
