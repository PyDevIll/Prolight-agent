# chrome — interaction guide



## Purpose / overview

- Google Chrome browser (process `chrome.exe`, class `Chrome_WidgetWin_1`).

- Primary window = the browser window; page content lives below the toolbar.

- Chrome also spawns **separate popup windows** of the same class for menus,

  bubbles and dropdowns (flags: `tool_window` / `no_activate` / `topmost`,

  `owner` = the main window). They are NOT children of the tab content.



## Window & focus

- Find the main window with `win_list_hwnd(process_filter="chrome")` — title is

  `<page title> - Google Chrome`. One window per Chrome window (multiple profiles

  ⇒ multiple windows).

- Always `win_ensure_foreground(hwnd)` before typing; SendInput only reaches the

  foreground window.

- A minimized window can still be focused — `win_focus` restores it.



## Main areas (maximized 1920x1080; approximate, window may move/resize)

- **Tab strip** y≈0–40: tab-search button x≈20, tabs from x≈60, `+` (new tab)

  x≈530, window min/max/close x≈1804 / 1851 / 1896.

- **Toolbar** y≈44–86: back x≈22, forward x≈59, reload x≈95, omnibox pill

  x≈121–1744 (site-info x≈132, bookmark star x≈1721), extension buttons

  x≈1771 / 1807, profile avatar x≈1858, ⋮ main menu x≈1897.

- **Page viewport** y≈86–bottom: rendered web page (content **not** part of the

  browser chrome).

- Popups (main menu, extension bubble, bookmark bubble) are separate windows —

  re-run `win_list_hwnd(include_popups=true)` to get their hwnd.



## Key controls & what they do

- `+` / new tab button → opens a blank new tab (also Ctrl+T).

- Omnibox → type a URL/search then Enter; contents select on focus (Ctrl+L).

- Back / forward / reload → act on the **ACTIVE tab only**.

- Site-info button (left of omnibox) → connection/certificate info.

- Bookmark star → bookmark/unbookmark the current page.

- ⋮ main menu → new tab/window, history, downloads, bookmarks, print, settings,

  extensions, zoom, exit. Labels are Russian in this profile (see `main_menu`

  state in chrome.profile.json).



## Shortcuts (preferred over clicking)

- New tab `Ctrl+T`, close tab `Ctrl+W`, reopen closed tab `Ctrl+Shift+T`.

- New window `Ctrl+N`, incognito `Ctrl+Shift+N`.

- Focus omnibox `Ctrl+L`, find in page `Ctrl+F`, reload `Ctrl+R`/`F5`.

- History `Ctrl+H`, downloads `Ctrl+J`, bookmarks manager `Ctrl+Shift+O`.

- Clear browsing data `Ctrl+Shift+Del`, print `Ctrl+P`, DevTools `F12`.

- Next/prev tab `Ctrl+Tab` / `Ctrl+Shift+Tab`.



## Reading app state

- Window title = active tab title. Tab strip labels are visible via OCR snapshot.

- **Page content:** read with `win_read_text(hwnd)` when Chromium accessibility is

  on (`win_snapshot` reports `chromium accessibility enabled (N document)`).

  If it is off, relaunch Chrome with `--force-renderer-accessibility` (a running

  Chrome silently ignores the flag; close it first) — keep the default profile.

- `route_app_state()` matches the `browser` state; when the ⋮ menu is open it

  matches `main_menu` (OCR needles `Новая вкладка` / `Новое окно`).



## Gotchas

- Chrome popups are top-level windows with **no title**; they won't show up in a

  titled-only window list — use `include_popups=true`.

- The profile's browser-state controls are **coordinate-based** (no live UIA

  element), so they assume a maximized 1920x1080 window. Prefer keyboard

  shortcuts when the window is not maximized.

- A screenshot of the whole Chrome window includes the page; snapshot OCR only

  reliably picks up the browser chrome and the page title.

- Existing `--force-renderer-accessibility` only applies on a fresh launch.



- Web apps hosted in Chrome (e.g. web.max.ru): the router resolves the window to the `chrome` profile (title tail "Google Chrome" / process win), so a separate web-app profile (e.g. `max`) is NOT auto-selected — load it by explicit name (load_app_profile(name=...)). See interaction_guides/max.md.

## Last verified

