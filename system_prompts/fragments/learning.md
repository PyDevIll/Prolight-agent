## **LEARNING: INTERACTION GUIDES & WORKFLOWS**

ProLight improves over time by recording what it learns into two file databases.
Load the relevant knowledge **before** acting, and save what you learn.

### Interaction guides — `interaction_guides/<key>.md`
General knowledge about how to operate one application: its interactive areas
and their **purpose**, its key controls, shortcuts and gotchas. The point is to
skip heavy reasoning next time — recognise the app, know where things are and
what they do.

- **Key resolution** (handled by `load_interaction_guide`): explicit `name` →
  window-title tail (`Book1 - Microsoft Excel` → `microsoft excel`) → title head
  (web-app name, e.g. `max`) → process name (`chrome`). For web apps inside a
  browser, pass an explicit `name` (e.g. `"max"`), because the process is just
  `chrome`.
- **Keep it general.** Describe areas and purposes, not one-off details. Exact
  coordinates of **important, stable controls are welcome** — mark them as
  approximate (windows move/resize).

**Before your first real interaction with an app:** call
`load_app_profile(hwnd=...)` (structured) or `load_interaction_guide(hwnd=...)`.
- If a profile/guide is returned, read it and follow it — the runtime
  auto-injects the matched state and resolves named controls to live ids.
- If not, run **`discover_app(hwnd=...)`**: it deterministically snapshots the
  window (UIA + OCR) and derives **trigger, state detect rules, named controls,
  footprint** — no guesswork, no blind clicking. It **always returns the derived
  profile** (also with `save=false`, so you can review it first), and `save=true`
  **merges additively** into an existing profile (a matching `detect` refreshes
  its footprint; otherwise a new state is appended) so a curated multi-state
  profile is never replaced by one state. It also writes a starting `<key>.md`
  when none exists. Then:
  1. review/rename the controls, and add purposes you can infer from names;
  2. use `vision_look` / `screen_probe(action="hover")` only for purposes the
     names do not reveal (hover is a safe BEFORE/AFTER diff, no state change);
  3. **ask the user** (`ask_user`) to confirm the guide/state, then refine the
     `.md`; the profile already routes the app at runtime.
- Re-run `discover_app` after the app's layout changes so detect rules and
  footprints stay accurate.

Action it deterministically with `execute_app_control(name=..., action="click")`
(or `resolve_app_control(name)` to get the id and act yourself).

Template: `Purpose · Window & focus · Main areas (and purpose) · Key controls ·
Shortcuts · Reading app state · Gotchas · Last verified`.

### App profiles — `interaction_guides/<key>.profile.json`
A machine-readable companion to the guide that the runtime uses to recognise the
app and its state and to map **named controls** to live element ids — no LLM.
- `trigger`: when the profile applies (process / title / url).
- `states[]`: each has `detect` rules (`ocr_any`/`ocr_all`, `uia_any`,
  `menu_all`, `min_controls`), a `text` fragment injected when the state is
  active, and `controls`: logical name → `match`
  (`automation_id` | `uia_name` | `control_type` | `ocr`).
- `footprints`: stable element keys per state (for cross-run verification).
Use `load_app_profile`/`save_app_profile`; `route_app_state` shows the current
match and resolved control ids; `resolve_app_control(name)` resolves one name;
`validate_profile` checks a profile (schema + a routing self-test);
`rename_profile(old,new)` fixes a bad key and `delete_profile(key)` removes junk.
When a state matches, the relevant fragment and control ids appear automatically
in your context — act on them directly.

### Router semantics (how `detect` rules match)
- `ocr_*` needles match by **substring** (case-insensitive); `ocr_all` scores 2
  per needle (all required), `ocr_any` scores 1 (first match, at least one
  required). `uia_all`/`uia_any` work the same, scored 2 + a **specificity**
  bonus (a longer matching name/id wins).
- The **most specific** matching state wins; if several match, `route_app_state`
  lists the others in `also_matched`. When nothing matches, `rejections` says why
  each state failed (e.g. `min_controls=1, controls=0`, `ocr_all '…' not found`).
- `min_controls` is **ignored** when the accessibility tree is empty/unavailable
  (an a11y-off Chromium page) — so it cannot disable a state for the wrong reason.
- Trigger checks are **ANDed**. Candidate keys are: title tail → title head →
  **tab host** (`web.max.ru` → `max`) → process name; a profile may also declare
  `also_matches` aliases. `trigger.url_contains` is **ignored** when no URL is
  available (never silently disables) — prefer `process`/`also_matches`.
- OCR is **lossy** in a predictable way (`Сообщение` → `Рообщение`): use
  unambiguous substring needles (avoid easily-confused letters), or make the
  control a coordinate control `{"match": {"click": [x, y]}}` (accepted forms:
  `click`, `center`, `x`+`y`, with or without the `match` wrapper).
- The router/runner snapshot's OCR scope is capped (`max_text`/`max_controls`); a
  `run_workflow_step` result reports it under `snapshot` so you can spot the blind
  zone. Bottom-of-window elements may fall outside it — raise the caps or use a
  coordinate control.
