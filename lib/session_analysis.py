"""Deterministic learning-session synthesis (L1-L3).

Turns a recorded learning session (``data/sessions/<label>_<ts>/``) into:

  * **per-app profile extensions** (``interaction_guides/<key>.profile.json``) —
    new states, refreshed footprints and merged named controls, deterministically;
  * a **cross-app workflow draft** (``workflows/<task>.md``) whole steps reference
    the app (guide key), the state and a named control — not pixels.

No LLM call: ``discover``/``router`` primitives are reused. The agent/user then
confirms and refines the prose. ``capsules`` in ``states.jsonl`` are rebuilt into
``WindowState`` objects so profile state-matching uses the same code as runtime.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from loguru import logger

from lib import discovery, learning_db, profiles, router
from lib import window_state as ws

# layout-change thresholds (fraction of fingerprint difference / moved controls)
SEG_KEY_DIFF = 0.25
SEG_MOVED_FRAC = 0.4
MOVE_TOL = 0.12
DEDUP_SIM = 0.7


# ── session I/O ───────────────────────────────────────────────────────────
def read_session(session_dir) -> tuple[list, list]:
    """Return ``(state_records, app_timeline)`` from a session directory."""
    d = Path(session_dir)
    recs: list = []
    sp = d / "states.jsonl"
    if sp.exists():
        for line in sp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    timeline: list = []
    tp = d / "app_timeline.json"
    if tp.exists():
        try:
            timeline = json.loads(tp.read_text(encoding="utf-8"))
        except Exception:
            timeline = []
    return recs, timeline


def rebuild_state(rec: dict) -> ws.WindowState:
    """Reconstruct a ``WindowState`` from a stored capture record."""
    def el(c: dict, kind: str) -> ws.Element:
        control = kind == "control"
        return ws.Element(
            id=c.get("id") or "", kind=kind, name=c.get("name", ""), rect=c.get("rect"),
            control_type=(c.get("type", "") if control else ""),
            automation_id=(c.get("automation_id", "") if control else ""),
            value=(c.get("value", "") if control else ""),
            enabled=(c.get("enabled") if control else None),
        )

    return ws.WindowState(
        snapshot_id=rec.get("capture_id", ""), created=0.0,
        hwnd=(rec.get("window") or {}).get("hwnd"), mode="window",
        window=rec.get("window") or {},
        controls=[el(c, "control") for c in rec.get("controls", [])],
        texts=[el(t, "text") for t in rec.get("texts", [])],
        menu=[el(m, "menu") for m in rec.get("menu", [])],
    )


# ── segmentation ──────────────────────────────────────────────────────────
def layout_diff(a: dict, b: dict) -> tuple:
    """``(key-set difference ratio, fraction of common controls that moved)``."""
    ka = set(a.get("stable_keys") or [])
    kb = set(b.get("stable_keys") or [])
    union = ka | kb
    key_diff = len(ka ^ kb) / len(union) if union else 0.0
    ra = (a.get("footprint") or {}).get("rel_rects") or {}
    rb = (b.get("footprint") or {}).get("rel_rects") or {}
    common = moved = 0
    for k in ka & kb:
        if k in ra and k in rb:
            common += 1
            ca = ((ra[k][0] + ra[k][2]) / 2, (ra[k][1] + ra[k][3]) / 2)
            cb = ((rb[k][0] + rb[k][2]) / 2, (rb[k][1] + rb[k][3]) / 2)
            if abs(ca[0] - cb[0]) > MOVE_TOL or abs(ca[1] - cb[1]) > MOVE_TOL:
                moved += 1
    return key_diff, (moved / common if common else 0.0)


def layout_changed(a: dict, b: dict) -> bool:
    kd, mf = layout_diff(a, b)
    return kd > SEG_KEY_DIFF or mf > SEG_MOVED_FRAC


def _segment(recs: list) -> list:
    """Split a capture sequence (same guide key) into layout states."""
    states: list = []
    for r in recs:
        if not states or layout_changed(states[-1][-1], r):
            states.append([r])
        else:
            states[-1].append(r)
    return states


def _spec_from_clicked(cl: dict) -> dict:
    aid = (cl.get("automation_id") or "").strip()
    if aid:
        return {"automation_id": aid}
    nm = (cl.get("name") or "").strip()
    if nm:
        return {"control_type": cl.get("control_type", ""), "name": nm}
    return {}


def _derive_state(segment: list, include_ocr: bool = True) -> dict:
    """Turn one layout segment into a profile-state dict (deterministic)."""
    rep = max(segment, key=lambda r: (len(r.get("controls") or []), len(r.get("texts") or [])))
    state = rebuild_state(rep)
    controls = discovery.controls_from_state(state, include_ocr=include_ocr)
    # Ensure every actually-clicked control is a named control (marked verified),
    # so a workflow step always resolves — even if the click resolved to an
    # element outside the depth-limited snapshot.
    used = set(controls)
    for r in segment:
        spec = _spec_from_clicked(r.get("clicked") or {})
        if not spec:
            continue
        existing = next((v for v in controls.values()
                         if _normspec({"match": spec}) == _normspec(v)), None)
        if existing is not None:
            existing["verified"] = True
            continue
        cl = r.get("clicked") or {}
        base = discovery.slugify(cl.get("name") or cl.get("automation_id") or "control")
        name, i = base, 2
        while name in used:
            name, i = f"{base}_{i}", i + 1
        used.add(name)
        controls[name] = {"match": spec, "verified": True}
    detect = discovery.detect_from_state(state, controls)
    menu_names = [m.get("name", "") for m in rep.get("menu", []) if m.get("name")]
    if menu_names:
        detect["menu_all"] = menu_names[:6]
    text = discovery.summary_text(state, discovery.classify_regions(state))
    return {
        "_state": state, "_rep": rep, "detect": detect, "controls": controls, "text": text,
        "footprint": state.footprint(with_rects=True), "capture_id": rep.get("capture_id"),
        "captures": [r.get("capture_id") for r in segment],
    }


def _footprint_similar(a: dict, b: dict) -> float:
    """Similarity 0..1 between two footprints (key Jaccard gated by placement).

    Returns 0 when the common controls drifted too far — a *moved* layout is a
    different state even if the same elements are present.
    """
    ka, kb = set(a.get("keys") or []), set(b.get("keys") or [])
    union = ka | kb
    if not union:
        return 1.0
    j = len(ka & kb) / len(union)
    if j < 0.6:
        return 0.0
    ra = a.get("rel_rects") or {}
    rb = b.get("rel_rects") or {}
    common = moved = 0
    for k in ka & kb:
        if k in ra and k in rb:
            common += 1
            ca = ((ra[k][0] + ra[k][2]) / 2, (ra[k][1] + ra[k][3]) / 2)
            cb = ((rb[k][0] + rb[k][2]) / 2, (rb[k][1] + rb[k][3]) / 2)
            if abs(ca[0] - cb[0]) > MOVE_TOL or abs(ca[1] - cb[1]) > MOVE_TOL:
                moved += 1
    if common and moved / common >= 0.5:
        return 0.0
    return j


def _dedup(states: list) -> list:
    """Merge states whose footprints are near-identical (recurring layouts)."""
    uniq: list = []
    for d in states:
        match = None
        for u in uniq:
            if _footprint_similar(u["footprint"], d["footprint"]) > DEDUP_SIM:
                match = u
                break
        if match:
            match["captures"] += d["captures"]
            match["footprint"]["keys"] = sorted(set(match["footprint"]["keys"]) | set(d["footprint"]["keys"]))
            match["footprint"]["rel_rects"] = {**(match["footprint"].get("rel_rects") or {}),
                                               **(d["footprint"].get("rel_rects") or {})}
            for name, spec in d["controls"].items():
                match["controls"].setdefault(name, spec)
        else:
            uniq.append(d)
    return uniq


# ── profile merge ─────────────────────────────────────────────────────────
def _normspec(spec) -> tuple:
    m = spec.get("match", spec) if isinstance(spec, dict) else {}
    return (m.get("automation_id", ""), m.get("control_type", ""),
            (m.get("name", "") or "").lower(), m.get("ocr", ""))


def _unique_sid(prof: dict) -> str:
    ids = {s.get("id") for s in prof.get("states", [])}
    if not ids:
        return "main"
    i = len(ids) + 1
    while f"state_{i}" in ids:
        i += 1
    return f"state_{i}"


def merge_profile(key: str, derived: list, window: dict) -> dict:
    """Additively merge observed states into ``<key>.profile.json``."""
    existing = profiles.load(key)
    fresh = existing is None
    prof = existing or profiles.template(key, display=(window or {}).get("title") or key)
    if fresh:
        prof["states"] = []  # drop the template placeholder; learned states become the profile
    prof.setdefault("trigger", {})
    prof.setdefault("states", [])
    prof.setdefault("footprints", {})
    proc = (window or {}).get("process", "") or ""
    proc = proc[:-4] if proc.lower().endswith(".exe") else proc
    if proc and proc not in prof["trigger"].setdefault("process", []):
        prof["trigger"]["process"].append(proc)

    added_states = refreshed = added_controls = 0
    for d in derived:
        wstate = d["_state"]
        match = None
        for ex in prof["states"]:
            ex_fp = prof["footprints"].get(ex.get("id"), {})
            if router._state_score(ex.get("detect") or {}, wstate) is not None \
                    and _footprint_similar(ex_fp, d["footprint"]) >= 0.6:
                match = ex
                break
        if match is None:
            sid = _unique_sid(prof)
            prof["states"].append({"id": sid, "detect": d["detect"], "text": d["text"],
                                   "controls": d["controls"]})
            prof["footprints"][sid] = d["footprint"]
            added_states += 1
        else:
            sid = match["id"]
            fp = prof["footprints"].setdefault(sid, {})
            fp["keys"] = sorted(set(fp.get("keys", [])) | set(d["footprint"]["keys"]))
            fp["rel_rects"] = {**(fp.get("rel_rects") or {}), **(d["footprint"].get("rel_rects") or {})}
            fp["controls"] = d["footprint"].get("controls", len(wstate.controls))
            fp["texts"] = d["footprint"].get("texts", len(wstate.texts))
            fp["menu"] = d["footprint"].get("menu", len(wstate.menu))
            ctrls = match.setdefault("controls", {})
            for name, spec in d["controls"].items():
                if name not in ctrls and not any(_normspec(spec) == _normspec(v) for v in ctrls.values()):
                    ctrls[name] = spec
                    added_controls += 1
            if not match.get("text") and d.get("text"):
                match["text"] = d["text"]
            refreshed += 1

    prof["updated"] = date.today().isoformat()
    path = profiles.save(key, prof)
    guide_path = None
    try:
        if not learning_db.read_guide(key):
            guide_path = str(learning_db.write_guide(key, discovery.guide_markdown(prof)))
    except Exception as e:
        logger.warning(f"session_analysis: could not seed guide for {key}: {e}")
    return {"key": key, "path": str(path), "guide_path": guide_path,
            "states_added": added_states, "states_refreshed": refreshed,
            "controls_added": added_controls, "states_total": len(prof["states"])}


# ── workflow draft ────────────────────────────────────────────────────────
def _state_id_for(states: list, capture_id: str) -> str:
    for i, st in enumerate(states, 1):
        if capture_id in (st.get("captures") or []):
            return st.get("id") or f"state_{i}"
    return "?"


def _nk(s) -> str:
    return " ".join((s or "").split()).strip().lower()


def _spec_stable_key(m: dict) -> str:
    aid = m.get("automation_id") or ""
    if aid:
        return f"control:{aid}"
    ct = (m.get("control_type") or "").lower()
    nm = _nk(m.get("name"))
    return f"control:{ct}:{nm}" if (ct or nm) else ""


def _control_name(states: list, capture_id: str, clicked: dict) -> str:
    """Map a recorded click to a logical control name (stable_key first)."""
    if not clicked:
        return ""
    ck = clicked.get("stable_key") or ""
    caid = (clicked.get("automation_id") or "").lower()
    cname = _nk(clicked.get("name"))
    cct = (clicked.get("control_type") or "").lower()
    for st in states:
        if capture_id not in (st.get("captures") or []):
            continue
        for name, spec in (st.get("controls") or {}).items():
            m = spec.get("match", spec) if isinstance(spec, dict) else {}
            if m.get("ocr"):
                continue
            if ck and _spec_stable_key(m) == ck:
                return name
            if caid and (m.get("automation_id") or "").lower() == caid:
                return name
            if cname and _nk(m.get("name")) == cname and (m.get("control_type") or "").lower() == cct:
                return name
        break
    return clicked.get("name", "")


def build_workflow(label: str, timeline: list, derived_by_key: dict, records_by_id: dict) -> str:
    lines = [f"# Workflow: {label or 'learned task'}", "", "## Goal",
             "- (fill in: the outcome this session accomplished.)", "", "## Apps & order"]
    for i, seg in enumerate(timeline, 1):
        lines.append(f"{i}. `{seg.get('guide_key')}` — {seg.get('title') or ''} "
                     f"({seg.get('process') or ''})")
    lines += ["", "## Steps"]
    for seg in timeline:
        key = seg.get("guide_key")
        states = derived_by_key.get(key, [])
        lines.append(f"### {key}")
        for cid in (seg.get("captures") or []):
            rec = records_by_id.get(cid)
            if not rec:
                continue
            sid = _state_id_for(states, cid)
            action = rec.get("action") or "action"
            clicked = rec.get("clicked") or {}
            cname = _control_name(states, cid, clicked)
            if action == "start":
                lines.append(f"- [state `{sid}`] (initial layout)")
            elif action == "click":
                label = cname or clicked.get("name") or "(unknown control — use OCR/vision)"
                lines.append(f"- [state `{sid}`] click `{label}`")
            elif action == "key":
                t = (rec.get("typed") or "").strip()
                if t:
                    t = t.replace("\n", "\\n")
                    if len(t) > 90:
                        t = t[:90] + "…"
                    lines.append(f"- [state `{sid}`] type `{t}`")
                else:
                    lines.append(f"- [state `{sid}`] type / key input")
            elif action == "focus":
                lines.append(f"- [state `{sid}`] switch to this app")
            else:
                lines.append(f"- [state `{sid}`] {action}")
    lines += ["", "## Decision points", "- (fill in as needed.)", "",
              "## Follow-ups", "- (any deferred check.)", ""]
    return "\n".join(lines)


# ── entry point ───────────────────────────────────────────────────────────
def analyze_session(session_dir, label: str = "", save: bool = True,
                    include_ocr: bool = True) -> dict:
    """Deterministically analyze a recorded session.

    Merges observed states into the per-app profiles when ``save`` and returns
    a cross-app workflow **draft** (not saved — the agent/user confirms it).
    """
    recs, timeline = read_session(session_dir)
    if not recs:
        return {"ok": False, "error": f"no states recorded in {session_dir}"}
    records_by_id = {r.get("capture_id"): r for r in recs if r.get("capture_id")}

    by_key: dict = {}
    for r in recs:
        by_key.setdefault(r.get("guide_key") or "unnamed", []).append(r)

    derived_by_key: dict = {}
    for key, rs in by_key.items():
        states = []
        for segment in _segment(rs):
            d = _derive_state(segment, include_ocr=include_ocr)
            states.append(d)
        derived_by_key[key] = _dedup(states)

    merges = []
    if save:
        for key, states in derived_by_key.items():
            window = (states[0]["_rep"].get("window") if states else {}) or {}
            merges.append(merge_profile(key, states, window))

    draft = build_workflow(label, timeline, derived_by_key, records_by_id)
    # Write the full draft to a file so it is never truncated in a tool response.
    workflow_path = None
    try:
        learning_db.ensure_dirs()
        slug = learning_db.normalize_key(label or "learned task").replace(" ", "_") or "learned_task"
        wf = learning_db.WORKFLOWS_DIR / f"_draft_{slug}.md"
        wf.write_text(draft, encoding="utf-8")
        workflow_path = str(wf)
    except Exception as e:
        logger.warning(f"session_analysis: could not write workflow draft: {e}")
    logger.info(f"session_analysis: keys={list(derived_by_key)} "
                f"states={ {k: len(v) for k, v in derived_by_key.items()} }")
    return {
        "ok": True,
        "session_dir": str(session_dir),
        "keys": list(derived_by_key.keys()),
        "states": {k: len(v) for k, v in derived_by_key.items()},
        "profile_merges": merges,
        "workflow_name": label or "learned task",
        "workflow_path": workflow_path,
        "workflow_preview": draft[:1200],
        "workflow_draft": draft[:2000],
    }
