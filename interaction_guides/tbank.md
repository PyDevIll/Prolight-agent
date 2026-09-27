# tbank — Т-Банк личный кабинет (tbank.ru)

## Purpose
- Т-Банк (ex-Тинькофф) personal-cabinet web app: **https://www.tbank.ru/mybank/** — a DOM
  page *inside Google Chrome* (process `chrome.exe`, class `Chrome_WidgetWin_1`).
- Logged-in home page ("Главная"): greeting, product/account cards, payments, cashback, operations chart.
- A login/SSO frame (`tbank.ru/api/common/v1/session/authorize`, `lk.gosuslugi.ru`) sits in the page
  when the session is not authorised.
- Knowledge so far = the **home page only**, read-only. No money operation has been executed or verified.

## Window & focus
- The window is the Chrome window; title = `<page title> | Т‑Банк - Google Chrome`.
  **Note the non-breaking hyphen** in `Т‑Банк` (U+2011) — a plain `-` will not match it.
- Always `win_ensure_foreground(hwnd)` before typing.
- **Auto-selection will not pick `tbank`.** The router keys on title tail → title head → tab host →
  process; the tail is `Google Chrome`, so the `chrome` profile wins. Drive it explicitly:
  `route_app_state(app="tbank")`, `resolve_app_control(name, app="tbank")`,
  `execute_app_control(name, app="tbank")`.

## Main areas (window maximised 1920×1080 at [-8,-8]; approximate)
- **Top bar, row 1** y≈117: `Бизнес` x≈596 · `Инвестиции` x≈672 · `Мобильная связь` x≈759 ·
  (`Страхование` x≈9xx — not confirmed) · `Путешествия` x≈975 · `Долями` x≈1054 ·
  profile name `Андрей` x≈1392.
- **Top main menu, row 2** y≈175: `Главная` x≈477 · `Операции` x≈571 · `Платежи` x≈645 ·
  `Бонусы` x≈719 · `Город` x≈793 · `Ещё` x≈842 · far right `Все продукты` x≈1348.
- **Left column** x≈456–750 (accounts/products):
  greeting `Добрый день` y≈280 · card **Black 3 673,52 ₽** y≈330–450 with the promo banner
  "Сделайте Black основной картой для соцвыплат" · card **Платинум 116,80 ₽** y≈610 ·
  **Самозанятость** y≈738 · **Потребительский кредит 0 ₽** y≈840 · `Показать скрытое` y≈909 ·
  yellow button **`Новый счет или продукт`** y≈981.
- **Main column** x≈860–1430:
  search field (placeholder `Поиск`) y≈351 · **four round quick-action tiles** y≈458–489 —
  `Перевести по телефону` x≈900 · `Перевести по реквизитам` x≈1065 · `Оплатить мобильный` x≈1210 ·
  `Распознать квитанцию` x≈1366 · **`Кэшбэк и бонусы`** block + link `Все предложения` y≈556–640
  (offer tiles, e.g. "Пятёрочка Доставка кэшбэк 52%") · **`Операции в сентябре`** y≈787 with tabs
  `Траты` / `Пополнения` and a donut chart (101 656 ₽) plus the category legend.
- **Footer** (below the fold): links О банке / Блог / Карьера / Помощь / Контакты / Карты, cookie
  notice, лицензия ЦБ РФ № 2673.

## Key controls & what they do (profile `tbank`, state `main`)
- `nav_*` — top product bar (row 1): switch to Бизнес / Инвестиции / Мобильная связь / Путешествия /
  Долями / profile.
- `menu_*` + `all_products` — main menu (row 2): Главная · Операции · Платежи · Бонусы · Город ·
  Ещё · Все продукты.
- `search` — page/product search field.
- `card_black`, `card_platinium`, `self_employment`, `credit` — open that account/product;
  `show_hidden` toggles hidden accounts; `new_product` starts opening a new account/product.
- `transfer_phone`, `transfer_requisites`, `pay_mobile`, `scan_receipt` — quick actions
  (**real money operations** — confirm with the user first).
- `cashback_block`, `all_offers`, `operations_september`, `tab_topups` — jump to the cashback /
  offers / operations blocks.
- Bars resolve by **coordinates**, content blocks by **OCR** (unique long Russian strings).

## Reading app state
- `win_read_text(hwnd)` is the reliable reader: Chromium accessibility is ON (`chromium_a11y:
  enabled`) and TextPattern returns the real page text (greeting, card names/amounts, operations
  legend) plus every link URL — even though the UIA **control** tree exposes only `Window` + `Pane`.
