# max — MAX web messenger (web.max.ru)

## Purpose
- MAX / МАХ web messenger at **https://web.max.ru** — runs as a web page *inside Google Chrome* (process `chrome.exe`).
- Because it is a DOM page, the UIA tree is nearly empty (only `Window` + `Pane`). Read the page with
  `win_read_text(hwnd)` (TextPattern works and returns the chat names / thread text); locate elements
  with OCR (`win_snapshot` text) or coordinates. Chromium accessibility is already ON for this window.

## Window & focus
- The "app window" is the Chrome window (`… - Google Chrome`). Always `win_ensure_foreground(hwnd)` before typing.
- One MAX tab per Chrome window; the tab title and the page URL change with what is open.

## Main areas (maximized 1920×1080; approximate)
- **Left icon rail** x≈0–70 (click the icon; the label sits ~20 px below the icon centre):
  `Новые` y≈196 · `Каналы` y≈262 · `Контакты` y≈353 · `Звонки` y≈419 · `Настройки` y≈994.
- **Chat-list column** x≈70–520: header `Чаты` y≈112, search field `Найти` y≈163, then chat rows
  (avatar + name + last-message preview + time). Click a row's **name** to open the conversation.
- **Main area** x≈540+: plain blue background while no chat is open; once a chat is opened the
  thread header (contact name + last-seen, e.g. `3 ч назад` / `онлайн`) appears at y≈102, with the
  scrolling message thread below.
- **Composer** (only when a chat is open) y≈985–1035: attach x≈870 · emoji x≈1430 · camera x≈1475 ·
  mic x≈1520 · placeholder `Сообщение` (faint grey, x≈895–981).

## Key controls & what they do
- Open a chat → click the chat row name (OCR locator). Known contacts in the list: **Назгуль**,
  **Денис Андреевич**, **Хорошая мысля** (channel), plus many others.
- Search → click `Найти` (left of the list).
- Send a message → click the composer, type the text; a round **blue Send button** appears at
  **[1522, 1010]** (only visible while the field is non-empty). **Enter also sends.**
- Message context menu (right-click a bubble) — menu box x≈1150–1370:
  `Скопировать текст` y≈852 · `Выбрать` y≈885 · `Удалить` (red) y≈923. `Esc` closes it.
  (For a message you sent, the menu shows only these three items.)

## How to drive it from the agent
- Profile key **`max`** (`interaction_guides/max.profile.json`, v9) — **5 states**:
  - `chat_list` — detect `ocr_all ["Чаты"]` + `ocr_any ["Найти","Новые","Звонки"]`; controls `search`,
    `nav_*` (rail coords), `chat_nazgul` / `chat_denis` / `chat_interesting` (OCR).
  - `chat_open` — detect `ocr_all ["Чаты","Найти"]` + `ocr_any ["назад","онлайн","был(а)","недавн","печатает","общение","Сообщение"]`;
    controls `message_input` (coords [930,1008]), `send_button` (coords [1522,1010]), `chat_search` (OCR),
    `message_input_ocr` (OCR `общение`, only resolved if the composer is inside the OCR scope).
  - `message_menu` — the right-click menu; detect `ocr_all ["Скопировать текст","Выбрать"]`; controls
    `mark_unread`, `copy_text`, `select_text`, `report`, `delete_message` (all OCR).
  - `delete_confirm` — the "Удалить сообщение?" dialog; detect `ocr_any ["Удалить сообщение","Удалить у всех","Не удалять"]`;
    controls `confirm_delete` (coords [868,630]), `cancel_delete` (coords [1052,630]), `delete_for_all_checkbox` (coords [877,551]).
  - `max_page` — low-priority fallback when only the URL / `Чаты` is visible (page body not parsed yet); no controls.
- **Precedence is by score** (`ocr_all` needle = 2, `ocr_any` = 1): `chat_open` (2+2+1 = 5) beats `chat_list` (2+1 = 3)
  when a chat is open; `max_page` (1) only wins when the others fail. That is why `chat_open`'s `ocr_all` keeps
  **both** `Чаты` and `Найти` — it must out-score `chat_list`.
- Always drive it **by explicit name**: `route_app_state(app="max")`, `resolve_app_control(name, app="max")`,
  `execute_app_control(name, app="max")`.

## Reading app state
- `win_read_text` is the reliable reader: it returns the open thread text, then the chat-list entries.
- `win_snapshot` OCR gives the layout (with rects) but: the list is capped by `max_text` and the
  faint composer placeholder is frequently missed — do **not** depend on OCRing `Сообщение`.
