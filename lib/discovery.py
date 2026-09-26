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


def controls_from_state(state, max_controls: int = 40, include_ocr: bool = True) -> dict:
    """Named logical controls → match specs, deterministically."""
    used: set = set()
    controls: dict = {}
    for c in state.controls[:max_controls]:
        aid = c.automation_id or ""
        # Prefer a human-readable logical name; a purely numeric automation_id
        # (e.g. Notepad's Edit = "15") is a poor name, so fall back to the label.
        raw = aid if (aid and not aid.isdigit()) else (c.name or c.control_type or aid or "control")
        base = slugify(raw)
        name = _unique(base, used)
        controls[name] = {"match": _match_spec(c)}
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


async def discover_app(
    hwnd: Optional[int] = None,
    name: str = "",
    save: bool = True,
    include_ocr: bool = True,
    max_controls: int = 40,
    write_guide: bool = True,
) -> dict:
    """Capture a window and build/save a deterministic profile for it."""
    from lib import window_state, winapi

    hwnd = int(hwnd) if hwnd is not None else (winapi.get_foreground_window() or 0)
    if not hwnd:
        return {"ok": False, "error": "no window (foreground unknown)"}
    if not name:
        res = learning_db.resolve_guide(hwnd=hwnd)
        name = res.get("key") or "unnamed"

    state = await window_state.capture_state(
        hwnd=hwnd, include={"uia", "menu", "focus"}, max_controls=max(200, max_controls * 4)
    )
    if not state.window:
        return {"ok": False, "error": f"cannot read window {hwnd}"}

    profile = build_profile(state, name, include_ocr=include_ocr, max_controls=max_controls)
    result = {
        "ok": True, "key": profile["key"], "process": (state.window or {}).get("process"),
        "states": len(profile["states"]),
        "controls": len(profile["states"][0]["controls"]),
        "footprint_keys": len(profile["footprints"]["main"]["keys"]),
        "summary": profile["initial_text"],
        "problems": profiles.validate(profile),
    }
    if save:
        path = profiles.save(profile["key"], profile)
        result["profile_path"] = str(path)
        if write_guide and not learning_db.read_guide(profile["key"]):
            gpath = learning_db.write_guide(profile["key"], guide_markdown(profile))
            result["guide_path"] = str(gpath)
        result["profile"] = profile
    logger.info(f"discover_app: {profile['key']} controls={result['controls']} "
                f"footprint_keys={result['footprint_keys']}")
    return result
