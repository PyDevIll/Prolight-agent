# Microsoft Outlook (Russian UI) — interaction guide

Purpose: read / compose / sort mail for the accounts configured on this PC.
Accounts seen: `110@pro-light.pro`, `106@pro-light.pro`, `Архивы`.
Last verified: 2026-09-21 (Outlook maximized, 1920x1080).

## Window & focus
- Process: `OUTLOOK.EXE`. Main window class: `rctrl_renwnd32`.
- Title pattern: `<folder> - <account> - Outlook`, e.g. `Входящие - 110@pro-light.pro - Outlook`.
- Normal rect ≈ `(-8,-8) - (1928,1048)` (maximized, 1920x1080).
- Focus with `win_focus(hwnd)`; typing needs the main window focused.

## Perception — IMPORTANT
- **The UIA tree of the main window is EMPTY** (`uia.coverage = empty`), because the UI is
  custom-drawn. Do NOT rely on `win_find_controls` / `win_click_control` here.
- Use OCR instead: `win_snapshot(hwnd, include=["text"])` returns every visible label with a
  screen `rect` — click the rect centre. Also `screen_find(kind="text", text="…", hwnd=…)`.
- Use `vision_look(rect=…)` on a single area when you need to read layout, and
  `win_changes()` to confirm what an action changed.
- Coordinates below are SCREEN pixels and are approximate (window moves/resizes).

## Layout (1920x1080, maximized)
- Quick-access strip y ≈ 13 (folder title, account, "Отправка и получение").
- **Ribbon tab row** y ≈ 44..57: `Файл` (15..43), `Главная` (74..116), `Отправка и получение`
  (145..269), `Папка` (297..330), `Вид` (358..377), `Справка` (404..450).
  To the right: the "**Что вы хотите сделать?**" command/search box (x ≈ 507..633).
- Ribbon body: y ≈ 70..155.
- **Folder navigation pane**: x ≈ 0..300, y ≈ 160..1040.
- **Message list**: x ≈ 265..760.
- **Reading pane**: x ≈ 760..1920. Empty state text: "Выберите сообщение, которое хотите прочитать".
- Status bar at the bottom (shows "Подключено", zoom "10%").

## Ribbon — «Главная» tab (verified)
- Группа **Создать**: `Создать сообщение` (~x55), `Создать элемент` (~x130).
- **Удалить**: `Пропустить`, `Очистить`, `Нежелательные`, `Удалить` (~265), `Архивировать` (~330).
- **Ответить**: `Ответить` (~400), `Ответить всем` (~465), `Переслать` (~530), `Собрание` (~600),
  `Больше`.
- **Быстрые действия**: `Реестры отпускных…`, `Сообщение группы…`, `Создать новое`,
  `Руководителю`, `Ответить и удалить`.
- **Переместить**: `Переместить` (~880), `Правила` (~955).
- **Теги**: `Прочитано/непрочитано`, `К исполнению`.
- **Найти**: `Поиск людей`, `Адресная книга`, `Фильтр почты`. **Речь**: `Прочесть вслух`.
- **Отправка и получение**: `Отправить и получить почту — все папки` (~1400).

## Ribbon — «Вид» tab (verified)
`Изменить представление`, `Показывать как беседы`, `Параметры беседы`, `Сообщения`, `Дата`,
`Обратный порядок сортировки`, `Добавить столбцы`, `Развернуть или свернуть`, `Уменьшить`,
`Область папок`, `Макет` (Область папок / Список дел), `Окно напоминаний`.

## Folder pane (verified 2026-09-21)
- **110@pro-light.pro**: Входящие (selected, 1 unread) · Документы приходов · Запросы от Тани ·
  Реестры отпускных доков · Рекламации · **Черновики [6]** · Отправленные · Рекламации (Отпр) ·
  Корзина 2 · RSS-каналы · Исходящие · Спам · Папки поиска.
- **106@pro-light.pro**: Входящие · Отправленные · Удаленные · Исходящие · Папки поиска.
- **Архивы**: Входящие · Отправленные · Удаленные · Исходящие · Папки поиска.

## Message list (Inbox)
- Filter row on top: search box "Искать в папке …", scope dropdown ("из текущего почтового ящика"),
  view tabs **Все** / **Непрочитанные**, sort **По дате**.
- No column headers — rows are grouped by date separators ("На прошлой неделе",
  "Две недели назад", "Три недели назад", "В прошлом месяце").
- **Unread** = blue vertical bar on the left edge + bold sender/subject.
- Attachment = paperclip icon at the right of the row.

## «Файл» backstage (verified)
Left column: `Сведения`, `Открыть и экспортировать`, `Сохранить как`, `Сохранить вложения`,
`Печать`, `Учетная запись Office`, `Параметры`, `Выход`.
Сведения shows: "Сведения об учетной записи", `Добавить учетную запись`,
`Настройка учетных записей`, `Управление правилами и оповещениями`,
`Управление надстройками COM`, `Параметры почтового ящика`. Close with Esc.

## Shortcuts
Standard Outlook (not yet verified in this install — check before relying):
Ctrl+N new message · Ctrl+R reply · Ctrl+Shift+R reply all · Ctrl+F forward · F9 send/receive ·
Esc closes backstage/dialogs · Del deletes (goes to Корзина).

## Gotchas
- No UIA → click OCR text rects, and **verify with `win_changes()`** after every action.
- **Opening a message (click in list / Reading Pane) generally marks it READ.** Ask the user
  before clicking messages; prefer already-read or clearly unimportant ones.