- `win_snapshot(hwnd)` OCR gives the layout with rects, but this page's font OCRs badly:
  `Главная`→"Глввнзя", `Операции`→"Опервции", `Платежи`→"П л втежи", `Платинум`→"Плэтинум",
  `Поиск`→"(Й Полск". Use the snapshot for geometry, `win_read_text` for truth.
- Router signals: state `main` ⇔ OCR has `Кэшбэк и бонусы` + one of `Новый счет или продукт` /
  `операции в сентябре` / `Все предложения`; `fallback` ⇔ only `tbank.ru` / `ТБанк` is visible
  (rendering, another section, or the login frame).

## Gotchas
- The greeting is time-dependent (`Добрый день` / `Доброе утро` / `Добрый вечер`) — never make it the
  only detect needle.
- Short OCR needles are dangerous on this page: `Все` also matches `Все предложения`, `Бонусы`
  matches `Кэшбэк и бонусы`. That is why the top bars use coordinates.
- Coordinates assume the window is maximised at 1920×1080 (window at [-8,-8]).
- Money-moving / product-opening controls (`Оплатить`, `Перевести`, `Новый счет или продукт`) do real
  things — ask the user before using them.
- After navigation the snapshot may predate the render → re-snapshot (`fresh=true`) before routing.

- **The row-2 main menu shifts ~24 px left on pages where the `Бонусы` badge is absent** (e.g. the
  offers catalog): `Операции` sits at x≈520-576, `Бонусы` at x≈667-707, `Город` at x≈730-761 there,
  versus x≈544-599 / x≈692-747 / x≈778-808 on the home page. That is why the nav controls are
  **hybrid** (`{"ocr": …, "click": [x,y]}`): the OCR needle wins when it matches, the coordinate is the
  fallback for the badge-less layout. The needles deliberately match this page's *mangled* OCR
  (`лввнз`=Главная, `Опер`=Операции, `тежи`=Платежи, `Бонус`, `дюз`=Город, `Еще`) so that both
  layouts resolve — do **not** "fix" them to the correct spelling.
- Row-2 navigation always prefers the OCR needle; verify with `resolve_app_control(name, app="tbank")`
  before an important click if the layout may have changed.
