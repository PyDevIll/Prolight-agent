"""Deterministic profile discovery for ProLight-agent (Phase 3).

Turns a live ``WindowState`` into a structured app profile
(``interaction_guides/<key>.profile.json``) with **no LLM call**:

  * trigger   — from the process / title;
  * a state   — deterministic ``detect`` rules (distinctive UIA controls or OCR);
  * controls  — named logical controls with deterministic match specs;
  * text      — a factual region summary (menu / editor / status bar / panes);
  * footprint — stable element keys + window-relative rects.

This makes the "first interaction with an app" fast and repeatable. The agent
may then refine names/purposes; ``lib.router`` consumes the result at runtime.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from loguru import logger

from lib import learning_db, profiles

_SLUG_RE = re.compile(r"[^0-9a-zA-Z\u0400-\u04ff]+")

_REGION_TYPES = {
    "editor": {"edit", "document", "textbox", "richtextbox"},
    "list": {"list", "listitem", "tree", "treeitem", "table", "datagrid", "listview"},
    "toolbar": {"toolbar", "button", "splitbutton", "hyperlink"},
    "tabs": {"tab", "tabitem"},
    "status": {"statusbar"},
    "menu": {"menubar", "menuitem"},
}


def slugify(s: str, fallback: str = "control") -> str:
    s = _SLUG_RE.sub("_", (s or "").strip()).strip("_").lower()
    s = re.sub(r"_{2,}", "_", s)
    return s[:40] or fallback


def _unique(base: str, used: set) -> str:
    name = base
    i = 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    used.add(name)
    return name


def classify_regions(state) -> dict:
    """Deterministic grouping of the window's elements into regions."""
    regions = {"menu": [], "toolbar": [], "editor": [], "list": [],
               "tabs": [], "status": [], "texts": []}
    for c in state.controls:
        ct = (c.control_type or "").lower()
        for region, types in _REGION_TYPES.items():
            if ct in types:
                regions[region].append(c)
                break
    for m in state.menu:
        regions["menu"].append(m)
    regions["texts"] = list(state.texts)
    return regions


def _match_spec(el) -> dict:
    if el.automation_id:
        return {"automation_id": el.automation_id}
    if el.name:
        return {"control_type": el.control_type, "name": el.name}
    return {"control_type": el.control_type}


def _spec_key(spec: dict) -> tuple:
    return (spec.get("automation_id", ""), spec.get("control_type", ""), spec.get("name", ""))


def controls_from_state(state, max_controls: int = 40, include_ocr: bool = True) -> dict:
    """Named logical controls → match specs, deterministically.

    The logical name prefers the control's **label** (e.g. a Qt button's
    ``automation_id`` is a long dotted path — the name is far cleaner); the match
    spec still prefers ``automation_id`` so it resolves reliably. Controls whose
    match spec is identical are deduplicated (no ``x_6``/``x_7`` twins).
    """
    used: set = set()
    seen: set = set()
    controls: dict = {}
    for c in state.controls[:max_controls]:
        spec = _match_spec(c)
        if _spec_key(spec) in seen:
            continue
        seen.add(_spec_key(spec))
        label = c.name if (c.name and not c.name.strip().isdigit()) \
            else (c.automation_id.split(".")[-1] if c.automation_id else (c.control_type or "control"))
        base = slugify(label)
        name = _unique(base, used)
        controls[name] = {"match": spec}
    if include_ocr:
        for t in state.texts:
            text = (t.name or "").strip()
            if len(text) < 3 or len(text) > 40:
                continue
            base = slugify(text, "text")
            if base in controls or base in used:
                continue
            name = _unique(base, used)
            controls[name] = {"match": {"ocr": text}}
    return controls


def detect_from_state(state, controls: dict) -> dict:
    """Deterministic detect rules that identify this state across runs."""
    det: dict = {}
    n = len(state.controls)
    if n:
        det["min_controls"] = min(3, n)
    # Prefer distinctive controls: those with an automation_id, ignoring
    # ubiquitous scrollbars, and favouring non-numeric ids (a numeric id such as
    # Notepad's Edit "15" is usually generic).
    cands = [c for c in state.controls
             if c.automation_id and (c.control_type or "").lower() != "scrollbar"]
    non_numeric = [c for c in cands if not c.automation_id.isdigit()]
    chosen = (non_numeric or cands)[:3]
    if chosen:
        det["uia_any"] = [{"automation_id": c.automation_id} for c in chosen]
    else:
        named = [c for c in state.controls if c.name][:3]
        if named:
            det["uia_any"] = [{"control_type": c.control_type, "name": c.name} for c in named]
        else:
            texts = [t.name.strip() for t in state.texts if 3 <= len(t.name.strip()) <= 40][:3]
            if texts:
                det["ocr_any"] = texts
    return det


