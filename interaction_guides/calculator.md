# Windows Calculator (CalculatorApp.exe / UWP)

## Purpose
Standard Windows 10/11 Calculator. Used to compute arithmetic by clicking keypad buttons.

## Window & focus
- Top-level HWND is the `ApplicationFrameWindow` owned by **ApplicationFrameHost.exe**
  (its HWND/title look tempting, but its UIA tree is nearly empty and `win_read_text` returns nothing useful).
- The **real** UI is a child window `Windows.UI.Core.CoreWindow`, process **CalculatorApp.exe**.
  - Find it with `win_list_hwnd` (filter `calc`) → pick the `ApplicationFrameHost.exe` entry, then locate the
    `CoreWindow` child (or read it from a `win_snapshot` of the frame's `focus_hwnd`).
  - Always **act on the CoreWindow hwnd**, not the frame.
- Focus with `win_ensure_foreground(hwnd=<CoreWindow>)`; keyboard delivery verifies fine.

## Main areas (UIA groups with automation ids)
- `CalculatorResults` (Text, name "Display is N") — the big result display. **Read the result from here.**
- `CalculatorExpression` (Text, name "Expression is ...") — the small expression line above the result.
- `MemoryPanel` (MC MR M+ M- MS M˅), `DisplayControls` (% CE C ⌫),
  `StandardFunctions` (1/x x² √x), `StandardOperators` (÷ × − + =), `NumberPad` (7-8-9 / 4-5-6 / 1-2-3 / +- 0 .),
  `TogglePaneButton` (hamburger), `NormalAlwaysOnTopButton` (pin), `HistoryButton`.

## Key controls (click by automation id — robust to window position)
- Digits: `num0Button` … `num9Button`
- Operators: `plusButton`, `minusButton`, `multiplyButton`, `divideButton`
- Equals: **`equalButton`** (note: singular; `equalsButton` does NOT match)
- Others: `negateButton`, `decimalSeparatorButton`, `clearButton`, `clearEntryButton`, `backspaceButton`,
  `percentButton`, `reciprocalButton`, `squareButton`, `squareRootButton`
- Prefer `win_click_control(hwnd=<CoreWindow>, automation_id=...)` — it re-queries live UIA coordinates and
  clicks the true centre, so it survives the window being moved. `name="Equals"` also works.

## Reading app state
- `win_read_text(hwnd=<CoreWindow>)` returns every label incl. `Expression is ...` and `Display is N`.
- For a quick check, `win_snapshot` shows control `CalculatorResults` whose name is `Display is <value>`.

## Shortcuts
- Standard: digits type directly, `+ - * /`, `Enter` = equals, `Esc` = clear, `Backspace` = delete.
  (Buttons are individual focus targets; keyboard shortcuts also work when the window has focus.)

## Gotchas
- The `ApplicationFrameHost.exe` top-level window exposes ~4 buttons only — don't rely on it; target the CoreWindow.
- Windows Calculator **moves/repaints**; UIA rects reported by an earlier snapshot go stale. Always click by
  automation id / name (fresh lookup) rather than cached screen pixels.
- OCR (`screen_find` text) often finds **no** text on this app — use UIA instead.
- `equalsButton` is wrong id → use `equalButton` or `name="Equals"`.

## Last verified
- 2026-09-26 — computed 2 + 2 = 4 by clicking Two, Plus, Two, Equals; display read "4".
