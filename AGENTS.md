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
- `lib/` = low-level layers: `winapi.py` (ctypes user32/gdi32/kernel32/dwmapi), `input_backend.py` (SendInput + WM_ fallback), `ui_tree.py` (pywinauto UIA control discovery), `image_ops.py` (crop/grab/pixel-diff), `vision_client.py` (transport), `vision_agent.py` (vision sub-agent), `visual_context_manager.py`, `console.py`.
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
- UI Automation tools (`builtin_tools/uia_tools.py`, backed by `lib/ui_tree.py`): `win_enum_controls`, `win_get_control_rects`, `win_find_controls`, `win_wait_for`, `win_click_control` (preferred click), `win_set_control_text` (UIA ValuePattern). UIA runs on one dedicated STA worker thread (`lib/ui_tree._run`); `sys.coinit_flags = 2` must be set before comtypes/pywinauto are imported.
- Deterministic geometry (`builtin_tools/search_tools.py`, backed by `lib/image_ops.py` using numpy + scipy): `screen_find_color` (numpy colour mask + `scipy.ndimage.label`), `screen_find_template` (FFT SSD). For custom-drawn UIs (Paint) where UIA is empty and vision coordinates are unreliable. Return screen coordinates.
- Focus/input reliability: `win_focus` returns `keyboard_focus` (verified via `GetGUIThreadInfo`, not just foreground); `win_ensure_foreground`/`win_get_foreground` added. `keybd_*` accept an optional `hwnd` and refuse loudly if it lacks keyboard focus (SendInput fails silently otherwise).

## Vision sub-agent

- `lib/vision_agent.py` (`VisionAgent`, singleton via `get_vision_agent()`) is a **separate** assistant on the **HELPER** key (`DEEPSEEK_API_KEY_VISION` → `_HELPER` → `DEEPSEEK_API_KEY`) with its **own** message history, so screenshots never touch the main agent's context or prefix cache.
- It looks at small regions/controls, not whole windows: `look()` crops via `lib/image_ops.py` (mss screen grab, or PrintWindow when occluded), saves to `data/vision/`, and remembers labelled *watches*.
- `compare(label)` re-captures and sends BEFORE+AFTER images in one request; `changed(label)` is a pixel-diff with **no** LLM call — call it first to avoid a needless vision request.
- `lib/visual_context_manager.py` (`VisualContext`) keeps only the last `keep_images` turns as images; older turns become text and are summarised into `memory` once they exceed `max_turns`/`max_text_tokens`.
- Tools: `vision_look`, `vision_compare`, `vision_changed`, `vision_watches`, `vision_forget`. `win_see` and `vision_analyze` route through the same agent via its one-shot `ask_once` (so all live vision uses the HELPER key).
- Do NOT call `lib/vision_client.analyze_image`/its shared `_get_client()` directly: that default client uses the **MASTERMIND** key (`vision_client.py:32`) and is a dead path. `vision_agent` always passes its own HELPER client into `analyze_messages`.

## Windows / input gotchas (hard-won)

- `import lib.winapi` calls `ensure_dpi_awareness()` at import time → all coordinates are physical pixels. Do not rescale.
- Call `force_utf8_console()` (`lib.console`) before the first print/log; Cyrillic titles/answers otherwise crash or mojibake. `main.py`/`app.py` already do — do the same in any new entrypoint.
- SendInput only reaches the **foreground** window: `win_focus(hwnd)` and verify before mouse/keyboard input.
- **UIPI**: a non-admin process cannot send input to elevated (admin) windows — those targets are off-limits.
- UIA discovery (`win_*control*`) is the reliable locator for native Win32 controls; browsers/Electron/1C/custom-drawn UIs often expose an empty/poor tree — fall back to vision + `mouse_click`.
- `win_send_message` (PostMessage WM_*) is a fallback for standard Win32 controls only; browsers/Electron/1C/custom UIs ignore it.
- Menu bars are NON-client area, so `GetClientRect` excludes them — use `win_get_menu_rects` to click menus, not client-relative offsets.
- `winapi.capture_window` returns `None` if both PrintWindow flags fail (never an uninitialized bitmap).

## Learning DB

- `interaction_guides/<app>.md` and `workflows/<task>.md` are the agent's learned-fact databases (see `system_prompts/learning.md`). Neither dir exists yet (Phase 4) and nothing reads/writes them; neither would be gitignored.

## Git hygiene

- `.gitignore` covers `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `data/`, `reference_sources/`.
- Written to the repo root on failure and NOT ignored — never commit: `failed_messages.json`, `failed_context.json`, `invalid_messages.json`.
- `.env` holds live DeepSeek keys — never print or commit.

## Phase / roadmap

- Phases 0–3 done (scaffold, perception, actuation, UIA control discovery) plus the vision sub-agent (region/control change-verification); next is Phase 4 (learning: guides/workflows DB + `components/tracker.py`).
- Roadmap: `DEVLOG.txt` + `GENERATED_PLAN.txt`; requirements: `APP_SPECS_OUTLINES.txt`; real-world friction log: `ISSUES_AND_IMPROVEMENT_IDEAS.txt` (Phase 3 addresses its "No OCR / UI Automation" and pixel-coordinate items).
- Planned but not yet created: `interaction_guides/`, `workflows/`, `builtin_tools/app_tools.py`, `learning_tools.py`, `components/tracker.py`, `heartbeat.py`. `pynput` and `pyperclip` are in `requirements.txt` but not imported anywhere yet.
- `reference_sources/` (gitignored) holds the verbatim iNysha copies and `UniClicker_sample_source/` (Delphi input-capture reference) — reference only, don't edit.
