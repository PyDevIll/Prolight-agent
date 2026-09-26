"""Deterministic learning-session synthesis (L1-L3).

Turns a recorded learning session (``data/sessions/<label>_<ts>/``) into:

  * **per-app profile extensions** (``interaction_guides/<key>.profile.json``) —
    new states, refreshed footprints and merged named controls, deterministically;
  * a **cross-app workflow draft** (``workflows/<task>.md``) whose steps reference
    the app (guide key), the state and a named control — not pixels;
  * a machine-readable **workflow program** (``workflows/<task>.workflow.json``)
    that the deterministic runner (``lib.workflow_runner``) can execute step by
    step, with no LLM call for the resolved steps.

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
    """``(key-set diff, fraction of common controls that moved, name changes)``."""
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
    sa = {s[0]: s for s in ((a.get("footprint") or {}).get("signature") or [])}
    sb = {s[0]: s for s in ((b.get("footprint") or {}).get("signature") or [])}
    name_changed = sum(1 for k in set(sa) & set(sb)
                       if (sa[k][2] or "") != (sb[k][2] or ""))
    return key_diff, (moved / common if common else 0.0), name_changed


def layout_changed(a: dict, b: dict) -> bool:
    kd, mf, nc = layout_diff(a, b)
    return kd > SEG_KEY_DIFF or mf > SEG_MOVED_FRAC or nc > 0


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
    signature = []
    for cd in rep.get("controls", []):
        ct = (cd.get("type") or "").lower()
        if ct in ws._ACTIONABLE:
            el = ws.Element(id="", kind="control", name=cd.get("name", ""),
                            control_type=cd.get("type", ""), automation_id=cd.get("automation_id", ""))
            signature.append([el.stable_key(), cd.get("type", ""), cd.get("name", "")])
    signature.sort(key=lambda x: x[0])
    return {
        "_state": state, "_rep": rep, "detect": detect, "controls": controls, "text": text,
        "footprint": state.footprint(with_rects=True), "capture_id": rep.get("capture_id"),
        "captures": [r.get("capture_id") for r in segment], "_sig": signature,
    }


def _sharpen_variants(states: list) -> None:
    """Make same-layout/different-label states distinguishable.

    If two states share a stable key but the actionable control's label differs
    (a toggle like *Подключиться* → *Подключено*), require each state's own label
    via ``uia_all`` so the router cannot match the wrong variant.
    """
    for i in range(len(states)):
        for j in range(i + 1, len(states)):
            a, b = states[i], states[j]
            sa = {s[0]: s for s in (a.get("_sig") or [])}
            sb = {s[0]: s for s in (b.get("_sig") or [])}
            for k in set(sa) & set(sb):
                na, nb = (sa[k][2] or ""), (sb[k][2] or "")
                if not na or not nb or na == nb:
                    continue
                for st, s in ((a, sa[k]), (b, sb[k])):
                    rule = {"control_type": s[1], "name": s[2]}
                    ua = st["detect"].setdefault("uia_all", [])
                    if rule not in ua:
                        ua.append(rule)


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
    # A changed actionable label (e.g. a toggle) means a different state even
    # when every stable key and rect is identical.
    sa = {s[0]: s for s in (a.get("signature") or [])}
    sb = {s[0]: s for s in (b.get("signature") or [])}
    if any((sa[k][2] or "") != (sb[k][2] or "") for k in set(sa) & set(sb)):
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
    capture_map: dict = {}
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
        for cid in d.get("captures") or []:
            capture_map[cid] = sid

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
            "controls_added": added_controls, "states_total": len(prof["states"]),
            "capture_states": capture_map}


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


# ── workflow program (executable sidecar) ─────────────────────────────────
def build_workflow_program(label: str, timeline: list, derived_by_key: dict,
                           records_by_id: dict, session_dir: str = "",
                           include_ocr: bool = True, capture_states: dict = None) -> dict:
    """Deterministically derive an **executable** workflow program from a session.

    Every step references an ``app`` (guide key), an optional guard ``state`` and
    (for actions) a named ``control`` — never pixels:

      * a ``focus`` step is emitted at each app switch;
      * ``start`` captures become ``assert_state`` guards;
      * a recorded click/keypress becomes a ``click``/``type``/``key`` step whose
        guard ``state`` is the app's *previous* state (an action is performed
        while in the state before it);
      * when the action changes the app's state, a ``wait_state`` step verifies
        the effect (the captured post-action state).

    An action whose control could not be named carries ``unresolved: true`` and
    the recorded ``expected`` label, so the runner hands that step to the LLM.
    """
    slug = (learning_db.normalize_key(label or "learned task") or "learned_task")
    apps: list = []
    steps: list = []
    prev_app = None
    prev_state: dict = {}

    def add(**kw) -> None:
        kw.setdefault("app", "")
        steps.append(kw)

    for seg in timeline:
        app = seg.get("guide_key") or "unnamed"
        if app not in apps:
            apps.append(app)
        if app != prev_app:
            add(app=app, action="focus")
            prev_app = app
        states = derived_by_key.get(app, [])
        for cid in (seg.get("captures") or []):
            rec = records_by_id.get(cid)
            if not rec:
                continue
            sid = (capture_states or {}).get(cid) or _state_id_for(states, cid)
            action = rec.get("action") or "action"
            before = prev_state.get(app)
            if action == "start":
                add(app=app, action="assert_state", state=sid)
            elif action == "click":
                cname = _control_name(states, cid, rec.get("clicked") or {})
                step = {"app": app, "action": "click"}
                if before:
                    step["state"] = before
                if cname:
                    step["control"] = cname
                else:
                    step["control"] = ""
                    step["unresolved"] = True
                    step["expected"] = (rec.get("clicked") or {}).get("name", "")
                add(**step)
            elif action == "key":
                typed = (rec.get("typed") or "").strip()
                step = {"app": app, "action": "type" if typed else "key"}
                if before:
                    step["state"] = before
                if typed:
                    step["text"] = typed
                else:
                    step["unresolved"] = True
                add(**step)
            elif action == "focus":
                add(app=app, action="focus")
            else:
                step = {"app": app, "action": action}
                if before:
                    step["state"] = before
                add(**step)
            if action != "start" and sid and sid != before:
                add(app=app, action="wait_state", state=sid, timeout_s=8)
            prev_state[app] = sid

    for i, st in enumerate(steps, 1):
        st["id"] = f"s{i}"

    windows: dict = {}
    for seg in timeline:
        windows.setdefault(seg.get("guide_key") or "unnamed", {
            "title": seg.get("title") or "", "process": seg.get("process") or ""})
    return {
        "key": slug,
        "title": label or "learned task",
        "goal": "",
        "source_session": str(session_dir or ""),
        "apps": apps,
        "windows": windows,
        "preconditions": [],
        "steps": steps,
    }


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
        states = _dedup(states)
        _sharpen_variants(states)
        derived_by_key[key] = states

    merges = []
    capture_states: dict = {}
    if save:
        for key, states in derived_by_key.items():
            window = (states[0]["_rep"].get("window") if states else {}) or {}
            m = merge_profile(key, states, window)
            merges.append(m)
            capture_states.update(m.get("capture_states") or {})

    draft = build_workflow(label, timeline, derived_by_key, records_by_id)
    program = build_workflow_program(label, timeline, derived_by_key, records_by_id,
                                     session_dir=str(session_dir), include_ocr=include_ocr,
                                     capture_states=capture_states)
    # Write the full draft (prose + executable program) to files so neither is
    # ever truncated in a tool response.
    workflow_path = None
    program_path = None
    try:
        learning_db.ensure_dirs()
        slug = program["key"]
        wf = learning_db.WORKFLOWS_DIR / f"_draft_{slug}.md"
        wf.write_text(draft, encoding="utf-8")
        workflow_path = str(wf)
        wj = learning_db.WORKFLOWS_DIR / f"_draft_{slug}.workflow.json"
        wj.write_text(json.dumps(program, ensure_ascii=False, indent=2), encoding="utf-8")
        program_path = str(wj)
    except Exception as e:
        logger.warning(f"session_analysis: could not write workflow draft: {e}")
    logger.info(f"session_analysis: keys={list(derived_by_key)} "
                f"states={ {k: len(v) for k, v in derived_by_key.items()} } "
                f"steps={len(program['steps'])}")
    return {
        "ok": True,
        "session_dir": str(session_dir),
        "keys": list(derived_by_key.keys()),
        "states": {k: len(v) for k, v in derived_by_key.items()},
        "profile_merges": merges,
        "workflow_name": label or "learned task",
        "workflow_path": workflow_path,
        "program_path": program_path,
        "program_steps": len(program["steps"]),
        "workflow_program": program,
        "workflow_preview": draft[:1200],
        "workflow_draft": draft[:2000],
    }
