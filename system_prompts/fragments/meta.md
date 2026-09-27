# META / SELF-MANAGEMENT

- `ping` — health check.
- `reload_tools` — hot-reload the builtin tool modules after editing them.
- `ask_user(question, options=[...], timeout=...)` — ask the user on the console and wait for their answer. Use it before any uncertain or state-changing step. Blocks until they reply. The answer is **free text** and may be an instruction, not only one of the options.
- `list_tools(group=None, query=None)` — list the available tools (`name, group, module, purpose, params`); call it when unsure what is callable. `group` also matches name prefixes (so `group="fs"` includes the `fs_*` edit tools, which live in the `edit` group); `query` searches names/descriptions. Tools are callable even when not in the injected schema.
- `enable_tools(groups=[...])` — unlock additional tool groups for the rest of this run. Valid groups are returned by `list_tools` / in this call's `valid_groups` (e.g. `perception`, `uia`, `vision`, `locate`, `probe`, `mouse`, `keybd`, `learning`, `workflow`, `overlay`, `fs`, `edit`, `apps`).
- Parameter aliases: `fs_read`/`fs_*` tools accept either `file` or `path`.
