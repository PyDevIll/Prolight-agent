# Telegram Web (web.telegram.org) — interaction guide

## Purpose / overview
- Web client of Telegram opened inside Chrome. Read/send messages, browse channels &
  groups, open channel-post **comments** and message threads.
- This build is **Web A**: URL `web.telegram.org/a/` (Web K would be `/k/`).
- Runs inside `chrome.exe` → the web content exposes **no UIA** (`uia.coverage` empty).
  Locate everything with OCR / screenshots; use `win_snapshot` text ids + `screen_find`.
- **ALWAYS read the omnibox (`Ctrl+L`) first.** `web.max.ru` is the **MAX** messenger
  (see `max.md`) — same process, same look. Do not confuse them (I did once).
- Load with an explicit name: `load_interaction_guide(name="telegram")`.

## Window & focus
- Process `chrome.exe`, class `Chrome_WidgetWin_1`, maximized.
- Screen rect ≈ `(-8,-8,1928,1058)`; snapshot image 1936×1066, scale 1 → **click coords =
  screen coords**.
- Title = `Telegram - Google Chrome`. `win_ensure_foreground(<chrome hwnd>)` before typing.

## Main areas (maximized, approximate — VERIFIED 2026-09-20)
- **Left column** (x ≈ 80–495):
  - **Top row:** rounded **search pill** (magnifier + "Search"), x ≈ 80–420, y ≈ 117–154.
    Story/avatar circles overlap its right end (x ≈ 407–490). **No hamburger at top-left.**
  - **Folder tab bar** (y ≈ 172–210): one tab **"Personal"** (with an unread badge); the
    strip can scroll horizontally (small caret at the far right).
  - **Chat list** (from y ≈ 215): rows = avatar + name + preview + time + unread badge;
    row pitch ≈ **72 px**; click a row at **x ≈ 250** to open. (star = pinned, ✓ = verified,
    speaker = muted.)
- **Right pane** (x ≈ 495–1920): **message-pane header** = name + "N subscribers" (channel)
  or "N members, N online" (group/gigagroup) + search icon and **⋮** menu on the right ·
  **message list** · **composer**.
  - **Composer** (bottom): avatar/attach on the left, rounded field with "Message"
    placeholder, then a smiley and a blue circular send/mic button. **Enter sends.**
  - A **muted** channel shows an "Unmute" bar along the bottom of the message pane.

## Key controls & what they do
- **Open a chat:** click its row in the left list.
- **Search (VERIFIED):** click the search pill → blue focus border + caret. Type a query:
  a filter chip row appears — **Chats, Channels, Apps, Posts, Media, Links** — with results
  under "Chats and Contacts" and a "Global search … Show More" link. `×` clears; **Esc**
  closes the search. (Click the middle of the pill, e.g. ≈ (285,135); clicking the magnifier
  end may not focus it. `Ctrl+K` did **NOT** focus search — do not use it.)
- **Read a channel post's comments (VERIFIED):** posts of channels linked to a discussion
  group show a footer control = speech-bubble icon + **"N comments"** (or **"Leave a
  comment"** when 0). Click it → the comments thread **replaces the chat pane**:
  header **"N Comments"** with a **back arrow** (top-left), a search icon and a **⋮** menu;
  the **parent post is pinned** at the top; comments below; composer at the bottom; a
  jump-to-bottom count badge. **Esc (or the back arrow) returns to the channel.**
- **Replies in groups:** in the sampled group ("Delphi & Lazarus") replies render as inline
  **quotes**, and no "N replies" thread bar was seen — treat group threads as UNVERIFIED.
- **Reach the newest message (VERIFIED):** opening a chat jumps to the **first unread**,
  not the end. Click the round floating button (down-arrow + unread count, e.g. "72") at the
  bottom-right of the message area (≈ **(1555, 950)** maximized) to jump to the **newest**.
- **Navigation gotcha:** only some channels have comments enabled — channels without a
  discussion group show NO footer bar (e.g. grетранслятор, Гифки Долбоёба, Пикабу).

- Bulk-read a thread (verified): click a message (or the message list) so the list has focus, then Ctrl+A selects all messages and Ctrl+C copies them to the clipboard as plain text (author + text + links). It captures only what is currently loaded in the DOM (a window around the viewport, ~40 messages) plus the pinned parent post and reaction counters — scroll to load more, then copy again. If the list is NOT focused, Ctrl+A/Ctrl+C copy nothing (empty clipboard).
## Shortcuts (VERIFIED)
- `Ctrl+L` — browser omnibox (use it to confirm this is Telegram, not MAX).
- `Esc` — closes the search AND the comments/thread view (returns to the chat). ✔
- `Ctrl+K` — NOT a search shortcut in this build. ✘
- `↑` / `↓` selection in the chat list, and other Web-A shortcuts: **not yet verified**.

## Reading app state
- Header right of the name tells what is open: channels "N subscribers", groups
  "N members, N online".
- A post's footer tells the discussion size: "N comments" / "Leave a comment".
- The floating badge over the message area = number of unread messages.

## Gotchas
- MAX (`web.max.ru`) looks almost identical — always read the URL.
- Many news/meme channels have comments DISABLED (no footer control).
- OCR garbles Cyrillic sometimes — cross-check with a tight screenshot.
- The window title changes with the open chat (e.g. `Delphi & Lazarus - Google Chrome`).

- Comment bodies can contain hyperlinks and forwarded channel posts. A stray click on a comment body can navigate the tab away (e.g. to cloud.mail.ru) or open another chat/profile (a long weird name like "Тауматафакатангиханга…" is a user in the linked discussion group). Do NOT click inside comment bodies: only scroll, and use the round floating down-arrow button (bottom-right, with an unread-count badge) to jump straight to the LAST comment. The comments thread keeps the parent post pinned at the top; comments below; composer at the bottom; Esc returns to the channel.
## Last verified
- 2026-09-20 (search focus, Esc close, comments open/close, scroll-to-newest, layout)
