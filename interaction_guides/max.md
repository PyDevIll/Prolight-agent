# MAX web messenger (web.max.ru) — interaction guide

## Purpose / overview
- A web messenger opened in Chrome. Send and read chats with contacts and groups.
- Runs inside Chrome, so web content has no UIA — locate everything by OCR/screenshots.
- Load this guide with an explicit name: `load_interaction_guide(name="max")`.

## Window & focus
- Process is `chrome.exe`; typically maximized (rect starts at (-8,-8)).
- `win_ensure_foreground(<chrome hwnd>)` before typing.

## Main areas
- **Left column**: search ("Найти") at the top, story circles, then the **chat list**
  (each row is one chat).
- **Right side**: the open conversation — **header** (contact name + last seen) at the top,
  the **message thread** in the middle, the **composer** at the bottom.
- **Composer**: a rounded pill with placeholder "Сообщение"; paperclip (attach) on the left;
  emoji/sticker and mic (voice) on the right. When it is non-empty the mic is replaced by a
  blue circular **SEND** (up-arrow) button.

## Key controls & what they do (approximate, maximized 1936x1066)
- Chat list rows: click to open (the first row under the search is ~ (250, 342)).
- Composer centre ~ (1200, 1015); the SEND button appears at its right once text is present.

## Shortcuts
- `Enter` sends the message; `Shift+Enter` inserts a newline.
- `Esc` dismisses overlays/menus.

## Reading app state
- The **header** shows the contact name and last-seen — read it to verify the open chat.
- After sending, the composer clears (placeholder "Сообщение" returns) and the message
  appears **right-aligned** with a timestamp.

## Gotchas
- **Sending is irreversible** — confirm the wording with the user before sending to a real
  person.
- Verify the active chat (read the header) before typing.
- Coordinates are machine-specific; window rect starts at (-8,-8) here.

## Last verified
- 2026-09-20
