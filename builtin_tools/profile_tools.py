"""Profile tools for ProLight-agent (Phase 2).

Manage structured app profiles (``interaction_guides/<key>.profile.json``) and
the deterministic router:

  - load_app_profile    : load a profile + its narrative guide for a window/app
  - save_app_profile    : create/replace a profile (validated)
  - route_app_state     : run the router on the latest snapshot (state + controls)
  - resolve_app_control : resolve one named control in the current state
  - discover_app        : deterministically build+save a profile from a window
  - execute_app_control : deterministically act on a named control (no LLM)

Backed by ``lib/profiles.py`` and ``lib/router.py``. See ``system_prompts/learning.md``.
"""

import json

from loguru import logger

from lib import learning_db, profiles, router


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


async def load_app_profile(
    hwnd: int = None, name: str = "", title: str = "", process: str = ""
) -> str:
    """Load the structured profile (and narrative guide) for an app.

    If the profile does not exist, returns ``found: false`` with a template to
    fill in and save via ``save_app_profile``. The narrative guide (``<key>.md``)
    is returned alongside when present.

    Args:
        hwnd: window to identify the app from.
        name: explicit profile key (e.g. "telegram").
        title / process: override the window title / process name.
    """
    cands = learning_db.guide_candidates(hwnd=hwnd, name=name, title=title, process=process)
    for key in cands:
        prof = profiles.load(key)
        if prof is not None:
            return _dump({
                "ok": True, "found": True, "key": key,
                "profile": prof,
                "guide": learning_db.read_guide(key) or "",
                "path": str(profiles.profile_path(key)),
                "problems": profiles.validate(prof),
            })
    default = cands[-1] if cands else (learning_db.normalize_key(name) or "unnamed")
    return _dump({
        "ok": True, "found": False, "key": default, "candidates": cands,
        "template": profiles.template(default),
        "hint": "No profile yet. Build one (states/controls/detect), then save_app_profile.",
    })


async def save_app_profile(name: str, profile: str = "", profile_json: str = "") -> str:
    """Create or replace an app profile (``interaction_guides/<name>.profile.json``).

    Args:
        name: profile key / app name.
        profile: the profile object (dict) or a JSON string.
        profile_json: JSON string alternative (when ``profile`` is not used).
    """
    data = profile
    if isinstance(data, str):
        data = data or profile_json
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as e:
            return _dump({"ok": False, "error": f"invalid JSON: {e}"})
    if not isinstance(data, dict):
        return _dump({"ok": False, "error": "profile must be a JSON object"})
    data.setdefault("key", learning_db.normalize_key(name))
    path = profiles.save(name, data)
    return _dump({"ok": True, "key": data["key"], "path": str(path),
                  "problems": profiles.validate(data)})


async def route_app_state() -> str:
    """Run the deterministic router on the latest ``win_snapshot``.

    Returns the matched app/state, resolved named controls (with live ids) and
    the fragment the agent would inject. Call ``win_snapshot`` first.
    """
    from lib import window_state
    state = window_state.get()
    if state is None or not state.window:
        return _dump({"ok": False, "error": "no snapshot — call win_snapshot first"})
    win = state.window or {}
    found = profiles.find_profile(
        hwnd=state.hwnd, title=win.get("title", ""), process=win.get("process", ""))
    if not found:
        return _dump({"ok": True, "matched": False,
                      "hint": "no profile for this window (load_app_profile / save_app_profile)"})
    key, prof = found
    result = router.route(prof, state, key)
    return _dump({"ok": True, "matched": bool(result.get("state_id")),
                  "route": result, "volatile": router.volatile_text(result, prof)})


async def resolve_app_control(name: str) -> str:
    """Resolve one logical control name (from the current profile state) to its
    live element id / screen centre on the latest snapshot."""
    from lib import window_state
    state = window_state.get()
    if state is None or not state.window:
        return _dump({"ok": False, "error": "no snapshot — call win_snapshot first"})
    win = state.window or {}
    found = profiles.find_profile(
        hwnd=state.hwnd, title=win.get("title", ""), process=win.get("process", ""))
    if not found:
        return _dump({"ok": False, "error": "no profile for this window"})
    key, prof = found
    res = router.resolve_named(prof, state, name)
    if not res:
        return _dump({"ok": False, "error": f"control {name!r} not resolveable",
                      "profile": key})
    return _dump({"ok": True, "profile": key, "control": name, **res})


async def discover_app(
    hwnd: int = None, name: str = "", save: bool = True,
    include_ocr: bool = True, write_guide: bool = True,
) -> str:
    """Deterministically discover an app: snapshot it, derive a profile
    (trigger, state detect rules, named controls, footprint) with no LLM call,
    and save ``<key>.profile.json`` (+ a starting ``<key>.md`` if none exists).

    Args:
        hwnd: window to discover (default: foreground).
        name: profile key (default: resolved from the window/process).
        save: write the profile (and guide) to disk.
        include_ocr: also map OCR texts to named controls.
        write_guide: write ``<key>.md`` when none exists.
    """
    from lib import discovery
    result = await discovery.discover_app(
        hwnd=hwnd, name=name, save=save, include_ocr=include_ocr, write_guide=write_guide)
    return _dump(result)


