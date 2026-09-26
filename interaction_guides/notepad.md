# notepad — interaction guide

## Purpose / overview
- Classic/modern Windows Notepad (process `notepad.exe`, window class `Notepad`).
- Plain-text editor. Usually one window per file; multiple Notepad windows can be open at once.
- This machine's UI is **Russian-localized** — menu and status-bar labels are in Russian.

## Window & focus
- Find with `win_list_hwnd(process_filter='notepad')`. Several windows may exist; pick by title / `foreground:true`.
- Title bar: `Безымянный – Блокнот` (Untitled – Notepad). A leading `*` (e.g. `*Безымянный – Блокнот`) means **unsaved changes**.
- Standard focus; keyboard delivery is reliable. `win_ensure_foreground` before typing.
- Window buttons top-right: Свернуть (minimize), Развернуть (maximize), Закрыть (close).

## Main areas (coordinates approximate, window was at 358,198 size 1239x838)
- **Title bar** (~y 199-229).
- **Menu bar** (~y 229-248), five items left→right:
  - `Файл` (File) ~x 366-409
  - `Правка` (Edit) ~x 409-463
  - `Формат` (Format) ~x 463-520
  - `Вид` (View) ~x 520-554
  - `Справка` (Help) ~x 554-614
- **Text editor area**: UIA `Edit` control, name `Текстовый редактор`, automation_id `15`, ~rect 366,249 → 1589,1005. This is where text is typed / read.
- **Vertical scrollbar** on the right (`NonClientVerticalScrollBar`), disabled while the document is short.
- **Status bar** at bottom (`StatusBar`, automation_id `1025`), fields left→right:
  - (empty Text — reserved)
  - `Стр N, стлб M` = line / column of the caret
  - `100%` = zoom level
  - `Windows (CRLF)` = line-ending mode
  - `UTF-8` = encoding

## Key controls & what they do
- Edit control (`c11`-style, automation_id `15`) → click to place caret, then type.
- Menu items (Файл/Правка/Формат/Вид/Справка) → standard Notepad menus (New/Open/Save, Undo/Cut/Copy/Paste, Word wrap & Font, Zoom, About).
- Status-bar fields are **informational**; hovering them shows **no** tooltip (verified). (In modern Notepad, double-clicking the zoom/% field or pressing shortcuts can open related dialogs — verify before relying on it.)

## Shortcuts
- `Ctrl+N` new, `Ctrl+O` open, `Ctrl+S` save, `Ctrl+Shift+S` save as
- `Ctrl+Z` undo, `Ctrl+X/C/V` cut/copy/paste, `Ctrl+A` select all, `Ctrl+F` find
- `Ctrl+Shift+S`? (save-as) ; `F5` insert time/date; `Ctrl++` / `Ctrl+-` zoom

## Reading app state
- Unsaved changes → `*` prefix in the title bar.
- Caret position / zoom / EOL / encoding → read the status-bar text fields (e.g. `  Стр 1, стлб 1`, ` 100%`, ` Windows (CRLF)`, ` UTF-8`), available via UI Automation Text elements.
- Document text → read the Edit control via `win_read_text` (TextPattern) rather than OCR.

## Gotchas
- Russian UI: menu labels are `Файл/Правка/Формат/Вид/Справка`, not File/Edit/… — match by position or the Russian names.
- Closing an unsaved window (`*` in title) triggers a "Save changes?" prompt — confirm with the user before closing.
- There can be **multiple** notepad.exe windows; always act on the intended HWND.
- Do not confuse with **Notepad++** (`notepad++.exe`, class `Notepad++`) — a different app.

## Last verified
- 2026-09-26
