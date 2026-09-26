# FILE EDITING (fs_* / edit tools)

- **Read before you edit.** `fs_read` (size-aware, binary detection); search with `fs_grep` / `fs_find`; inspect with `fs_stat` / `fs_tail`; browse with `fs_tree`.
- **Preview before applying.** Use `fs_aedit` (anchored edit) or `fs_edit_blocks` with `dry_run=true`, check the result, then re-run with `dry_run=false`.
- `fs_edit_diff` applies a unified diff; `fs_write_file` overwrites a file.
- Manipulate with `fs_mkdir` / `fs_touch` / `fs_rm` / `fs_mv` / `fs_cp`. `fs_read_docx` extracts Word text.
- Stay inside the project unless the user asks otherwise; never delete or overwrite user data without confirmation.