def summary_text(state, regions: dict) -> str:
    win = state.window or {}
    proc = (win.get("process") or "").replace(".exe", "")
    title = win.get("title") or ""
    parts = [f"Window '{title}' (process {proc or '?'}, class {win.get('class') or '?'})."]
    if regions["menu"]:
        menu_names = [m.name for m in regions["menu"] if m.name]
        if menu_names:
            parts.append("Menu: " + " | ".join(menu_names[:10]) + ".")
    if regions["editor"]:
        e = regions["editor"][0]
        parts.append(f"Editor: '{e.name}' ({e.control_type}).")
    if regions["list"]:
        parts.append(f"Content list/tree: {len(regions['list'])} item(s).")
    if regions["toolbar"]:
        parts.append(f"Toolbar/buttons: {len(regions['toolbar'])}.")
    if regions["status"]:
        parts.append(f"Status bar ({len(regions['status'])} control).")
    parts.append(f"UIA coverage: {state.uia_coverage}; controls={len(state.controls)}, "
                 f"texts={len(state.texts)}.")
    return " ".join(parts)


def build_profile(
    state,
    key: str,
    display: str = "",
    *,
    include_ocr: bool = True,
    max_controls: int = 40,
    state_id: str = "main",
) -> dict:
    """Build a complete profile dict from a live WindowState (deterministic)."""
    win = state.window or {}
    proc = (win.get("process") or "").replace(".exe", "")
    regions = classify_regions(state)
    controls = controls_from_state(state, max_controls=max_controls, include_ocr=include_ocr)
    detect = detect_from_state(state, controls)
    text = summary_text(state, regions)
    trigger = {"process": [proc] if proc else [], "title_contains": []}
    return {
        "key": learning_db.normalize_key(key),
        "display": display or (win.get("title") or key),
        "version": profiles.SCHEMA_VERSION,
        "updated": date.today().isoformat(),
        "trigger": trigger,
        "initial_text": text,
        "states": [{"id": state_id, "detect": detect, "text": text, "controls": controls}],
        "footprints": {state_id: state.footprint(with_rects=True)},
    }


def guide_markdown(profile: dict) -> str:
    """A starting ``<key>.md`` narrative derived from the profile."""
    st = (profile.get("states") or [{}])[0]
    lines = [
        f"# {profile.get('display')} — interaction guide",
        "",
        "## Purpose / overview",
        f"- {profile.get('initial_text', '')}",
        "",
        "## Key controls & what they do",
    ]
    for name, spec in (st.get("controls") or {}).items():
        lines.append(f"- `{name}` → {spec.get('match')}")
    lines += [
        "",
        "## Reading app state",
        f"- Detected by: {st.get('detect')}",
        "",
        "## Gotchas",
        "- (fill in as you learn.)",
        "",
        "## Last verified",
        f"- {date.today().isoformat()}",
        "",
    ]
    return "\n".join(lines)


def _unique_state_id(prof: dict) -> str:
    ids = {s.get("id") for s in prof.get("states") or []}
    i = len(ids) + 1
    while f"state_{i}" in ids:
        i += 1
    return f"state_{i}"


def _detect_sig(state: dict) -> str:
    import json
    return json.dumps(state.get("detect") or {}, sort_keys=True, ensure_ascii=False)