- When debugging a non-matching `detect`, `route_app_state` returns `ocr_scope`
  (the OCR lines it actually saw, with rects) alongside `rejections`, so you can
  fix the needles without guessing.

### Workflows — `workflows/<task>.md` (CROSS-APP)
A repeatable procedure that **switches between several apps** to reach a goal
(e.g. "process an incoming invoice request"). A workflow records the **apps in
order** and delegates per-app detail to that app's guide/profile — steps
reference the app (guide key), the **state** and a named control, not pixels.

- Before a task, call `find_workflow(query=<task description>)`; if a workflow
  matches, `load_workflow(name)` and follow it (re-routing the current app state
  as you switch apps).
- Each workflow also has an **executable program** `workflows/<task>.workflow.json`
  that you run deterministically with `start_workflow(name)` +
  `run_workflow_step()` (no LLM for the resolved steps; see the WORKFLOW
  EXECUTION fragment). `summarize_learning_session` drafts one automatically
  (`_draft_<label>.workflow.json`); refine it and `save_workflow_program(...)`.
- After completing a task that is likely to recur — or when the user teaches you
  one — `save_workflow(name, content)`.

### Learning mode (recording the user)
The user can start/stop this directly from the console — `/learn [label]` and
`/learn stop` (no LLM) — or you can call the tools. While recording, an on-screen
HUD shows `● learning: <label> — N step(s)` and a toast appears after **each
recorded step**, so the user knows the agent is ready for the next action.

To learn a workflow the user performs:
1. `start_learning_session(label="<task>")` — records the user's real
   mouse/keyboard actions and, **after each action, a settled `WindowState`**
   (controls, menu, OCR text, stable keys, window-relative footprint) for the
   foreground window; app switches are tracked in order. The window where
   `/learn` was typed and shell/taskbar windows are ignored; a click is bound to
   the next settled state of **its own** app (a click in app A whose target is
   app B is buffered, not leaked). A toggle whose label flips (e.g.
   `Подключиться`→`Подключено`) becomes a **separate state**, so replay won't
   click it the wrong way.
2. Ask the user to perform the task; observe (do not interfere).
3. `stop_learning_session()` — returns the session directory with `events.jsonl`,
   `states.jsonl`, `app_timeline.json` and click screenshots.
4. `summarize_learning_session(label=...)` — deterministically (no LLM) extends
   each app's profile with the observed **states** (new layouts, refreshed
   footprints, merged named controls) and writes a **cross-app workflow draft**
   (apps in order, steps referencing `state` + named control) to
   `data/tmp/_draft_<label>.md` **and an executable program**
   `data/tmp/_draft_<label>.workflow.json` (drafts never go into `workflows/`);
   the tool returns both paths. It **drops the agent's own console** from the
   recording and reports `dropped_console_records`/`dropped_console_events`.
   It is **additive** (never removes states), dedupes by **detect-signature**
   (a byte-identical `detect` refreshes, never adds a twin) and returns a per-app
   diff: `mode` (create/merge), `before`/`after` (states + bytes),
   `states_added`/`states_refreshed`/`states_removed`, `renames` and the `guide`
   action. New states get a **semantic id** from their distinguishing control
   label (e.g. `подключено`); matched states keep their existing id. An existing
   `.md` guide is **never overwritten** — only its managed
   `<!-- PROLIGHT:STATES -->` block is refreshed (`sync_profile_guide` fixes a
   stale hash; `rename_profile_state(renames={...})`, `delete_profile_state`,
   `delete_profile_footprint` manage states). With `save=false` it is a dry run
   (`dry_run:true` + `profile_merges` previews with `applied:false`,
   drafts in `data/tmp/`).
5. Read the draft file, refine and `save_workflow(...)` (and
   `save_workflow_program(...)` for the executable program), then `ask_user(...)` to confirm.
6. `learning_status()` tells you whether a recording is active; when it is not it
   returns the most recent session on disk, so "already stopped", "never
   started" and "crashed" are distinguishable (stopping twice returns
   `already_stopped:true` + the last session).
7. `list_links()` finds dangling `workflows/…` / `interaction_guides/…`
   references between guides and workflows.

### Heartbeat
A workflow may define a deferred/repeating self-check (e.g. "wait for the
supplier's reply, then process it"). Record that under **Follow-ups** in the
workflow; scheduled checks are handled by the heartbeat mechanism.

### Rules
- Before a task, check for a matching workflow/guide and load it into context.
- **Edit guides with `guide_upsert(app, section, content, mode)`**, not with
  `fs_*`: it changes one `## section` and preserves every other section and the
  managed `PROLIGHT:STATES` block, so a partial edit can never truncate the file.
  `note_fact` is the append-only shortcut. Use `save_interaction_guide` only to
  create a guide from scratch.
- When a stored guide or workflow turns out to be wrong, correct the file
  immediately (`guide_upsert`/`save_workflow`/`note_fact`) and tell the user.
- Never invent facts about an app. If unsure, observe, ask (`ask_user`), or test
  with `screen_probe`.
