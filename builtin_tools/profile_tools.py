"""Profile tools for ProLight-agent (Phase 2).

Manage structured app profiles (``interaction_guides/<key>.profile.json``) and
the deterministic router:

  - load_app_profile    : load a profile + its narrative guide for a window/app
  - save_app_profile    : create/replace a profile (validated)
  - route_app_state     : run the router on the latest snapshot (state + controls)
  - resolve_app_control : resolve one named control in the current state
  - discover_app        : deterministically build+save a profile from a window
  - execute_app_control : deterministically act on a named control (no LLM)
  - validate_profile    : schema + routing self-test of a profile
  - rename_profile      : fix a bad/auto-generated key
  - delete_profile      : remove a junk/obsolete profile

Backed by ``lib/profiles.py`` and ``lib/router.py``. See ``system_prompts/learning.md``.
"""

import json
from datetime import date

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
    from lib import window_state
    cands = learning_db.guide_candidates(hwnd=hwnd, name=name, title=title, process=process,
                                         url=window_state.active_url())
    for key in cands:
        prof = profiles.load(key)
        if prof is not None:
            return _dump({
                "ok": True, "found": True, "key": key,
                "profile": prof,
                "guide": learning_db.read_guide(key) or "",
                "path": str(profiles.profile_path(key)),
                "guide_stale": profiles.guide_stale(prof),
                "problems": profiles.validate(prof),
            })
    default = cands[-1] if cands else (learning_db.normalize_key(name) or "unnamed")
    return _dump({
        "ok": True, "found": False, "key": default, "candidates": cands,
        "template": profiles.template(default),
        "hint": "No profile yet. Build one (states/controls/detect), then save_app_profile.",
    })


