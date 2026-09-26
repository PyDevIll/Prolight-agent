"""Deterministic state router for ProLight-agent (Phase 2).

Given a profile (``lib.profiles``) and a current ``WindowState``
(``lib.window_state``), the router decides — with no LLM call — which known
state the app is in, resolves the profile's logical control names to the
runtime element ids of *this* snapshot, and produces a short "volatile" text
that the agent injects into its context (``PromptPlan.volatile``).

This is the deterministic half of the hybrid model: the LLM still decides what
to do, but *what is on screen*, *which fragment applies* and *which id a named
control maps to* are computed here.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger


def _norm(s) -> str:
    return " ".join((s or "").split()).strip().lower()


def _blob(values) -> str:
    return " ".join(_norm(v) for v in values if v)


def _control_matches(d: dict, spec: dict) -> bool:
    """Does a control dict match a ``{control_type,name,automation_id}`` spec?

    Note ``Element.to_dict()`` emits the control type under ``type`` (not
    ``control_type``), so accept either. A spec with no fields matches nothing
    (otherwise an OCR-only spec would match every control).
    """
    checks = []
    if spec.get("name"):
        checks.append(_norm(spec["name"]) in _norm(d.get("name", "")))
    ct = spec.get("control_type") or spec.get("type")
    if ct:
        checks.append(_norm(ct) in _norm(d.get("control_type") or d.get("type") or ""))
    if spec.get("automation_id"):
        checks.append(_norm(spec["automation_id"]) in _norm(d.get("automation_id", "")))
    return all(checks) if checks else False


# ── state detection ───────────────────────────────────────────────────────
def _state_score(detect: dict, state) -> Optional[int]:
    """Return a match score (higher = more specific) or None when it fails."""
    if not detect:
        return None
    score = 0
    ocr = _blob([t.name for t in state.texts])
    uia = [c.to_dict() for c in state.controls]
    menu = _blob([m.name for m in state.menu])

    for needle in detect.get("ocr_all") or []:
        if _norm(needle) not in ocr:
            return None
        score += 2
    for needle in detect.get("ocr_any") or []:
        if _norm(needle) in ocr:
            score += 1
            break
    else:
        if detect.get("ocr_any"):
            return None

    uia_any = detect.get("uia_any") or []
    if uia_any:
        if not any(_control_matches(c, spec) for c in uia for spec in uia_any):
            return None
        score += 2

    for needle in detect.get("menu_all") or []:
        if _norm(needle) not in menu:
            return None
        score += 1

    min_controls = detect.get("min_controls")
    if min_controls is not None and len(state.controls) < int(min_controls):
        return None
    return score


def detect_state(profile: dict, state) -> Optional[dict]:
    """Return the best-matching state dict, or None."""
    best, best_score = None, -1
    for st in profile.get("states") or []:
        sc = _state_score(st.get("detect") or {}, state)
        if sc is not None and sc > best_score:
            best, best_score = st, sc
    return best


# ── named-control resolution ──────────────────────────────────────────────
def _find_control(state, spec: dict):
    """Return the best matching control Element, or the matching text Element."""
    if any(spec.get(k) for k in ("name", "control_type", "automation_id")):
        for c in state.controls:
            if _control_matches(c.to_dict(), spec):
                return c
    want = spec.get("ocr")
    if want:
        for t in state.texts:
            if _norm(want) in _norm(t.name):
                return t
    return None


def resolve_controls(state_def: dict, state) -> dict:
    """Map ``logical name -> {id, kind, key, rect, name, value}`` for this snapshot."""
    out = {}
    for name, spec in (state_def.get("controls") or {}).items():
        spec = spec.get("match", spec) if isinstance(spec, dict) else {}
        el = _find_control(state, spec or {})
        if el is not None:
            out[name] = {
                "id": el.id, "kind": el.kind, "key": el.stable_key(),
                "rect": el.rect, "name": el.name, "value": el.value,
                "center": el.center(),
            }
        elif spec.get("click"):
            out[name] = {"id": None, "kind": "coords", "key": None,
                         "rect": None, "name": name, "value": "",
                         "center": list(spec["click"])}
    return out


def resolve_named(profile: dict, state, name: str) -> Optional[dict]:
    """Resolve one logical control name against the profile's states."""
    for st in profile.get("states") or []:
        ctrls = st.get("controls") or {}
        if name in ctrls:
            spec = ctrls[name]
            spec = spec.get("match", spec) if isinstance(spec, dict) else {}
            el = _find_control(state, spec or {})
            if el is not None:
                return {"id": el.id, "kind": el.kind, "key": el.stable_key(),
                        "rect": el.rect, "center": el.center(), "name": el.name}
            if isinstance(ctrls[name], dict) and ctrls[name].get("click"):
                return {"id": None, "kind": "coords", "center": list(ctrls[name]["click"]),
                        "name": name}
    return None


def _verify_footprint(profile: dict, state_id: Optional[str], state) -> dict:
    fps = (profile.get("footprints") or {}).get(state_id or "")
    stored = (fps or {}).get("keys") or []
    if not stored:
        return {"footprint_ok": None, "missing_keys": []}
    current = set(state.stable_keys())
    missing = [k for k in stored if k not in current]
    ok = len(missing) <= max(1, len(stored) // 2)
    return {"footprint_ok": ok, "missing_keys": missing}


# ── route + injection text ────────────────────────────────────────────────
def route(profile: dict, state, key: str = "") -> dict:
    state_def = detect_state(profile, state)
    state_id = (state_def or {}).get("id")
    controls = resolve_controls(state_def, state) if state_def else {}
    fp = _verify_footprint(profile, state_id, state)

    groups = set()
    if state_def:
        groups |= {"uia", "mouse"}   # a known state implies acting by id
    return {
        "profile_key": key or profile.get("key", ""),
        "display": profile.get("display", key),
        "state_id": state_id,
        "text": (state_def or {}).get("text") or profile.get("initial_text", ""),
        "controls": controls,
        "tool_groups": groups,
        **fp,
    }


def volatile_text(result: dict, profile: Optional[dict] = None) -> str:
    """Build the short fragment injected for the current route."""
    if not result.get("state_id") and not result.get("text"):
        return ""
    head = f"## CURRENT APP: {result.get('display') or result.get('profile_key')}"
    if result.get("state_id"):
        head += f" — state: {result['state_id']}"
    lines = [head]
    if result.get("text"):
        lines.append(str(result["text"]).strip())
    controls = result.get("controls") or {}
    if controls:
        lines.append("\n**Named controls (ids are valid for the latest snapshot):**")
        for name, c in controls.items():
            if c.get("id"):
                rect = c.get("rect")
                lines.append(f"- `{name}` → id `{c['id']}` ({c.get('kind')} '{c.get('name','')}')"
                             + (f" rect {rect}" if rect else ""))
            elif c.get("center"):
                lines.append(f"- `{name}` → click at {c['center']} (no live element matched)")
    if result.get("footprint_ok") is False:
        lines.append("\n⚠ profile footprint mismatch (missing: "
                     + ", ".join(result.get("missing_keys", [])[:5]) + ") — re-snapshot before acting.")
    if controls:
        lines.append("\nAct on these ids directly (`win_click_control(id=...)` / `mouse_click(id=...)`); "
                     "call `win_changes()` first if the screen may have changed.")
    logger.debug(f"router: profile={result.get('profile_key')} state={result.get('state_id')} "
                 f"controls={list(controls)}")
    return "\n".join(lines)
