# MAX web messenger (web.max.ru) — interaction guide

## Layout (maximised Chrome; window rect starts at -8,-8, size 1936x1066)
- LEFT column (~x 0..520): "Найти" search at the top, story circles, then the chat list.
- RIGHT side (~x 520..1930): the open conversation — header (contact name + last-seen) at
  the top, the message thread, and the composer at the bottom.
- Composer: a rounded pill at the BOTTOM of the conversation, placeholder "Сообщение".
  At this resolution the pill centre is ~ (1197,1015); it spans ~x 844..1549, y ~998..1033.
  Inside: paperclip (attach) on the left; sticker/emoji, then mic (voice), on the right.

## Opening a chat
- Click its row in the LEFT list. Example: "Денис Андреевич" was the 1st row under the
  search field, around screen (250, 342).
- VERIFY by reading the name at the top of the conversation.

## Writing / sending
- Click the composer (e.g. ~ (1200,1015)) and type. As soon as it is non-empty, the mic is
  replaced by a blue circular SEND (up-arrow) button on the right.
- **Enter sends** the message. (Shift+Enter = newline.)
- After sending, the composer clears (placeholder "Сообщение" returns) and the message
  appears RIGHT-aligned in the thread with a timestamp.

## Notes
- UIA exposes nothing (Chrome web content) — locate everything by screenshots/coordinates
  (ask for FRACTIONS, then verify by re-capture).
- Sending is IRREVERSIBLE — confirm the wording with the user before sending to a real person.
- The sidebar also lists "Пролайт", "ProLight основная", "Влада ProLight", "Доставки
  ProLight", "Госуслуги", etc.; the service also shows group chats.