- A modal **"Ход отправки и получения сообщений Outlook"** progress dialog can appear and blocks
  input — wait for it to close (or Esc) before clicking elsewhere.
- **Never** use send / reply-all / delete / "Очистить" / "Нежелательные" without explicit user
  confirmation. Deleting mail is irreversible from the agent's point of view.

## Compose window (message)
- New message: Ctrl+N opens a separate top-level window "Без имени - Сообщение (HTML)" (class rctrl_renwnd32). Header fields: Кому (To) ~y190, Копия (Cc) ~y216, Тема (Subject) ~y250; body starts ~y285. The **Send (Отправить) button with a paper-plane icon sits in a narrow LEFT column at approx (54,232)** — NOT in the ribbon. After a successful send the compose window closes automatically; verify in the account's "Отправленные" folder (top item under "Сегодня"). Interacting with header fields: click the field then paste (clipboard_set + ctrl+v); verify a field's exact content by clicking it, Ctrl+A, Ctrl+C, clipboard_get.

## Reading a message (Reading Pane) — body structure (verified 2026-09-21)
- Selecting a message in the list renders it in the **Reading Pane** (x ≈ 760..1920). Header row:
  **From** (sender address) · **To** ("Кому") · date/time (e.g. "пн 21.09.2026 16:00"); action bar
  top-right: `Ответить` / `Ответить всем` / `Переслать`.
- To read the ENTIRE body exactly (no OCR errors): click inside the body, `Ctrl+A`, `Ctrl+C`, then
  `clipboard_get`. This returns the reply text + signature + quoted original in one go.
- A message body is structured **top → bottom**:
  1. **Sender's new text** — the ACTUAL content of the letter; it sits ABOVE the signature.
  2. **Automatic signature** — inserted automatically by the sender's Outlook; reads like a polite
     postscript "from whom": `С Уважением, <Имя>.` / `<Должность>, <компания>.` / `Телефон: …` /
     `Почта: …` / `Адрес: …`. **This is auto-added and is NOT the message content.**
  3. **Quoted original message** — a block starting `From: … / Sent: … / To: … / Subject: …`
     followed by the quoted text.
- **Gotcha — what is the "meaning"?** The meaningful content of a reply is ONLY item 1 (the text
  above the signature). The signature (contacts / "from whom") is generated automatically. A reply
  may be extremely terse — e.g. literally `???` — so read the top text carefully; do not dismiss it
  as an image/emoji placeholder, and do NOT mistake the signature block for the answer.
- First text line can render as a small grey box (e.g. a bare `???`): that IS the sender's typed
  text, not a blocked image. Confirm by selecting the body (Ctrl+A/Ctrl+C) vs. `vision_look`.

## Analyzing a message properly (NOT just Ctrl+A/Ctrl+C)
Reading a mail means more than copying its text. Check, in order:
1. **Header block** (Reading Pane, right side): **From** (name + address), **To** ("Кому"), **Cc**,
   **Date** (e.g. `пт 18.09.2026 18:24`). The From address is the ground truth for the sender.
2. **Body text** (top = real content; signature below — see previous section).
3. **Attachments** — see next section (count + names + sizes).
4. **Links / inline items** — hyperlinks, "download pictures" bar (`Чтобы скачать рисунки…`), etc.
Use Ctrl+A / Ctrl+C only as a *helper* to get exact text; it does not reveal attachments or the header.

## Attachments — detect, count, download
- **Where:** attachments show as **chips** in the Reading-Pane header, in a row just under
  From/To/Date (approx y ≈ 300 for a full-screen window), each with name + size (e.g. "269 KB", "5 MB").
- **Counting gotcha:** only the chips that fit are drawn in the pane. To get the RELIABLE count, open
  the save-all dialog (below) — it lists **every** attachment. On 2026-09-21 the pane showed 2 chips but
  the message actually had **3** attachments.
- **Download all in one go (preferred):**
  1. Right-click a chip → the ribbon switches to **«Работа с вложениями → Вложения»**.
  2. In the ribbon click **«Сохранить все вложения»** (big "Сохранить" button, ~x255, y120).
  3. Dialog **«Сохранение всех вложений»** appears listing all files → press its **OK** (default,
     has the blue focus border; press `Enter`) — or «Закрыть» to cancel.
  4. A folder-picker dialog opens (Explorer-style, starts in **Документы**). Left tree:
     **Быстрый доступ** / Рабочий стол / **Загрузки** / Документы / Изображения / Microsoft Outlook /
     OneDrive / Этот компьютер (…/Видео/Документы/Изображения/Музыка). Breadcrumb + "Поиск: <folder>"
     confirms the current folder.
  5. Navigate to the target folder (e.g. **Загрузки = Downloads**), then press **OK/Enter** (button at the
     bottom bar, right of «Сервис», left of «Отмена»). All attachments are written there at once.
- **Single attachment:** right-click its chip → «Сохранить как…».
- **Verify:** after saving, list the folder (e.g. `fs_find`/`fs_stat`) and confirm names + sizes + fresh mtime.

## Message list misc
- `Ctrl+A` `Ctrl+C` in the message list copies it as a **TSV table** with columns `От / Тема / Получено / Размер` — handy to dump a folder/result list deterministically (no OCR).
- The **Reading Pane marks a message READ** on selection (already noted under Gotchas).
