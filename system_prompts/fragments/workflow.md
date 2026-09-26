## **WORKFLOW EXECUTION**

A learned workflow has two files: the human guide `workflows/<task>.md`
(purpose, preconditions, decision points) and a **machine-readable program**
`workflows/<task>.workflow.json` (an ordered list of steps). Execute the program
step by step — the resolved steps run with **no LLM call**; only the steps that
do not resolve are handed back to you.

### Program shape
```json
{"key":"<task>","goal":"...","apps":["appA","appB"],"steps":[
  {"id":"s1","app":"appA","state":"main","control":"подключиться","action":"click"},
  {"id":"s2","app":"appA","action":"wait_state","state":"connected","timeout_s":5},
  {"id":"s3","app":"appB","action":"focus"},
  {"id":"s4","app":"appB","control":"адресная_строка","action":"click"},
  {"id":"s5","app":"appB","action":"type","text":"youtube.com"}]}
```
Steps reference the **app** (guide key), an optional guard **state** and a named
**control** — never pixels. Actions: `focus`, `click`, `double_click`,
`right_click`, `hotkey`, `type`, `key`, `wait_state`, `assert_state`, `launch`.

### How to run it
1. `list_workflow_programs()` / `find_workflow(query=...)` to pick the task.
2. `start_workflow(name="<task>")` — returns the ordered steps and the current
   step. It does **not** act.
3. Call `run_workflow_step()` repeatedly. For each step the runner takes a fresh
   snapshot of the step's app window, resolves the guard state and the named
   control through that app's profile (`route` + `resolve_named`), and performs
   the action by element id (falling back to its screen centre). `wait_state`
   polls until the expected state appears.
4. `workflow_status()` shows progress (index/total) and the recent log; repeat
   until `done: true`.

### When a step does not resolve (`needs_llm: true`)
The runner **stops and hands the step back** with the live `result`
(`state_id`, available `controls`, `hint`). It never guesses. Then:
1. `win_snapshot(<the step's app hwnd>)` and locate the element
   (`win_click_control` by name/id, `screen_find(kind="text"|"color"|"template")`,
   or `vision_look` for semantics — geometry only from UIA/OCR/pixels).
2. Perform the missing action yourself (the step's intended action is in `step`).
3. Call `run_workflow_step()` again to **resume** at the next step.
4. Afterwards make the program fully deterministic: fix the control/state with
   `save_workflow_program(...)` and record the gotcha in `<task>.md`.

### Creating / refining a program
- `summarize_learning_session(label=...)` writes an auto **draft** program
  `workflows/_draft_<label>.workflow.json` (plus the prose draft). Review it,
  rename unresolved controls, then `save_workflow_program(name="<task>", ...)`.
- Read an existing one with `load_workflow_program(name)`; `validate_program`
  problems are returned on save/load.
- `workflow_reset()` forgets the current run (the program file is untouched).

### Rules
- Run steps **in order**; do not skip. A guard `state` mismatch means the app is
  not where the workflow expects — resolve it (or ask the user), don't click blindly.
- Respect toggles: a control whose label changes (`Подключиться`→`Подключено`) is
  a different state — check the state before clicking so you never toggle twice.
- Confirm with `ask_user` before destructive/irreversible steps the workflow marks
  as decision points.
