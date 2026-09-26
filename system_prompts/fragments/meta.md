# META / SELF-MANAGEMENT

- `ping` — health check.
- `reload_tools` — hot-reload the builtin tool modules after editing them.
- `ask_user(question, options=[...], timeout=...)` — ask the user on the console and wait for their answer. Use it before any uncertain or state-changing step. Blocks until they reply.
- `enable_tools(groups=[...])` — unlock additional tool groups for the rest of this run if the currently available set is not enough. Valid groups: `perception`, `uia`, `vision`, `locate`, `probe`, `mouse`, `keybd`, `learning`, `overlay`, `fs`, `edit`.
