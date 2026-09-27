# META / SELF-MANAGEMENT

- `ping` — health check.
- `reload_tools` — hot-reload the builtin tool modules after editing them.
- `ask_user(question, options=[...], timeout=...)` — ask the user on the console and wait for their answer. Use it before any uncertain or state-changing step. Blocks until they reply. The answer is **free text** and may be an instruction, not only one of the options.
- `list_tools(group=None)` — list the available tools (`name, group, purpose, params`); call it when unsure what is callable. Tools are callable even when not in the injected schema.
- `enable_tools(groups=[...])` — unlock additional tool groups for the rest of this run. Valid groups are returned by `list_tools` / in this call's `valid_groups` (e.g. `perception`, `uia`, `vision`, `locate`, `probe`, `mouse`, `keybd`, `learning`, `workflow`, `overlay`, `fs`, `edit`, `apps`).
