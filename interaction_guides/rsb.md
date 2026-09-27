# rsb — Банк Русский Стандарт, интернет-банк (online.rsb.ru)

## Purpose
- Интернет-банк **АО «Банк Русский Стандарт»** — https://online.rsb.ru/ , a DOM page *inside Google
  Chrome* (process `chrome.exe`). Window title: `Интернет-банк Русский Стандарт Онлайн - Google Chrome`
  (it does **not** change per page — the page identity comes from the URL in the omnibox).
- Pages seen: `/main` (главная), `/card/<id>` (карта), `/credit/<id>` (кредит), `/account/<id>`
  (счёт), plus `/history`, `/suggestions`, `/promotions`, `/statements`, `/payments/...`,
  `/create-product`, `/installments`, `/externalcard/<id>`, `/bonus/RS_CASHB`.
- Knowledge so far = reading the product pages (cards & credits) and the main page. **No payment,
  transfer or product action has been carried out.**

## Window & focus
- Always `win_ensure_foreground(hwnd)` before typing/clicking.
- The router does **not** auto-select this app (the title tail is `Google Chrome`, so the `chrome`
  profile wins). Drive it explicitly: `route_app_state(app="rsb")`,
  `resolve_app_control(name, app="rsb")`, `execute_app_control(name, app="rsb")`.
- Chromium accessibility is ON (`chromium accessibility enabled`), and `win_read_text(hwnd)` returns
  the page text — **including the current URL as a line**.

## Main areas (/main, window maximised 1920×1080 at [-8,-8]; approximate)
- **Header nav** y≈128: `Главная` x745 · `Платежи и переводы` x884 · `История` x1026 ·
  `Предложения банка` x1165 · `Акции` x1295 · `Документы` x1392.
- **Left column** x≈344-650 — the product list, present on **every** page (so you can hop from one
  product to another without going back):
  - `Карты` (y211) + `Оформить карту`: 4 tiles at y≈254-309 `МИР в кармане` (кредитная, •8566) ·
    y≈345-400 `Русский Стандарт` (кредитная, •6796) · y≈436-491 `130 дней без %` (кредитная, •9662) ·
    y≈527-582 `Банк в кармане Digital` (дебетовая, •5662). Tile = название + сумма + тип + маска.
  - `Кредиты` (y634) + `Оформить кредит`: tiles y≈677-732 `Кредит Классика` · y≈768-823
    `Надежный кредит` · y≈859-914 `Кредитная линия+` — debt + `N ₽ оплатить до DD.MM.YYYY`.
  - `Счета` (y964) + `Оформить счет`: `Текущий счет` y≈1009-1044.
- **Main column**: credit offer banner `Одобрено 990 000 ₽ / Выгодная ставка / Предложение
  ограничено` (x751-1175, y235-468); a transfer widget (`Переводы` · `Номер телефона получателя` ·
  `По реквизитам`); `Избранное` y548-567 (`все` link x1525) with `Автоплатежи` (y686) and
  `Моя квартира` (y810); `Счета на оплату` (x1299 y810); `Платежи` y897 with `Сотовая связь` x813,
  `Интернет и ТВ` x1030, `ЖКХ` x1247, `За рубеж` x1464 (y1026).
- **Footer**: курсы валют (USD 80.30/88.90, EUR 91.50/101.30 — «Курсы для переводов… между счетами в
  разных валютах»), `Круглосуточная поддержка 24 часа`, © 2017—2026 АО «Банк Русский Стандарт»,
  лицензия Банка России № 2289.

## Product pages
- **Card** `/card/<id>` (click a `Карты` tile): card visual + `Баланс на <date>`; `Показать номер карты
  и CVC` (y≈475), `Информация о карте` (collapsible, y≈641), `Условия и тарифы` (y≈686); for credit
  cards `Полная задолженность` / `Оставшаяся сумма минимального платежа` (+ `Оплатить` each) /
  `Дата оплаты минимального платежа`; middle column x≈990-1285: `Доступный остаток` y≈276 ·
  `Собственные средства` y≈326 · `Кредитные средства` y≈374 (credit only) · `Заблокированная сумма`
  y≈422 · `Кредитный лимит` + `Увеличить лимит` y≈471 (credit only) · `N Бонусов` y≈550 ·
  `Реквизиты счета` y≈651; right column x≈1328-1520: `Действия с картой` y223 · `Услуги` y281 ·
  `Страховые программы` y339 · `Расходные лимиты карты` y398 · `Рассрочка` y454 ·
  `Бонусная программа` y511; below: `Последние операции` + `Фильтр` + `Просмотр чеков из магазинов`.
- **Credit** `/credit/<id>` (click a `Кредиты` tile): back link `< <Название кредита>` (x713 y208);
  middle column x≈990-1285: `Осталось для полного погашения по графику` y≈276 ·
  `Осталось для оплаты по графику` (+ `до DD.MM.YYYY`) y≈370 · `Остаток основного долга` y≈437 ·
  `Остаток размещённых денежных средств` y≈505 · `Реквизиты счета` y≈627; main column:
  `Информация на <date>` y≈305, `Информация о кредите` collapsible (ставка · срок · номер договора ·
  дата заключения · сумма предоставленного кредита, values at x≈1180-1280), `Подробная информация`,
  `График платежей` (rows: дата · Оплачен/Предстоящий · Сумма · Статус, headers y≈988) +
  `Смотреть весь график`.