- 2026-09-27 — new tab opened (Ctrl+T) and web.max.ru loaded (title "Веб-версия

  MAX"); accessibility enabled, `win_read_text` returned the page text.

## Web apps in Chrome (drive by explicit profile name)
- - **tbank.ru (Т-Банк личный кабинет)** → profile `tbank` (guide interaction_guides/tbank.md, states `main`/`fallback`, 28 named controls). Drive explicitly: `route_app_state(app="tbank")` / `execute_app_control(name, app="tbank")`. Window title `Личный кабинет | Т‑Банк - Google Chrome` (non-breaking hyphen in Т‑Банк). Chromium a11y ON but UIA exposes only Window+Pane → read the page with `win_read_text(hwnd)`; OCR of this page mangles Cyrillic, so the profile uses coordinates for the top bars and OCR needles for long unique strings. Verified 2026-09-27.
- - **online.rsb.ru (Банк Русский Стандарт — Интернет-банк)** → profile `rsb` (guide interaction_guides/rsb.md; states `main` / `card_detail` / `credit_detail` / `fallback`). Drive explicitly: `route_app_state(app="rsb")` / `execute_app_control(name, app="rsb")`. The window title never changes (`Интернет-банк Русский Стандарт Онлайн - Google Chrome`) — identify the page from the URL in `win_read_text`. Money values are NOT in the a11y text: read them with an OCR region snapshot. Left product column (Карты/Кредиты/Счета) is present on every page. Verified 2026-09-27.
- - **halvacard.ru/lk2 (Совкомбанк «Халва», личный кабинет)** → profile `halva` (guide interaction_guides/halva.md; states `card_balance` / `history` / `fallback`). Title is always «Личный кабинет», so drive by URL (win_read_text) and explicitly: `route_app_state(app="halva")` / `execute_app_control(name, app="halva")`. «История операций» = action button `история` on the card balance page → `/lk2/card/<id>/history/successful`; read the ops list with win_read_text (clean Cyrillic) — OCR mangles it into Latin. «Ближайший платеж» sits below the fold on the balance page. Verified 2026-09-27.
- - **web.alfabank.ru (Альфа-Банк, «Альфа-Онлайн»)** → profile `alfa` (guide interaction_guides/alfa.md; states `account` / `dashboard` / `fallback`). Drive explicitly: `route_app_state(app="alfa")` / `execute_app_control(name, app="alfa")`. Задолженность по кредитной карте — на странице счёта `/accounts/<номер>` (открывается из левой панели «Мои продукты» → строка «Счет кредитной карты»): «Задолженность»/«Общий долг», «Доступно» = остаток лимита. `win_read_text` на страницах счетов почти пуст — суммы читать OCR-ом. Левая панель одинакова на всех страницах, `Общий баланс` НЕ различает состояния (левую панель на дашборде и на счёте видно одинаково). Verified 2026-09-28.
- - **online.vtb.ru (ВТБ Онлайн)** → profile `vtb` (guide interaction_guides/vtb.md; states `home` / `history` / `products` / `fallback`). Drive explicitly: `route_app_state(app="vtb")` / `execute_app_control(name, app="vtb")`. Долг по кредитной карте — на главной `/home` в блоке «Счет кредитной карты • 6874» (строка «задолженность N ₽»; «доступно» = остаток лимита). История — `/history` (группировка по дням с итогом за день, кнопка «Фильтры»), грузится асинхронно: пустой OCR сразу после перехода — норма. Левое меню (Главная/Платежи/История/Справки/Продукты) есть на всех страницах. Verified 2026-09-28.
- - **online.sberbank.ru (СберБанк Онлайн)** → profile `sber` (guide interaction_guides/sber.md; states `main` / `wallet` / `card_detail` / `history` / `loans` / `fallback`). Drive explicitly: `route_app_state(app="sber")` / `execute_app_control(name, app="sber")`. Левое меню всего из 4 пунктов (Главный · Накопления для жизни · Платежи · Кредиты), на страницах карты и кошелька вместо меню «Назад». История — `/app/operations` (фильтры «Что показывать/Период/Карта или счёт/Сумма», группировка по дням с итогом за день). `/app/loans` — только витрина (активных кредитов нет). Гоча: на `/app/loans` OCR транслитерирует всю страницу латиницей — читать через `win_read_text`/vision. Verified 2026-09-28.
