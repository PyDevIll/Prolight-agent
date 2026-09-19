# AGENTS.md

Windows desktop GUI-automation agent ("ProLight"). Console-only, **non-admin**, asyncio. It perceives the desktop (window capture + DeepSeek vision) and acts on it (SendInput mouse/keyboard). No tests, lint, build, or CI.

## Run

- `python main.py` from the repo root (use `.venv`). Needs `.env` with `DEEPSEEK_API_KEY_MASTERMIND` and `DEEPSEEK_API_KEY_HELPER`.
- Interactive `ProLight> ` console: type a task; `/q` `/quit` `/exit` to stop. `load_dotenv()` has no path, so `.env` is found only when CWD = repo root.
- Static check only: `python -m py_compile <files>`. There is no test/lint/typecheck command.
- First launch can look hung: cold imports are slow under endpoint protection (`import openai` ~77 s cold, ~5 s warm; `scipy` adds a few seconds). Start once and wait.
- `git` may not be on PATH; portable git is `C:\Users\PC-3111\Documents\Dev\Git\App\Git\cmd\git.exe`.

## LLM / vision

- One provider for everything: DeepSeek `deepseek-flash` (reasoning **and** multimodal), `base_url=https://api.deepseek.com` (`agent.py:20,83`, `lib/vision_client.py:22`).
- Key lookup is `DEEPSEEK_API_KEY_<AGENT_NAME>` → fallback `DEEPSEEK_API_KEY` (`agent.py:86`). Agents are `MASTERMIND` and `HELPER`, so the name is load-bearing.
- `deepseek-flash` is a reasoning model: give vision calls a generous `max_tokens` or `content` returns empty (reasoning eats the budget). `analyze_image` falls back to `reasoning_content`.
- Images are downscaled to max side 1600 px and JPEG-compressed (q80) before sending (`vision_client.py`).

## Architecture

- `main.py` → `app.start_app()`: builds a global `agent`, a global `request_queue`, one sequential `worker()`, and the console loop.
- Request flow: console → `{"type":"user","prompt":...}` → `worker()` → `components/cmd_line.py` → `agent.run_with_crash_recovery()`. Only the `user` type is handled.
- Shutdown: `get_command()` reads stdin on a **daemon thread from a dup of fd 0** — not `sys.stdin`. `asyncio.to_thread(input)` hangs exit (asyncio.run joins the default executor and the thread never returns), while a daemon `input()` crashes finalization (`_enter_buffered_busy ... due to daemon threads`) by holding stdin's buffer lock. `start_app()` waits with `asyncio.wait(..., FIRST_COMPLETED)` and cancels the survivor, so `/q`, EOF, and Ctrl+C all exit cleanly (`main.py` catches `KeyboardInterrupt`).
- `lib/` = low-level layers: `winapi.py` (ctypes user32/gdi32/kernel32/dwmapi), `input_backend.py` (SendInput + WM_ fallback), `ui_tree.py` (pywinauto UIA control discovery), `image_ops.py` (crop/grab/pixel-diff), `vision_client.py` (transport), `vision_agent.py` (vision sub-agent), `console.py`.
- `builtin_tools/` = the agent's tools; `system_prompts/` = the agent's runtime prompt (core/desktop/tools_guidelines/learning) — editing these changes behavior.
- `agent.py`, `context_manager.py`, `tool_registry.py` originated as verbatim copies of the sibling iNysha project; `context_manager.py` has since diverged (token-driven compression), and `tool_registry.py` hardcodes the `builtin_tools` package name — keep it.
- Memory/compression (`context_manager.py`): the trigger is **token-driven** — `get_assembled_tokens()` (base prompts + persistent + compressed + masked + sliding) vs `max_tokens` at `COMPRESS_TRIGGER_RATIO` (0.70), not message count. `agent.py` MUST set `messages.base_prompt_tokens` or the budget under-counts. Summaries are parsed from `## Heading` / `**Heading:**` sections (the `Narrative` is capped to ~1-2 sentences); `data/last_compression.txt` is seeded into `_compressed_dicts` on load instead of being injected as a base prompt (avoids duplication).

## Tools