async def execute_app_control(name: str, action: str = "click", hwnd: int = None) -> str:
    """Deterministically act on a profile's named control (no LLM).

    Resolves ``name`` against the current profile state and the latest snapshot,
    then performs ``action``: click | double_click | right_click | hotkey | focus.

    Args:
        name: logical control name from the current profile state.
        action: the action to perform.
        hwnd: target window (default: the snapshot's window).
    """
    from lib import window_state
    from builtin_tools import uia_tools, mouse_tools, keybd_tools, win_tools

    state = window_state.get()
    if state is None or not state.window:
        return _dump({"ok": False, "error": "no snapshot — call win_snapshot first"})
    win = state.window or {}
    found = profiles.find_profile(
        hwnd=state.hwnd, title=win.get("title", ""), process=win.get("process", ""))
    if not found:
        return _dump({"ok": False, "error": "no profile for this window"})
    key, prof = found
    res = router.resolve_named(prof, state, name)
    if not res:
        return _dump({"ok": False, "error": f"control {name!r} not resolved in any state",
                      "profile": key})
    spec = {}
    for st in prof.get("states") or []:
        c = (st.get("controls") or {}).get(name)
        if isinstance(c, dict):
            spec = c
            break
    action = (action or "click").lower()
    target = int(hwnd) if hwnd is not None else (state.hwnd or 0)

    if action == "focus":
        return await win_tools.win_ensure_foreground(target)
    if action == "hotkey":
        combo = spec.get("hotkey")
        if not combo:
            return _dump({"ok": False, "error": f"control {name!r} has no hotkey"})
        return await keybd_tools.keybd_hotkey(combo, hwnd=target or None)
    if action in ("click", "double_click", "right_click"):
        if res.get("id") and action != "right_click":
            return await uia_tools.win_click_control(
                hwnd=target, id=res["id"], double=(action == "double_click"))
        center = res.get("center")
        if not center:
            return _dump({"ok": False, "error": f"no centre for {name!r}"})
        button = "right" if action == "right_click" else "left"
        clicks = 2 if action == "double_click" else 1
        return await mouse_tools.mouse_click(x=center[0], y=center[1], button=button, clicks=clicks)
    return _dump({"ok": False, "error": f"unknown action {action!r}"})


GROUP = "learning"


TOOL_DEFINITIONS = [
    (
        "load_app_profile",
        load_app_profile,
        "Load the structured profile (and narrative guide) for an app. Profiles "
        "make an app programmable: triggers, per-state detect rules, injected "
        "fragments and named control mappings.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window to identify the app from"},
                "name": {"type": "string", "description": "Explicit profile key, e.g. 'telegram'"},
                "title": {"type": "string", "description": "Override the window title"},
                "process": {"type": "string", "description": "Override the process name"},
            },
            "required": [],
        },
    ),
    (
        "save_app_profile",
        save_app_profile,
        "Create or replace an app profile (interaction_guides/<name>.profile.json): "
        "trigger, states (detect rules + text + named controls) and footprints.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Profile key / app name"},
                "profile": {"type": "string", "description": "Profile object (dict) or JSON string"},
                "profile_json": {"type": "string", "description": "JSON string alternative"},
            },
            "required": ["name"],
        },
    ),
    (
        "route_app_state",
        route_app_state,
        "Run the deterministic router on the latest win_snapshot: returns the "
        "matched app state, resolved named controls with live ids, and the "
        "fragment that would be injected.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "resolve_app_control",
        resolve_app_control,
        "Resolve one logical control name from the current profile state to its "
        "live element id / screen centre.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Logical control name"}},
            "required": ["name"],
        },
    ),
    (
        "discover_app",
        discover_app,
        "Deterministically discover an app: snapshot it, derive a profile "
        "(trigger, state detect rules, named controls, footprint) with no LLM "
        "call, and save <key>.profile.json (+ a starting <key>.md if none).",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window to discover (default: foreground)"},
                "name": {"type": "string", "description": "Profile key (default: resolved from window/process)"},
                "save": {"type": "boolean", "description": "Write the profile/guide to disk (default true)"},
                "include_ocr": {"type": "boolean", "description": "Also map OCR texts to controls (default true)"},
                "write_guide": {"type": "boolean", "description": "Write <key>.md when none exists (default true)"},
            },
            "required": [],
        },
    ),
    (
        "execute_app_control",
        execute_app_control,
        "Deterministically act on a profile's named control (no LLM): resolves "
        "the name against the current state and snapshot, then clicks / "
        "double-clicks / right-clicks / sends its hotkey / focuses the window.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Logical control name from the current profile state"},
                "action": {"type": "string", "description": "click | double_click | right_click | hotkey | focus"},
                "hwnd": {"type": "integer", "description": "Target window (default: the snapshot's window)"},
            },
            "required": ["name"],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} profile tool(s)")