def _merge_discovered(existing: dict, new: dict) -> tuple[dict, dict]:
    """Additively merge a freshly discovered single-state profile into ``existing``.

    A state with an identical ``detect`` signature is *refreshed* (footprint),
    never duplicated; otherwise the new state is appended under a unique id. The
    trigger process list is unioned. Mirrors ``session_analysis``'s additive
    contract so a discovery can never wipe the curated profile.
    """
    import copy
    prof = copy.deepcopy(existing)
    prof.setdefault("states", [])
    prof.setdefault("footprints", {})
    nstate = dict(new["states"][0])
    sig = _detect_sig(nstate)
    same = next((s for s in prof["states"] if _detect_sig(s) == sig), None)
    added, refreshed = [], []
    if same:
        same["controls"] = {**(same.get("controls") or {}), **(nstate.get("controls") or {})}
        same["text"] = nstate.get("text") or same.get("text", "")
        prof["footprints"][same["id"]] = new["footprints"]["main"]
        refreshed.append(same["id"])
    else:
        sid = "main" if not any(s.get("id") == "main" for s in prof["states"]) else _unique_state_id(prof)
        nstate["id"] = sid
        prof["states"].append(nstate)
        prof["footprints"][sid] = new["footprints"]["main"]
        added.append(sid)
    trig = prof.get("trigger") or {}
    procs = sorted(set(trig.get("process") or []) | set((new.get("trigger") or {}).get("process") or []))
    if procs:
        trig["process"] = procs
    prof["trigger"] = trig
    prof["version"] = int(existing.get("version", 1) or 1) + 1
    prof["updated"] = date.today().isoformat()
    diff = {"mode": "merge", "states_added": added, "states_refreshed": refreshed,
            "before": len(existing.get("states") or []), "after": len(prof["states"])}
    return prof, diff


async def discover_app(
    hwnd: Optional[int] = None,
    name: str = "",
    save: bool = True,
    include_ocr: bool = True,
    max_controls: int = 40,
    write_guide: bool = True,
    merge: bool = True,
) -> dict:
    """Capture a window and build/save a deterministic profile for it.

    ``save=True`` **merges** by default: a state whose ``detect`` matches an
    existing one is refreshed, otherwise it is added — an existing curated
    profile is never replaced by a single discovered state. Pass ``merge=False``
    to replace (a ``warning`` is then included). The derived profile is always
    returned under ``profile``, even in dry-run.
    """
    from lib import window_state, winapi

    hwnd = int(hwnd) if hwnd is not None else (winapi.get_foreground_window() or 0)
    if not hwnd:
        return {"ok": False, "error": "no window (foreground unknown)"}
    if not name:
        res = learning_db.resolve_guide(hwnd=hwnd, url=window_state.active_url())
        name = res.get("key") or "unnamed"

    include = {"uia", "menu", "focus"}
    if include_ocr:
        include.add("text")
    state = await window_state.capture_state(
        hwnd=hwnd, include=include,
        max_controls=max(200, max_controls * 4), max_text=max(120, max_controls * 3),
    )
    if not state.window:
        return {"ok": False, "error": f"cannot read window {hwnd}"}

    profile = build_profile(state, name, include_ocr=include_ocr, max_controls=max_controls)
    existing = profiles.load(profile["key"])
    result = {
        "ok": True, "key": profile["key"], "process": (state.window or {}).get("process"),
        "states": len(profile["states"]),
        "controls": len(profile["states"][0]["controls"]),
        "footprint_keys": len(profile["footprints"]["main"]["keys"]),
        "summary": profile["initial_text"],
        "ocr_lines": len(state.texts),
        "problems": profiles.validate(profile),
        "existing_states": len(existing.get("states") or []) if existing else 0,
        "profile": profile,
    }
    if not include_ocr:
        result["note"] = "OCR was not captured (include_ocr=false): detect rules may be weaker."
    elif state.texts == []:
        result["note"] = ("0 OCR lines — the window may be occluded or not foreground; "
                          "state detection may be degenerate.")
    if save:
        if existing and merge:
            merged, diff = _merge_discovered(existing, profile)
            path = profiles.save(profile["key"], merged)
            result["mode"] = "merge"
            result["profile_merge"] = diff
        else:
            if existing:
                result["warning"] = (f"REPLACING an existing profile with "
                                     f"{len(existing.get('states') or [])} state(s) by 1 — "
                                     "use merge=True to keep them")
            path = profiles.save(profile["key"], profile)
            result["mode"] = "replace"
        result["profile_path"] = str(path)
        if write_guide and not learning_db.read_guide(profile["key"]):
            gpath = learning_db.write_guide(profile["key"], guide_markdown(profile))
            result["guide_path"] = str(gpath)
    logger.info(f"discover_app: {profile['key']} controls={result['controls']} "
                f"footprint_keys={result['footprint_keys']} ocr={result['ocr_lines']}")
    return result