def _as_obj(value):
    """Accept a dict/list or a JSON string for a delta argument."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return value


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                v = json.loads(s)
            except Exception:
                return [value]
            return v if isinstance(v, list) else [v]
        return [value]
    return value if isinstance(value, list) else [value]


def _unique_state_id(prof: dict) -> str:
    ids = {s.get("id") for s in prof.get("states") or []}
    if not ids:
        return "main"
    i = len(ids) + 1
    while f"state_{i}" in ids:
        i += 1
    return f"state_{i}"


def _merge_delta(prof: dict, *, add_state=None, patch_state=None, remove_state=None,
                 patch_trigger=None, set_initial_text=None) -> tuple:
    """Apply typed deltas to ``prof`` in place. Returns ``(diff, error)`` (R23)."""
    prof.setdefault("trigger", {})
    prof.setdefault("states", [])
    prof.setdefault("footprints", {})

    trigger_changed: list = []
    if patch_trigger:
        pt = _as_obj(patch_trigger)
        if not isinstance(pt, dict):
            return None, "patch_trigger must be an object"
        for k, v in pt.items():
            if v is None:
                prof["trigger"].pop(k, None)
                trigger_changed.append(f"{k}: removed")
            else:
                prof["trigger"][k] = v
                trigger_changed.append(f"{k}: set")

    removed: list = []
    for sid in _as_list(remove_state):
        if not sid:
            continue
        before = len(prof["states"])
        prof["states"] = [s for s in prof["states"] if s.get("id") != sid]
        prof["footprints"].pop(sid, None)
        if len(prof["states"]) < before:
            removed.append(sid)

    added: list = []
    for s in _as_list(add_state):
        if not isinstance(s, dict):
            return None, "add_state entries must be objects"
        sid = s.get("id") or _unique_state_id(prof)
        if any(x.get("id") == sid for x in prof["states"]):
            return None, f"add_state: id {sid!r} already exists"
        prof["states"].append({"id": sid, "detect": s.get("detect") or {},
                               "text": s.get("text", ""), "controls": s.get("controls") or {}})
        if isinstance(s.get("footprint"), dict):
            prof["footprints"][sid] = s["footprint"]
        added.append(sid)

    patched: list = []
    for ps in _as_list(patch_state):
        if not isinstance(ps, dict) or not ps.get("id"):
            return None, "patch_state entries need an 'id'"
        sid = ps["id"]
        target = next((x for x in prof["states"] if x.get("id") == sid), None)
        if target is None:
            return None, f"patch_state: no state {sid!r}"
        for k, v in ps.items():
            if k == "id":
                continue
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                target[k] = {**target[k], **v}          # deep-merge detect/controls
            else:
                target[k] = v
        patched.append(sid)

    if set_initial_text is not None:
        prof["initial_text"] = str(set_initial_text)

    return ({"states_added": added, "states_patched": patched, "states_removed": removed,
             "trigger_changed": trigger_changed}, None)


async def save_app_profile(name: str, profile: str = "", profile_json: str = "",
                           add_state=None, patch_state=None, remove_state=None,
                           patch_trigger=None, set_initial_text=None) -> str:
    """Create or replace an app profile, **or** apply small deltas to an existing one.

    Full replace: pass ``profile``/``profile_json`` (the whole object).
    Delta edit (R23): pass any of ``add_state`` / ``patch_state`` / ``remove_state``
    / ``patch_trigger`` / ``set_initial_text`` — the existing profile is loaded,
    the ops applied, ``version`` auto-incremented, and only a per-op diff returned
    (no need to re-emit the whole JSON). ``patch_state`` deep-merges ``detect``/
    ``controls``.

    Args:
        name: profile key / app name.
        profile: the full profile object (dict) or a JSON string (full replace).
        profile_json: JSON string alternative for a full replace.
        add_state: a state object (or list) to append.
        patch_state: ``{id, …}`` (or list) — fields to merge into a state.
        remove_state: a state id (or list) to delete (with its footprint).
        patch_trigger: ``{process: [...]}`` etc. to merge (a ``null`` value removes a key).
        set_initial_text: replace ``initial_text``.
    """
    delta_requested = any(v is not None for v in
                          (add_state, patch_state, remove_state, patch_trigger, set_initial_text))
    data = profile or profile_json

    if data and not delta_requested:
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as e:
                return _dump({"ok": False, "error": f"invalid JSON: {e}"})
        if not isinstance(data, dict):
            return _dump({"ok": False, "error": "profile must be a JSON object"})
        data.setdefault("key", learning_db.normalize_key(name))
        path = profiles.save(name, data)
        return _dump({"ok": True, "mode": "replace", "key": data["key"], "path": str(path),
                      "version": data.get("version"), "problems": profiles.validate(data)})

    # Delta mode
    prof = profiles.load(name)
    if prof is None:
        return _dump({"ok": False, "error": f"no profile {name!r} — pass 'profile' to create it"})
    diff, err = _merge_delta(prof, add_state=add_state, patch_state=patch_state,
                             remove_state=remove_state, patch_trigger=patch_trigger,
                             set_initial_text=set_initial_text)
    if err:
        return _dump({"ok": False, "error": err, "applied": False})
    prof["version"] = int(prof.get("version", 1) or 1) + 1
    if diff["states_added"] or diff["states_removed"]:
        prof["guide_synced"] = {"ids_hash": profiles.states_hash(prof),
                                "states": [s.get("id") for s in prof["states"]],
                                "updated": date.today().isoformat()}
    path = profiles.save(name, prof)
    if diff["states_added"] or diff["states_removed"]:
        try:
            from lib import session_analysis
            session_analysis.sync_guide_block(name, prof)
        except Exception:
            pass
    logger.info(f"save_app_profile delta: {name} {diff}")
    return _dump({"ok": True, "mode": "delta", "key": learning_db.normalize_key(name),
                  "path": str(path), "version": prof["version"], "diff": diff,
                  "states": [s.get("id") for s in prof["states"]],
                  "problems": profiles.validate(prof)})


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
        hwnd=state.hwnd, title=win.get("title", ""), process=win.get("process", ""),
        url=state.active_url())
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
        hwnd=state.hwnd, title=win.get("title", ""), process=win.get("process", ""),
        url=state.active_url())
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
        hwnd=state.hwnd, title=win.get("title", ""), process=win.get("process", ""),
        url=state.active_url())
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


async def validate_profile(name: str = "") -> str:
    """Validate one profile (JSON + a routing self-test on the latest snapshot).

    With no ``name``, validates every profile and reports problems per key.

    Args:
        name: profile key to validate (default: all profiles).
    """
    if name:
        prof = profiles.load(name)
        if prof is None:
            return _dump({"ok": False, "error": f"no profile {name!r}",
                          "available": [p["key"] for p in profiles.list_profiles()]})
        out = {"ok": True, "key": prof.get("key") or learning_db.normalize_key(name),
               "problems": profiles.validate(prof),
               "guide_stale": profiles.guide_stale(prof),
               "states": [s.get("id") for s in prof.get("states") or []]}
        from lib import window_state
        st = window_state.get()
        if st is not None and st.window:
            res = router.route(prof, st, prof.get("key", ""))
            out["route"] = {
                "state_id": res.get("state_id"),
                "footprint_ok": res.get("footprint_ok"),
                "missing_keys": res.get("missing_keys"),
                "moved_keys": res.get("moved_keys"),
                "layout_changed": res.get("layout_changed"),
                "resolved_controls": sorted((res.get("controls") or {}).keys()),
            }
        else:
            out["route"] = None
        return _dump(out)
    out = []
    for item in profiles.list_profiles():
        prof = profiles.load(item["key"])
        out.append({"key": item["key"],
                    "problems": profiles.validate(prof) if prof else ["unreadable"]})
    return _dump({"ok": True, "profiles": out})


async def rename_profile(old: str, new: str) -> str:
    """Rename a profile key (and its guide) — useful to fix a bad key.

    Args:
        old: current profile key.
        new: desired key.
    """
    prof = profiles.load(old)
    if prof is None:
        return _dump({"ok": False, "error": f"no profile {old!r}"})
    newkey = learning_db.normalize_key(new)
    old_path = profiles.profile_path(old)
    prof["key"] = newkey
    new_path = profiles.save(newkey, prof)
    if old_path.exists() and old_path != new_path:
        old_path.unlink()
    old_md = learning_db.GUIDES_DIR / f"{learning_db.normalize_key(old)}.md"
    new_md = learning_db.GUIDES_DIR / f"{newkey}.md"
    if old_md.exists() and not new_md.exists() and old_md != new_md:
        old_md.rename(new_md)
    logger.info(f"rename_profile: {old!r} -> {newkey!r}")
    return _dump({"ok": True, "key": newkey, "path": str(new_path)})


async def delete_profile(key: str, also_guide: bool = False) -> str:
    """Delete a profile (and optionally its ``.md`` guide).

    Args:
        key: profile key.
        also_guide: also delete ``<key>.md``.
    """
    p = profiles.profile_path(key)
    removed = {}
    if p.exists():
        p.unlink()
        removed["profile"] = str(p)
    if also_guide:
        md = learning_db.GUIDES_DIR / f"{learning_db.normalize_key(key)}.md"
        if md.exists():
            md.unlink()
            removed["guide"] = str(md)
    logger.info(f"delete_profile: {key!r} removed={list(removed)}")
    return _dump({"ok": bool(removed), "key": learning_db.normalize_key(key), "removed": removed})


async def rename_profile_state(key: str, old: str = "", new: str = "",
                               renames: str = "") -> str:
    """Rename one or more profile state ids (and their footprints + guide block).

    Accepts either ``old``/``new`` or a ``renames`` map (``{"state_3": "watch"}``
    — a JSON string or object), so several ids can be re-pointed in one pass
    (R17). Returns the applied ``renames`` map; workflows/guides can then be
    updated. The managed guide block is refreshed.

    Args:
        key: profile key.
        old: current state id (single rename).
        new: new state id (single rename).
        renames: a ``{old: new}`` map for multiple renames (JSON string or object).
    """
    from lib import session_analysis
    mapping: dict = {}
    if renames:
        try:
            mapping = json.loads(renames) if isinstance(renames, str) else dict(renames)
        except Exception as e:
            return _dump({"ok": False, "error": f"invalid renames map: {e}"})
    if old and new:
        mapping[old] = new
    if not mapping:
        return _dump({"ok": False, "error": "provide old/new or a renames map"})

    prof = profiles.load(key)
    if prof is None:
        return _dump({"ok": False, "error": f"no profile {key!r}"})
    ids = [s.get("id") for s in prof.get("states") or []]
    applied: dict = {}
    result_ids = list(ids)
    for o, n in mapping.items():
        new_id = learning_db.normalize_key(n).replace(" ", "_") or n
        if o not in ids:
            return _dump({"ok": False, "error": f"no state {o!r}", "states": ids})
        if new_id in result_ids and new_id != o:
            return _dump({"ok": False, "error": f"state {new_id!r} already exists"})
        result_ids = [new_id if i == o else i for i in result_ids]
        applied[o] = new_id
    for s in prof.get("states") or []:
        if s.get("id") in applied:
            s["id"] = applied[s["id"]]
    fps = prof.get("footprints") or {}
    for o, n in applied.items():
        if o in fps:
            fps[n] = fps.pop(o)
    prof["guide_synced"] = {"ids_hash": profiles.states_hash(prof),
                            "states": [s.get("id") for s in prof.get("states") or []],
                            "updated": date.today().isoformat()}
    path = profiles.save(key, prof)
    gpath, gaction = session_analysis.sync_guide_block(key, prof)
    logger.info(f"rename_profile_state: {key} {applied}")
    return _dump({"ok": True, "key": key, "renames": applied, "path": str(path),
                  "states": [s.get("id") for s in prof.get("states") or []],
                  "guide": {"path": gpath, "action": gaction}})


async def delete_profile_state(key: str, id: str) -> str:
    """Delete a state (and its footprint) from a profile; refresh the guide block.

    Args:
        key: profile key.
        id: state id to remove.
    """
    from lib import session_analysis
    prof = profiles.load(key)
    if prof is None:
        return _dump({"ok": False, "error": f"no profile {key!r}"})
    ids = [s.get("id") for s in prof.get("states") or []]
    if id not in ids:
        return _dump({"ok": False, "error": f"no state {id!r}", "states": ids})
    prof["states"] = [s for s in prof.get("states") if s.get("id") != id]
    (prof.get("footprints") or {}).pop(id, None)
    prof["guide_synced"] = {"ids_hash": profiles.states_hash(prof),
                            "states": [s.get("id") for s in prof["states"]],
                            "updated": date.today().isoformat()}
    path = profiles.save(key, prof)
    gpath, gaction = session_analysis.sync_guide_block(key, prof)
    logger.info(f"delete_profile_state: {key} removed {id!r}")
    return _dump({"ok": True, "key": key, "removed": id,
                  "states": [s.get("id") for s in prof["states"]],
                  "path": str(path), "guide": {"path": gpath, "action": gaction}})


async def delete_profile_footprint(key: str, id: str) -> str:
    """Delete one state's footprint from a profile (without removing the state).

    Args:
        key: profile key.
        id: state id whose footprint to drop.
    """
    prof = profiles.load(key)
    if prof is None:
        return _dump({"ok": False, "error": f"no profile {key!r}"})
    fps = prof.get("footprints") or {}
    if id not in fps:
        return _dump({"ok": False, "error": f"no footprint {id!r}", "footprints": list(fps)})
    fps.pop(id)
    path = profiles.save(key, prof)
    return _dump({"ok": True, "key": key, "removed": id, "footprints": list(fps),
                  "path": str(path)})


async def sync_profile_guide(key: str) -> str:
    """Regenerate the managed ``PROLIGHT:STATES`` block and the ``guide_synced`` hash.

    Use it to clear ``guide_stale`` after a hand edit (R16): the hash is
    ``sha1(','.join(sorted(state_ids)))[:12]`` and is recomputed here from the
    profile, then the guide block is rewritten to match.

    Args:
        key: profile key.
    """
    from lib import session_analysis
    prof = profiles.load(key)
    if prof is None:
        return _dump({"ok": False, "error": f"no profile {key!r}"})
    prof["guide_synced"] = {"ids_hash": profiles.states_hash(prof),
                            "states": [s.get("id") for s in prof.get("states") or []],
                            "updated": date.today().isoformat()}
    path = profiles.save(key, prof)
    gpath, gaction = session_analysis.sync_guide_block(key, prof)
    logger.info(f"sync_profile_guide: {key} -> {gaction}")
    return _dump({"ok": True, "key": key, "profile_path": str(path),
                  "guide": {"path": gpath, "action": gaction},
                  "guide_stale": profiles.guide_stale(prof),
                  "ids_hash": prof["guide_synced"]["ids_hash"]})


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
        "Create/replace an app profile (interaction_guides/<name>.profile.json) **or** "
        "apply small deltas to an existing one without re-emitting the whole JSON: "
        "add_state / patch_state / remove_state / patch_trigger / set_initial_text. "
        "Delta mode auto-increments version and returns a per-op diff.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Profile key / app name"},
                "profile": {"type": "string", "description": "Full profile object (dict) or JSON string (replaces the file)"},
                "profile_json": {"type": "string", "description": "JSON string alternative for a full replace"},
                "add_state": {"type": "string", "description": "State object (or list) to append, e.g. {id, detect, controls, text}"},
                "patch_state": {"type": "string", "description": "{id, …} (or list) — fields to deep-merge into a state (detect/controls merge)"},
                "remove_state": {"type": "string", "description": "State id (or list) to delete, with its footprint"},
                "patch_trigger": {"type": "string", "description": "Trigger fields to merge, e.g. {process:[...]}; a null value removes a key"},
                "set_initial_text": {"type": "string", "description": "Replace initial_text"},
            },
            "required": ["name"],
        },
    ),
    (
        "route_app_state",
        route_app_state,
        "Run the deterministic router on the latest win_snapshot: returns the "
        "matched app state (most-specific rule wins), resolved named controls with "
        "live ids, also_matched (runner-up states) and rejections (why the other "
        "states failed), and the fragment that would be injected.",
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
    (
        "rename_profile_state",
        rename_profile_state,
        "Rename one or more profile state ids (and their footprints + guide block). "
        "Give old/new, or a renames map {old: new} to do several in one call. "
        "Returns the applied renames map.",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Profile key"},
                "old": {"type": "string", "description": "Current state id (single rename)"},
                "new": {"type": "string", "description": "New state id (single rename)"},
                "renames": {"type": "string", "description": "JSON map {old: new} for multiple renames"},
            },
            "required": ["key"],
        },
    ),
    (
        "delete_profile_state",
        delete_profile_state,
        "Delete a state (and its footprint) from a profile and refresh the guide block.",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Profile key"},
                "id": {"type": "string", "description": "State id to remove"},
            },
            "required": ["key", "id"],
        },
    ),
    (
        "delete_profile_footprint",
        delete_profile_footprint,
        "Delete one state's footprint from a profile (keeping the state).",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Profile key"},
                "id": {"type": "string", "description": "State id whose footprint to drop"},
            },
            "required": ["key", "id"],
        },
    ),
    (
        "sync_profile_guide",
        sync_profile_guide,
        "Regenerate the managed PROLIGHT:STATES guide block and the guide_synced hash "
        "from the profile — clears guide_stale after a hand edit. hash = "
        "sha1(','.join(sorted(state_ids)))[:12].",
        {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Profile key"}},
            "required": ["key"],
        },
    ),
    (
        "validate_profile",
        validate_profile,
        "Validate a profile: JSON/schema problems plus a routing self-test against "
        "the latest win_snapshot (matched state, footprint ok/moved, resolved "
        "controls). With no name, validates every profile.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Profile key (default: all)"}},
            "required": [],
        },
    ),
    (
        "rename_profile",
        rename_profile,
        "Rename a profile key (and its .md guide) — use it to fix an auto-generated "
        "or bad key.",
        {
            "type": "object",
            "properties": {
                "old": {"type": "string", "description": "Current profile key"},
                "new": {"type": "string", "description": "Desired key"},
            },
            "required": ["old", "new"],
        },
    ),
    (
        "delete_profile",
        delete_profile,
        "Delete a profile (optionally its .md guide) — e.g. a junk profile created "
        "from a shell/launcher window.",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Profile key"},
                "also_guide": {"type": "boolean", "description": "Also delete <key>.md (default false)"},
            },
            "required": ["key"],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} profile tool(s)")
