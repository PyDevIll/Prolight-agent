# 1C:Enterprise — Управление торговлей, ред. 11 (thin client)

- Process: `1cv8c.exe` (1C:Enterprise thin client)
- Window class: `V8TopLevelFrameSDI`
- Verified: 2026-09-17, window maximized on a 1920x1080 screen
- Window title example: "Торговля ОБМЕН ИП Серебрякова Е.Э. / Управление торговлей, редакция 11"

## Find the platform version
**Main menu → «Справка» → «О программе…»**

## Top chrome is exposed via UI Automation — USE THIS FIRST
The title strip (y ≈ 0..25) and the section strip (y ≈ 35..124) ARE in the UIA tree, with exact
names, rects and an `enabled` flag. Use `win_find_controls` / `win_enum_controls` instead of vision
for anything there. (1C document FORMS' command bars are NOT exposed — those stay vision-only.)

- Button **«Главное меню»**: the ▼ at centre **(38, 13)**, rect (27,3)-(49,23).
- Button **«Меню функций»**: centre ≈ (61, 13).
- Button **«Избранное»**: centre ≈ (89, 13).
- **Section tabs** (~94 px wide, centre_y = 79):
  Рабочий стол (54) · CRM и маркетинг (148) · Продажи (242) · Закупки (336) ·
  Склад и доставка (430) · Казначейство (524) · Финансовый результат и контроллинг (640) ·
  НСИ и администрирование (770) · Расчёты с дизайнерами (878) · Помощь (972)
- **Document tabs**: centre_y = 138.

## Main-menu availability (VERIFIED via UIA)
**While the «Рабочий стол» section is active, the «Главное меню» (▼) button is DISABLED.**
- `enabled = true` normally.
- Click section «Рабочий стол» (54, 79) → `enabled = false`; clicking the ▼ then does nothing
  (no menu opens).
- Activating a document form (e.g. clicking an order tab) → `enabled = true` again.
Read the flag from UIA, never from a screenshot: vision described the same greyed button once as
"grey/disabled" and once as "dark/enabled".

## Main menu contents
- ▼ opens a short vertical dropdown: **Файл, Правка, Вид, Сервис, Окна, Справка,
  Функции для технического специалиста…**
- Row pitch ≈ 16.5 px, so a click one row too low lands OUTSIDE the menu.
- «Справка» opens its submenu to the right (x ≈ 150–620), ending with **«О программе…»**.

## Creating a shipment (Реализация) from a customer order
- Primary: the hyperlink **«Оформить Реализацию»** in the bottom panel of the «Заказ клиента»
  form — creates «Реализация товаров и услуг» for that order. Sits next to «Оформить Акт»,
  «ПКО № …», the «Отгружать одной датой» checkbox and the shipment date.
- Generic route: **«Все действия»** (right end of the form command bar) → **«Создать на основании»**
  → «Реализация товаров и услуг».

## Gotchas
- **Section icons show no hover highlight** — hovering cannot confirm a target.
- **1C hyperlinks do not change appearance on hover** either («Оформить Реализацию», «Не начат», …)
  — confirm a link by the resulting dialog, not by hovering.
- Menus are short; always confirm the highlighted row / the resulting dialog before the next click.
- With the main menu open and «Справка» highlighted, pressing **→ closed the menu** (it did not
  enter the submenu). Use the mouse to enter submenus.
- If the desktop or another window shows up in a capture, it is usually **real** (show-desktop /
  Aero Peek / minimized windows), not a capture bug. Verify with `win_get_screenshot`.

