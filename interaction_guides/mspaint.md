# MS Paint (mspaint.exe) — interaction guide

## Purpose / overview
- Simple raster drawing editor. Windows 11 Paint; window class `MSPaintApp`, process
  `mspaint.exe`.
- The ribbon is custom-drawn: its buttons and colour palette expose almost no UIA. Locate
  tools by approximate coordinates and confirm the active tool via the canvas pane name.

## Window & focus
- Find with `win_list_hwnd(process_filter="paint")`. Title = `"<name> - Paint"`.
- Usually maximized; rect starts at (-8,-8).
- **Keyboard focus gotcha:** `win_focus` may report foreground, yet keyboard `SendInput`
  does NOT reach Paint. If shortcuts (Ctrl+Z, Ctrl+V, ...) do nothing, click the title bar
  once with the real mouse — then the keyboard works. Mouse clicks work regardless.

## Main areas
- **Ribbon** (top): tabs (Файл / Главная / Вид), then groups — Инструменты (tools), Фигуры,
  Кисти, Цвета (palette).
- **Canvas** (centre): the drawing. At 100% zoom it maps 1:1 to image pixels.
- **Status bar** (bottom): first field shows the cursor position in image pixels; the right
  side has zoom controls ("Мельче" / "Крупнее").

## UI Automation
- `uia.coverage` is poor: only the canvas pane and the status bar are exposed (plus the zoom
  buttons).
- **Active-tool readout (reliable):** the canvas pane's accessible name is
  `Использование инструмента <TOOL> на Холст` — e.g. "...Кисть на Холст" (Brush),
  "...Заливка цветом на Холст" (Fill). `win_snapshot` includes this named pane.

## Key controls & what they do (approximate, maximized)
- Инструменты group: Карандаш, **Заливка (Fill) ~ (374,72)**, Текст; bottom row Ластик,
  Пипетка, Масштаб (~y 103). **Кисть (Brush) split button ~ (460,70)**; the dropdown arrow
  ~ (470,70) opens the brush gallery.
- Цвета palette: swatches ~ x 893..1075, top row y ~ 64-78, bottom row y ~ 82-96.
  Bright red (#ED1C24) ~ (958,70); white (row2 col1) ~ (902,90).
- "Цвет 1" ~ (828,70); "Цвет 2" ~ (855,70).
- Zoom: "Мельче" ~ (1786,1038), "Крупнее" ~ (1908,1038).

## Shortcuts
- `Ctrl+Z` undo, `Ctrl+Y` redo, `Ctrl+S` save, `Ctrl+Shift+S` save as, `Ctrl+A` select all,
  `Ctrl+V` paste. (Need real keyboard focus — see the gotcha.)

## Reading app state
- Active tool: the canvas pane name (see above). Cursor position: the status bar's first field.
- Colour verification: reading the "Цвет 1" swatch is unreliable — draw a test dot and check
  its colour with `screen_find(kind="color")` instead.

## Gotchas
- The ribbon exposes no clickable controls; locate by coordinates and verify the tool.
- The keyboard may be silently dead until the window is raised with a real click.
- A single brush click leaves a ~7-9 px dot — mind thin shapes.
- With the Fill tool, clicking a background gap floods the whole canvas.

## Last verified
- 2026-09-20