- Signals: chat is open ⇔ the main area has a thread header / message text (or the URL is
  `web.max.ru/<chatId>`); the tab title carries the unread count (e.g. `2 непрочитанных чата`).

## Gotchas
- **Auto-selection will not pick `max`.** The router keys on title tail → title head → tab host → process. A MAX tab
  title normally starts with the unread count (e.g. `2 непрочитанных чата - Google Chrome`), so title head ≠ `max`;
  it falls through to process `chrome.exe` and the `chrome` profile wins. Drive `max` **explicitly by name**
  (`app="max"`). Only a tab whose title literally starts with `MAX` would auto-select the profile.
- `trigger.url_contains:"web.max.ru"` is ignored when no runtime URL is available, and the OCR'd URL is garbled
  (`web„max.ru/4651910`) — never rely on it alone (hence the `also_matches` fallback).
- Coordinates assume the Chrome window is maximized at 1920×1080 (window at [-8,-8]).
- Accessibility toggles: `chromium_a11y` shows `enabled` right after load but flips to `unknown` / UIA `n/a` after
  using in-page menus or dialogs — switch to OCR / `screen_find` + `vision_look` at that point.
- The window/tab title carries the tab name and the left chat-list preview mirrors the last message — use them as a
  quick confirmation that a send/delete took effect.
- **Sending and deleting are irreversible** and visible to the contact — confirm with the user first.
- Emoji / camera / mic / attach open media flows; not yet explored.

## Last verified
- 2026-09-27 — opened the `Назгуль` chat, sent the text `тест` (blue Send button, appears bottom-right
  at 20:20 with a sent check-mark), right-clicked the bubble → context menu
  `Скопировать текст / Выбрать / Удалить`.


## Delete a message (verified flow)
- Right-click directly ON a message bubble's text → context menu appears (Chrome-internal, NOT a separate HWND, so it does not show in win_list_hwnd; find items with `screen_find(kind="text", ...)`).
- Menu items (top→bottom): `Отметить непрочитанным`, `Скопировать текст`, `Выбрать`, `Удалить` (red).
- Click `Удалить` → confirmation dialog **"Удалить сообщение?"** with a checkbox **"Удалить у всех?"** (checked by default) and two buttons: **`Удалить`** (left, ~[868,630]) and **`Не удалять`** (right). Click left button to confirm.
- Verified 2026-09-27: deleted the last message "тест" (20:20) in the Назгуль chat; the chat-list preview and the thread both updated and the text disappeared from the screen.

<!-- PROLIGHT:STATES -->
## Profile states (managed block — auto-generated, do not edit)

| State | Distinguishing signal | Named controls |
|---|---|---|
| `chat_list` |  | search, nav_new, nav_channels, nav_contacts, nav_calls, nav_settings, chat_nazgul, chat_denis |
| `chat_open` |  | message_input, message_input_ocr, send_button, chat_search |
| `message_menu` |  | mark_unread, copy_text, select_text, report, delete_message |
| `delete_confirm` |  | confirm_delete, cancel_delete, delete_for_all_checkbox |
| `max_page` |  |  |
<!-- /PROLIGHT:STATES -->


## Router & OCR scope (how detection works)
- `route_app_state` / `validate_profile` run on the **latest `win_snapshot`** (they do NOT re-capture). Right after
  navigation the snapshot may predate the page render → only browser-chrome OCR → no page needle matches. **Take a
  fresh `win_snapshot(hwnd)` (or `win_read_text(hwnd)`) once the page is loaded, then route.** This was the exact
  cause of the 2026-09-27 `matched:false` (`ocr_all 'Чаты' not found`).
- The router's OCR scope is **capped (~40 lines, top-down)**. It reliably sees the top of the page (tab bar, URL,
  `Чаты`, `Найти`, thread header, first messages) but **not** bottom-anchored UI: the message context menu
  (y≈680–930) and the delete-confirmation dialog (y≈490–640) fall outside it. So `message_menu` / `delete_confirm`
  are usually *rejected* (`reason: ocr_all … not found`). For those, locate items with `screen_find(kind="text", …)`
  and click the coordinates; the states stay valid as documentation/fragments if the caps are ever raised.
- Health check: if MAX is clearly on screen and `route_app_state(app="max")` returns `matched:false`, the snapshot
  was stale → re-snapshot and re-route (do not conclude the profile is broken).