- Tools MUST be `async def`; the registry awaits them.
- Each module defines a bottom-level `TOOL_DEFINITIONS = [(name, func, description, params_schema), ...]` plus `register_all(registry)` looping over it. Return a string (modules use a local `_dump()` = `json.dumps(..., ensure_ascii=False)`).
- Schema property keys must match the function's kwarg names exactly; params with defaults must NOT be in `required`.
- New module file → add it to BOTH the import list and the `register_all()` body in `builtin_tools/__init__.py`.
- `reload_tools` hot-reloads the submodules and the package `__init__.py`, so a new module added to that import list is picked up without a restart.
- Per-tool timeout 120 s (`agent.py:141`); agent loop caps at 7 iterations (`agent.py:339`).
- When you add a tool, document it in `system_prompts/desktop.md` / `tools_guidelines.md` (both are injected into the LLM).
- **Perception is snapshot/diff** (`builtin_tools/win_tools.py` + `lib/window_state.py`): `win_snapshot` returns identity, focus, menu, UIA controls and OCR text in ONE call, each with an id (`c7`/`t3`/`m2`); `win_changes` returns only the delta (UIA/OCR/pixel) and can `wait_for` an element. Ids are stable across snapshots (one-to-one identity matching). Act by id via `win_click_control(id=...)` / `win_set_control_text(id=...)` / `mouse_click(id=...)`. `lib/window_state.py` also does the Chromium a11y auto-enable (via `winapi.send_getobject`, `WM_GETOBJECT`/`UiaRootObjectId`).
- OCR (`lib/ocr.py`): Windows.Media.Ocr via modular PyWinRT (`winrt-*` packages in `requirements.txt`), run on a dedicated worker thread (own asyncio loop, like `ui_tree`). `recognize_auto` tries every installed language and keeps the richest (needed for Cyrillic). Windows OCR packs: `en-US`, `ru`.
- UI Automation actions (`builtin_tools/uia_tools.py`, backed by `lib/ui_tree.py`): `win_click_control`, `win_set_control_text`. Discovery moved into `win_snapshot` (the old `win_enum_controls`/`win_get_control_rects`/`win_find_controls`/`win_wait_for` tools were removed). UIA runs on one dedicated STA worker thread (`lib/ui_tree._run`); `sys.coinit_flags = 2` must be set before comtypes/pywinauto are imported.
- Deterministic locator (`builtin_tools/search_tools.py`, backed by `lib/image_ops.py` using numpy + scipy): `screen_find(kind="color"|"template"|"text")` — colour blobs (`scipy.ndimage.label`), FFT template match, and OCR text search. Replaces `screen_find_color`/`screen_find_template`. Returns screen coordinates. `image_ops.snap_box` refines a fraction/approximate box to exact pixels (used by `vision_locate`).
- Focus/input reliability: `win_focus` returns `keyboard_focus` (via `GetGUIThreadInfo`); `win_ensure_foreground(probe=true)` injects a benign key and returns `keyboard_delivery` (`winapi.verify_keyboard`), distinguishing `mouse_ok` from `keyboard_ok`. `keybd_*` accept an optional `hwnd` and refuse loudly if it lacks keyboard focus (SendInput fails silently otherwise). `win_get_foreground` was removed (the snapshot's `focus` covers it).
- Discovery probe (`builtin_tools/probe_tools.py`): `screen_probe(x, y, action="hover"|"click"|"scroll", ...)` — BEFORE/AFTER pixel diff; auto-focuses for click/scroll (a wheel needs the window focused), `undo_hotkey` for clicks, `undo` for scroll. Used by the learning discovery pass.

## Vision sub-agent

- `lib/vision_agent.py` (`VisionAgent`, singleton via `get_vision_agent()`) is a **separate** assistant on the **HELPER** key (`DEEPSEEK_API_KEY_VISION` → `_HELPER` → `DEEPSEEK_API_KEY`). Calls are **one-shot** (system prompt + the current crop only) so screenshots never touch the main agent's context or prefix cache and a stale frame can never bias a fresh observation.
- It looks at small regions/controls, not whole windows: `look()` crops via `lib/image_ops.py` (mss screen grab, or PrintWindow when occluded), saves to `data/vision/`, and records labelled *watches*.
- `compare(label)` re-captures and sends BEFORE+AFTER images in one request; `changed(label)` is a pixel-diff with **no** LLM call — call it first (via `vision_compare(pixel_only=true)`) to avoid a needless vision request.
- `locate()` asks the model for element boxes as **fractions** of the crop (the model is reliable at fractions, not pixels), then converts to pixels and refines with `image_ops.snap_box`. Boxes are hints — exact geometry still comes from UIA/OCR/colour.
- Cross-request continuity is **structural**, not conversational: a labelled `watch` (region + last image) drives `compare`/`changed`; the main agent carries the text answers in its own context. There is no vision-side message history.
- Tools: `vision_look` (also handles an image `path`), `vision_compare` (with `pixel_only`), `vision_forget` (with `list_only`). `win_snapshot(vision_query=...)` routes through the same agent (so all live vision uses the HELPER key).
- Do NOT call `lib/vision_client.analyze_image`/its shared `_get_client()` directly: that default client uses the **MASTERMIND** key (`vision_client.py:32`) and is a dead path. `vision_agent` always passes its own HELPER client into `analyze_messages`.

## Windows / input gotchas (hard-won)

- `import lib.winapi` calls `ensure_dpi_awareness()` at import time → all coordinates are physical pixels. Do not rescale.
- Call `force_utf8_console()` (`lib.console`) before the first print/log; Cyrillic titles/answers otherwise crash or mojibake. `main.py`/`app.py` already do — do the same in any new entrypoint.
- SendInput only reaches the **foreground** window: `win_focus(hwnd)` and verify before mouse/keyboard input.
- **UIPI**: a non-admin process cannot send input to elevated (admin) windows — those targets are off-limits.
- UIA discovery is the reliable locator for native Win32 controls; browsers/Electron/1C/custom-drawn UIs often expose an empty/poor tree — `win_snapshot` reports `uia.coverage` and falls back to OCR text / `screen_find`.
- `win_send_message` (PostMessage WM_*) is a fallback for standard Win32 controls only; browsers/Electron/1C/custom UIs ignore it.
- Menu bars are NON-client area, so `GetClientRect` excludes them — `win_snapshot` includes menu items (id `m*`) with screen rects; click those, not client-relative offsets.
- `winapi.capture_window` returns `None` if both PrintWindow flags fail (never an uninitialized bitmap).

## Learning DB

- `interaction_guides/<key>.md` and `workflows/<task>.md` are the agent's learned-fact databases, managed by `lib/learning_db.py` + `builtin_tools/learning_tools.py` (see `system_prompts/learning.md`). Guide key resolution: explicit name → window-title tail → title head → process name (`guide_candidates`). Neither dir is gitignored; `workflows/` is created on demand.
- Learning mode records the user's real actions with `components/tracker.py` (pynput global hooks → `data/sessions/<label>_<ts>/events.jsonl` + a whole-window JPEG per click; the control under the cursor via `ui_tree.element_at_point_sync`). It keeps the last `MAX_SESSIONS` (10) sessions; `data/` is gitignored.
- `ask_user` (in `meta_tools.py`) is a **blocking** question: `app.ask_user_question` sets `_pending_question`, and the console loop routes the next line to it instead of enqueuing a request.

## Git hygiene

- `.gitignore` covers `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `data/`, `reference_sources/`.
- Written to the repo root on failure and NOT ignored — never commit: `failed_messages.json`, `failed_context.json`, `invalid_messages.json`.
- `.env` holds live DeepSeek keys — never print or commit.

## Phase / roadmap

- Phases 0–3 done (scaffold, perception, actuation, UIA control discovery) plus the vision sub-agent, the perception consolidation (snapshot/diff + OCR locator) and **Phase 4 learning** (`lib/learning_db.py`, `builtin_tools/learning_tools.py`, `components/tracker.py`, `screen_probe`, blocking `ask_user`). Next is Phase 5 (heartbeat).
- Roadmap: `GENERATED_PLAN.txt` (no `DEVLOG.txt`); requirements: `APP_SPECS_OUTLINES.txt`; real-world friction log: `ISSUES_AND_IMPROVEMENT_IDEAS.txt`.
- Planned but not yet created: `builtin_tools/app_tools.py` (launch/list/activate/close), `components/heartbeat.py` (Phase 5).
- `reference_sources/` (gitignored) holds the verbatim iNysha copies and `UniClicker_sample_source/` (Delphi input-capture reference) — reference only, don't edit.