- The left product list stays visible on product pages, so `card_*` / `credit_*` controls work from
  any state (cross-state resolution).

## Key controls (profile `rsb`)
- `nav_home`, `nav_payments`, `nav_history`, `nav_suggestions`, `nav_promotions`, `nav_documents`.
- `card_mir_v_karmane`, `card_russky_standart`, `card_130_dney`, `card_bank_v_karmane_digital`,
  `credit_klassika`, `credit_nadezhny`, `credit_linia_plus`, `account_current`,
  `new_card`, `new_credit`, `new_account`.
- `show_card_number`, `card_info`, `card_terms`, `card_actions`, `card_services`, `card_insurance`,
  `card_spend_limits`, `card_bonus_programme`, `card_requisites`, `increase_limit`, `pay_debt`.
- `credit_info`, `credit_actions`, `credit_schedule`, `credit_full_schedule`, `credit_details`,
  `credit_requisites`, `back`.
- Main-page shortcuts: `offer_block`, `favorites_all`, `favorite_autopayments`, `favorite_flat`,
  `pay_invoices`, `pay_mobile`, `pay_internet_tv`, `pay_jkh`, `pay_abroad`.
- Product tiles use a **hybrid** match: the card **mask** (`8566` / `6796` / `9662` / `5662`) or the
  product name as an OCR needle, with the tile coordinate as fallback — masks are unique and short,
  so they survive this page's lossy OCR.

## Reading app state
- `win_read_text(hwnd)` gives the page text and the URL, but **money values are NOT in the
  accessibility text** — read them with OCR: `win_snapshot(hwnd, include=["text"], max_text=…,
  region=[left,top,right,bottom])`. Good regions: middle column `[975,250,1310,590]`,
  main+middle `[700,195,1310,1010]`, product header `[700,195,1310,1010]`.
- State router needles: `main` ⇔ `оплатить до` + one of `Оформить карту/счет/Избранное`;
  `card_detail` ⇔ `Баланс на` + `Доступный остаток/номер карты`; `credit_detail` ⇔ `Информация о
  кредите` + `Остаток основного/График платежей`; `fallback` ⇔ anything else on online.rsb.ru.

## Gotchas
- The **page title never changes** — use the URL from `win_read_text` to know where you are.
- OCR of this app mangles the left column badly (`МИР д кармане`, `Надежный`, `з 570,00 е`,
  `“5662`) — prefer the mask digits (4 digits) or coordinates, and treat `е`/`Р` as ₽.
- A blank OCR region (`text: []`) usually means the SPA was still rendering — retry after a moment.
- `Оплатить` / `Перевести` / `Оформить …` are **real money/product operations** — ask the user first.
- Coordinates assume the window is maximised at 1920×1080.

## Last verified
- 2026-09-27 23:48 — logged-in session. Watched (read-only) all four cards
  (/card/60874987 МИР в кармане, /card/58425224 Русский Стандарт, /card/60358563 130 дней без %,
  /card/60417174 Банк в кармане Digital) and all three credits (/credit/263959035 Кредит Классика,
  /credit/261241935 Надежный кредит, /credit/259442614 Кредитная линия+), plus the main page.
  Profile `rsb` v1 (states `main`, `card_detail`, `credit_detail`, `fallback`) written and
  self-tested against the live page; `main` matched on /main.

<!-- PROLIGHT:STATES -->
## Profile states (managed block — auto-generated, do not edit)

| State | Distinguishing signal | Named controls |
|---|---|---|
| `main` |  | nav_home, nav_payments, nav_history, nav_suggestions, nav_promotions, nav_documents, card_mir_v_karmane, card_russky_standart |
| `card_detail` |  | show_card_number, card_info, card_terms, card_actions, card_services, card_insurance, card_spend_limits, card_installments |
| `credit_detail` |  | credit_info, credit_actions, credit_schedule, credit_full_schedule, credit_details, credit_requisites, back |
| `fallback` |  | nav_home |
<!-- /PROLIGHT:STATES -->


## Вход в кабинет
- **Вход в кабинет** (записано 28.09.2026): новая вкладка → `https://online.rsb.ru/` → Chrome выдаёт
  **«Обнаружена проблема при проверке сертификата»** → «Дополнительно» → **«Перейти на сайт
  (небезопасно)»** → страница «Интернет-банк Русский Стандарт Онлайн» → логин/пароль (вводит пользователь).
- Предупреждение о сертификате появляется при каждом новом входе — это нормальный шаг, не ошибка агента.
- Сессия в Chrome-профиле «Андрей» долгоживущая. Общий сценарий — workflow `вход_в_лк_банка`.