- The left-column promo banner **rotates** ("Сделайте Black основной картой…" / "За каждый рубль
  получите…" / рассрочка) — never use it as a detect needle.
- `Страхование` in the row-1 bar (x≈8xx) is reported by the vision model but was not confirmed by OCR —
  treat its position as unverified.
- Clicking `Выбрать` in the cashback block or a partner offer card **activates a real offer**, and
  `Перевести` / `Пополнить` / `Оплатить` / `Новый счет или продукт` are real money/product
  operations — always confirm with the user first.
## Last verified
- 2026-09-27 23:44 — discovery run on the logged-in cabinet (profile Андрей), home page + all main
  sections recorded: `main`, `operations` (/mybank/operations/), `payments` (/mybank/payments/),
  `bonuses` (/mybank/bonuses/), `offers_catalog` (/mybank/bonuses/categories/new/ from the main
  page's right-side `Все предложения`), `gorod` (/mybank/gorod/), `more_settings` (/mybank/settings/),
  `account_debit` (/mybank/accounts/debit/<id>/ from the left-column card), `fallback`.
  Profile `tbank` v5, 9 states, all named controls resolved against the live page; the row-2 menu
  controls were verified on both the badge and badge-less layouts. Nothing but read-only navigation
  was performed (no payment, no offer activation).

<!-- PROLIGHT:STATES -->
## Profile states (managed block — auto-generated, do not edit)

| State | Distinguishing signal | Named controls |
|---|---|---|
| `main` |  | nav_business, nav_investments, nav_mobile, nav_travel, nav_dolyame, profile, menu_home, menu_operations |
| `fallback` |  | menu_home, menu_operations, menu_payments |
| `operations` |  | search, filter_month, filter_accounts, filter_no_transfers, download_report, summary_spend, summary_income, first_row |
| `payments` |  | search, invoices, favorites, fav_mobile, fav_mts, fav_transfer_card, fav_add, transfer_phone_form |
| `bonuses` |  | search, subscription, choose_cashback_september, friends_black, friends_platinium, friends_tmobile, friends_business, partners_section |
| `gorod` |  | city, travel, afisha, orders, tbank_shop, dolyame_media, buy_auto, tracking |
| `more_settings` |  | ios_app, security, settings_tile, atm_and_topup, support_phones, statement, work |
| `account_debit` |  | back, transfer, top_up, account_operations, take_from_kubyshka, rounding_setup, add_card_for_relatives |
| `offers_catalog` |  | offer_1, offer_2, offer_3, category_tab_new |
<!-- /PROLIGHT:STATES -->


## Section map (states) and navigation
State ids in `tbank.profile.json` v5 (each with detect rules + named controls):

| state | URL / how reached | what it is |
|---|---|---|
| `main` | /mybank/ (Главная) | home: cards, quick actions, cashback, operations donut |
| `operations` | menu **Операции** → /mybank/operations/ | transaction list: search, filters (month · Счета и карты · Без переводов), `101 656 ₽ Траты` / `90 858 ₽ Доходы`, date-grouped rows, `Доступна рассрочка` banner |
| `payments` | menu **Платежи** → /mybank/payments/ | 'Платежи и переводы': Счета на оплату, Избранное (Мой телефон / МТС Mobile / Перевод на карту / Добавить), 'Перевод по телефону' + saved-recipient grid, Переводы tiles (Из другого банка · Между счетами · По номеру карты · По номеру договора · По реквизитам · SWIFT), Платежи tiles (Мобильная связь · ЖКХ · Госуслуги · Погашение кредитов) |
| `bonuses` | menu **Бонусы** → /mybank/bonuses/ | 'Кэшбэк и бонусы': 'Получайте больше выгоды' offer cards (`Выбрать`), 'Приглашайте друзей' (Black 500 ₽ / Платинум 5 000 ₽ / Т-Мобайл 500 ₽ / Счёт для бизнеса 7 000 ₽), 'Кэшбэк у партнеров' + category tabs |
| `offers_catalog` | main page right column → link **Все предложения** → /mybank/bonuses/categories/new/ | 3-column offer grid (Литрес, Страхование Petstory, Бананы в Городе, Церезит, Askona, Royal Canin, EDERRA, Свой отель, ROBUXLAND) with % badges and expiry |
| `gorod` | menu **Город** → /mybank/gorod/ | Т-Банк Город: city picker (Москва), tiles Путешествия · Афиша · Заказы · T-Bank Shop · Долями Медиа · Купить авто · Отслеживание посылок |
| `more_settings` | menu **Ещё** → /mybank/settings/ | 'Еще': курсы валют (USD/EUR/GBP), Банкоматы и точки пополнения, Связь с банком 8 (800) 555-22-77 / +7 (495) 645-59-19, Установить приложение на iPhone, Безопасность, настройки, заказать справку, Работа в Т-Банке |
| `account_debit` | main page LEFT column → click a card (Black / Платинум / Самозанятость / кредит) | account/card page: balance, Перевести / Пополнить, banners (Сбербанк, Кубышка, Округление покупок), 'операции по счету в <месяц>' + category donut, 'Бонусы по Black', right column 'Накоплено кэшбэка / 11 октября'; below the fold Реквизиты · QR-код · Ссылка · Детали счёта · Привязанные сервисы · Тариф · Выписка / Заказать справку · Защита карт |
| `fallback` | anything else on tbank.ru | rendering / unknown section / login-SSO frame — re-snapshot and use `menu_home` |

Navigation rules:
- The **top row-1 bar** (Бизнес · Инвестиции · Мобильная связь · Путешествия · Долями · profile) and the **row-2 main menu** (Главная · Операции · Платежи · Бонусы · Город · Ещё · Все продукты) are the same on every page; every state's controls resolve them (cross-state fallback works).
- `win_read_text` on any page prints the **URL as the first line** (e.g. `https://www.tbank.ru/mybank/operations/`) — the cheapest way to know where you are.
- Return to the home page with `menu_home`; the browser Back button (`back_button` in the chrome profile, or the page's own `< Назад` on an account page at x481 y230) also works.


## Вход в кабинет
- **Вход в кабинет** (записано 28.09.2026): новая вкладка → в омнибоксе набрать `https://www.tbank.ru/` →
  кнопка **«Личный кабинет»** в шапке сайта → форма входа (телефон/логин + пароль — вводит пользователь) →
  кабинет `https://www.tbank.ru/mybank/` (титул вкладки «Личный кабинет | Т‑Банк»).
- Сессия в Chrome-профиле «Андрей» долгоживущая: если она жива, «Личный кабинет» открывается сразу.
- Общий сценарий и остальные банки — workflow `вход_в_лк_банка`.
