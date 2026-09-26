"""Deterministic workflow runner for ProLight-agent.

Executes a machine-readable workflow program
(``workflows/<task>.workflow.json``) step by step using only the deterministic
layers — profiles (``lib.profiles``), the state router (``lib.router``) and the
UIA/mouse/keyboard tools. **No LLM call is made**: a step either resolves
against a live window state and is performed, or it is returned as
``needs_llm`` together with the live state so the agent can fall back to
free-form perception/action and then resume.

A program is a list of steps::

    {"id": "s1", "app": "amneziavpn", "state": "main",
     "control": "подключиться", "action": "click"}

Actions: ``focus | click | double_click | right_click | hotkey | type | key |
wait_state | assert_state | launch``. Controls are referenced by *name* (never
pixels); the router maps them to the live element ids of the current snapshot.

Progress is persisted to ``data/workflow_run.json`` so a run can be resumed.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from pathlib import Path
from typing import Optional

from loguru import logger

from lib import learning_db, profiles, router, winapi
from lib import window_state as ws

ROOT = Path(__file__).resolve().parent.parent
RUN_FILE = ROOT / "data" / "workflow_run.json"

ACTIONS = {"focus", "click", "double_click", "right_click", "hotkey", "type",
           "key", "wait_state", "assert_state", "launch"}
CONTROL_ACTIONS = {"click", "double_click", "right_click"}
_STATE_ACTIONS = CONTROL_ACTIONS | {"hotkey", "assert_state", "wait_state"}


# ── program I/O ───────────────────────────────────────────────────────────
def program_path(key: str) -> Path:
    return learning_db.WORKFLOWS_DIR / f"{learning_db.normalize_key(key)}.workflow.json"


def list_programs() -> list[dict]:
    learning_db.ensure_dirs()
    out: list[dict] = []
    for p in sorted(learning_db.WORKFLOWS_DIR.glob("*.workflow.json")):
        out.append({
            "key": p.name[: -len(".workflow.json")],
            "path": str(p),
            "draft": p.name.startswith("_draft_"),
        })
    return out


def load_program(name: str) -> Optional[dict]:
    """Load a workflow program by key, file name or path (drafts included)."""
    if not name:
        return None
    learning_db.ensure_dirs()
    cands: list[Path] = []
    p = Path(name)
    if p.suffix == ".json" or p.exists():
        cands.append(p)
    key = learning_db.normalize_key(name)
    cands += [
        learning_db.WORKFLOWS_DIR / f"{key}.workflow.json",
        learning_db.WORKFLOWS_DIR / f"_draft_{key}.workflow.json",
    ]
    for c in cands:
        if c.exists():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"workflow_runner: cannot parse {c.name}: {e}")
                return None
    return None


def save_program(name: str, program) -> tuple[Path, list[str]]:
    if isinstance(program, str):
        program = json.loads(program)
    if not isinstance(program, dict):
        raise ValueError("program must be a JSON object")
    program.setdefault("key", learning_db.normalize_key(name))
    program.setdefault("steps", [])
    learning_db.ensure_dirs()
    p = program_path(name or program["key"])
    p.write_text(json.dumps(program, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"workflow_runner: wrote {p.name} ({len(program.get('steps') or [])} steps)")
    return p, validate_program(program)


def validate_program(program) -> list[str]:
    """Return a list of human-readable problems ([] = OK)."""
    if not isinstance(program, dict):
        return ["program must be a JSON object"]
    steps = program.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["steps must be a non-empty list"]
    problems: list[str] = []
    seen: set = set()
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            problems.append(f"step[{i}] must be an object")
            continue
        sid = s.get("id")
        if not sid:
            problems.append(f"step[{i}] missing 'id'")
        elif sid in seen:
            problems.append(f"duplicate step id {sid!r}")
        seen.add(sid)
        act = (s.get("action") or "").lower()
        if act not in ACTIONS:
            problems.append(f"step[{sid or i}] unknown action {act!r}")
        if act in CONTROL_ACTIONS and not s.get("control"):
            problems.append(f"step[{sid or i}] {act} has no control")
        if act in ("assert_state", "wait_state") and not s.get("state"):
            problems.append(f"step[{sid or i}] {act} has no state")
    return problems


# ── run state ─────────────────────────────────────────────────────────────
def load_run() -> dict:
    if RUN_FILE.exists():
        try:
            return json.loads(RUN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_run(run: dict) -> None:
    RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUN_FILE.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")


def reset() -> dict:
    if RUN_FILE.exists():
        RUN_FILE.unlink()
    return {"ok": True, "reset": True}


def status() -> dict:
    run = load_run()
    if not run or "key" not in run:
        return {"ok": False, "error": "no workflow run in progress"}
    program = load_program(run["key"]) or {}
    steps = program.get("steps") or []
    idx = int(run.get("index", 0))
    return {
        "ok": True,
        "key": run.get("key"),
        "index": idx,
        "total": len(steps),
        "done": idx >= len(steps),
        "current_step": steps[idx] if idx < len(steps) else None,
        "started": run.get("started"),
        "log_tail": (run.get("log") or [])[-5:],
    }


# ── helpers ───────────────────────────────────────────────────────────────
def _json(s):
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s)
    except Exception:
        return {"ok": False, "error": str(s)[:300], "raw": str(s)[:300]}


def find_app_window(program: dict, app: str) -> Optional[int]:
    """Find a live top-level window for an app (program hint → profile trigger)."""
    hint = (program.get("windows") or {}).get(app) or {}
    proc = (hint.get("process") or "").lower()
    if not proc:
        prof = profiles.load(app)
        procs = ((prof or {}).get("trigger") or {}).get("process") or []
        proc = (procs[0] if procs else "").lower()
    if proc.endswith(".exe"):
        proc = proc[:-4]
    fallback = None
    for w in winapi.list_windows(visible_only=True, titled_only=False):
        wp = (w.get("process") or "").lower()
        if wp.endswith(".exe"):
            wp = wp[:-4]
        if proc and proc not in wp:
            continue
        if w.get("foreground"):
            return w.get("hwnd")
        if fallback is None:
            fallback = w.get("hwnd")
    return fallback


async def _capture(hwnd: int) -> ws.WindowState:
    state = await ws.capture_state(hwnd=int(hwnd), include={"uia", "text", "menu"},
                                   max_controls=60, max_text=60)
    ws.store(state)
    return state


def _needs(step: dict, result: dict, hwnd: Optional[int] = None) -> dict:
    result.setdefault("step_id", step.get("id"))
    result.setdefault("app", step.get("app"))
    if hwnd:
        result.setdefault("hwnd", hwnd)
    return result


# ── step execution ────────────────────────────────────────────────────────
async def _exec(program: dict, step: dict) -> tuple[dict, bool]:
    """Execute one step. Returns ``(result, needs_llm)``."""
    app = step.get("app") or ""
    action = (step.get("action") or "").lower()

    if action == "launch":
        from builtin_tools import app_tools
        out = _json(await app_tools.app_launch(path=step.get("path", ""),
                                               args=step.get("args", ""),
                                               cwd=step.get("cwd", "")))
        return _needs(step, out), not out.get("ok", False)

    hwnd = find_app_window(program, app)
    if not hwnd:
        return _needs(step, {"ok": False, "error": f"no live window for app {app!r}"}), False

    if action == "focus":
        from builtin_tools import win_tools
        out = _json(await win_tools.win_ensure_foreground(hwnd, probe=False))
        return _needs(step, out, hwnd), not out.get("ok", False)

    profile = profiles.load(app)
    if profile is None and action in _STATE_ACTIONS:
        return _needs(step, {"ok": False, "error": f"no profile for app {app!r}",
                             "hint": "load_app_profile / discover_app first"}, hwnd), True

    if action == "wait_state":
        expected = step.get("state")
        timeout = float(step.get("timeout_s", 8))
        deadline = time.time() + max(0.0, timeout)
        live = None
        resolved: dict = {}
        while True:
            state = await _capture(hwnd)
            resolved = router.route(profile, state, app) if profile else {"state_id": None}
            live = resolved.get("state_id")
            if live == expected:
                return _needs(step, {"ok": True, "state_id": live}, hwnd), False
            if time.time() >= deadline:
                break
            await asyncio.sleep(0.7)
        return _needs(step, {"ok": False,
                             "error": f"wait_state {expected!r} timed out (live {live!r})",
                             "state_id": live,
                             "controls": sorted((resolved.get("controls") or {}).keys())}, hwnd), True

    state = await _capture(hwnd)
    routed = router.route(profile, state, app) if profile else {"state_id": None, "controls": {}}
    expected = step.get("state")
    if expected and routed.get("state_id") != expected:
        return _needs(step, {"ok": False,
                             "error": f"state mismatch: expected {expected!r}, live {routed.get('state_id')!r}",
                             "state_id": routed.get("state_id"),
                             "controls": sorted((routed.get("controls") or {}).keys())}, hwnd), True

    if action == "assert_state":
        if not expected:
            return _needs(step, {"ok": False, "error": "assert_state without a state"}, hwnd), True
        return _needs(step, {"ok": True, "state_id": routed.get("state_id")}, hwnd), False

    if action in CONTROL_ACTIONS:
        name = step.get("control") or ""
        if not name:
            return _needs(step, {"ok": False, "error": "unresolved control",
                                 "expected": step.get("expected", ""),
                                 "state_id": routed.get("state_id"),
                                 "controls": sorted((routed.get("controls") or {}).keys())}, hwnd), True
        res = router.resolve_named(profile, state, name)
        if not res:
            return _needs(step, {"ok": False, "error": f"control {name!r} not resolved",
                                 "state_id": routed.get("state_id"),
                                 "controls": sorted((routed.get("controls") or {}).keys())}, hwnd), True
        if res.get("id") and action != "right_click":
            from builtin_tools import uia_tools
            out = _json(await uia_tools.win_click_control(
                hwnd=hwnd, id=res["id"], double=(action == "double_click")))
        elif res.get("center"):
            from builtin_tools import mouse_tools
            button = "right" if action == "right_click" else "left"
            clicks = 2 if action == "double_click" else 1
            out = _json(await mouse_tools.mouse_click(
                x=res["center"][0], y=res["center"][1], button=button, clicks=clicks))
        else:
            return _needs(step, {"ok": False, "error": f"no id/centre for {name!r}"}, hwnd), True
        out.setdefault("control", name)
        out.setdefault("target", res.get("id") or res.get("center"))
        return _needs(step, out, hwnd), not out.get("ok", True)

    from builtin_tools import keybd_tools, win_tools

    if action == "type":
        await win_tools.win_ensure_foreground(hwnd, probe=True)
        out = _json(await keybd_tools.keybd_type(step.get("text", ""), hwnd=hwnd))
        return _needs(step, out, hwnd), not out.get("ok", True)

    if action == "key":
        key = step.get("key") or step.get("keys") or ""
        if not key:
            return _needs(step, {"ok": False, "error": "unresolved key"}, hwnd), True
        await win_tools.win_ensure_foreground(hwnd, probe=True)
        out = _json(await keybd_tools.keybd_stroke(key, hwnd=hwnd))
        return _needs(step, out, hwnd), not out.get("ok", True)

    if action == "hotkey":
        combo = step.get("keys") or step.get("key") or ""
        if not combo and step.get("control"):
            for st in (profile or {}).get("states") or []:
                c = (st.get("controls") or {}).get(step["control"])
                if isinstance(c, dict) and c.get("hotkey"):
                    combo = c["hotkey"]
                    break
        if not combo:
            return _needs(step, {"ok": False, "error": "no hotkey for step"}, hwnd), True
        await win_tools.win_ensure_foreground(hwnd, probe=True)
        out = _json(await keybd_tools.keybd_hotkey(combo, hwnd=hwnd))
        return _needs(step, out, hwnd), not out.get("ok", True)

    return _needs(step, {"ok": False, "error": f"unknown action {action!r}"}, hwnd), True


# ── public API ────────────────────────────────────────────────────────────
def start(name: str, from_step: str = "") -> dict:
    """Load a program and (re)start a run at the first step (or ``from_step``)."""
    program = load_program(name)
    if not program:
        return {"ok": False, "error": f"no workflow program {name!r}",
                "available": list_programs()}
    steps = program.get("steps") or []
    if not steps:
        return {"ok": False, "error": "program has no steps", "key": program.get("key")}
    index = 0
    if from_step:
        for i, s in enumerate(steps):
            if s.get("id") == from_step:
                index = i
                break
    run = {"key": program.get("key") or learning_db.normalize_key(name),
           "index": index, "started": date.today().isoformat(), "log": []}
    save_run(run)
    unresolved = [s.get("id") for s in steps if s.get("unresolved")]
    return {
        "ok": True,
        "key": run["key"],
        "total": len(steps),
        "index": index,
        "current_step": steps[index] if index < len(steps) else None,
        "unresolved_steps": unresolved,
        "apps": program.get("apps"),
        "problems": validate_program(program),
        "steps": [{"id": s.get("id"), "app": s.get("app"), "action": s.get("action"),
                   "state": s.get("state"), "control": s.get("control"),
                   "unresolved": bool(s.get("unresolved"))} for s in steps],
    }


async def run_step(name: str = "") -> dict:
    """Execute the current step of the run and advance on success."""
    run = load_run()
    if name and run.get("key") != learning_db.normalize_key(name):
        run = {}
    if not run or "key" not in run:
        if not name:
            return {"ok": False, "error": "no workflow run — call start_workflow(name) first"}
        started = start(name)
        if not started.get("ok"):
            return started
        run = load_run()

    program = load_program(run["key"])
    if not program:
        return {"ok": False, "error": f"workflow program {run['key']!r} not found"}
    steps = program.get("steps") or []
    idx = int(run.get("index", 0))
    if idx >= len(steps):
        return {"ok": True, "done": True, "key": run["key"], "index": idx,
                "total": len(steps), "message": "workflow complete"}

    step = steps[idx]
    result, needs_llm = await _exec(program, step)
    ok = bool(result.get("ok")) and not needs_llm
    run.setdefault("log", []).append({
        "id": step.get("id"), "action": step.get("action"),
        "ok": ok, "needs_llm": bool(needs_llm)})
    if ok:
        run["index"] = idx + 1
    save_run(run)

    out = {
        "ok": ok,
        "key": run["key"],
        "index": run["index"],
        "total": len(steps),
        "step": step,
        "result": result,
        "needs_llm": bool(needs_llm),
        "done": run["index"] >= len(steps),
    }
    if needs_llm:
        out["hint"] = (
            f"Step {step.get('id')} ({step.get('action')} "
            f"{step.get('control') or ''}) did not resolve deterministically — "
            "take a win_snapshot, locate the element and perform the action yourself, "
            "then call run_workflow_step again to continue.")
    logger.info(f"workflow_runner: step={step.get('id')} action={step.get('action')} "
                f"ok={ok} needs_llm={needs_llm} index={run['index']}/{len(steps)}")
    return out
