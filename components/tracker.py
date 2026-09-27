"""Learning tracker: record the user's real input + structured window states.

Used by the learning tools (``start_learning_session`` / ``stop_learning_session``).
While active it captures global mouse/keyboard events (pynput) and, for every
action, the foreground window plus a **whole-window screenshot** and the control
under the cursor (UIA, falling back to the window title).

In addition it captures a **structured ``WindowState``** (L1): after each action
it waits for the window to *settle* (pixel-stable; on timeout the state is
accepted anyway) and records that layout — controls, menu, OCR text, stable
keys and a window-relative footprint — one JSON line per capture. This makes the
recording machine-readable, so ``lib.session_analysis`` can deterministically
extend per-app profiles and draft a cross-app workflow (no LLM).

Recording layout::

    data/sessions/<label>_<ts>/events.jsonl      # raw input + focus/app-switch
    data/sessions/<label>_<ts>/states.jsonl      # settled WindowState captures
    data/sessions/<label>_<ts>/app_timeline.json # ordered app (guide-key) segments
    data/sessions/<label>_<ts>/shots/<n>.jpg     # whole-window screenshots

Only the last ``MAX_SESSIONS`` sessions are kept. Note: the tracker records
*all* input of the session, including anything the agent itself injects while
recording. Elevated (admin) windows are not reachable from this non-admin
process (UIPI), so their controls may not be labelled.
"""

from __future__ import annotations

import asyncio
import json
import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from lib import image_ops, learning_db, ui_tree, winapi, window_state

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = ROOT / "data" / "sessions"
MAX_SESSIONS = 10
_SAMPLE_INTERVAL = 1.0

# settle-before-capture tuning (seconds / poll counts)
_SETTLE_INTERVAL = 0.25
_SETTLE_POLLS = 2          # native apps: ~0.5 s quiet
_WEB_SETTLE_POLLS = 3      # web/Chromium: longer quiet
_SETTLE_MAX = 6.0          # cap; on timeout the state is accepted as settled
_DEBOUNCE = 0.5
_MAX_REPS = 60

_BROWSER_KEYWORDS = ("chrome", "msedge", "brave", "opera", "vivaldi", "chromium")
# Shell/taskbar windows are never an "app" to learn or merge into a profile.
_SHELL_CLASSES = ("shell_traywnd", "shell_secondarytraywnd", "notifyiconoverflowwindow",
                  "progman", "workerw")

_session: Optional[dict] = None
_lock = threading.Lock()


def _ov():
    """Overlay module (best-effort; feedback is optional)."""
    try:
        from lib import overlay
        return overlay
    except Exception:
        return None


def overlay_status():
    """``(text, kind)`` for the persistent HUD while recording, else ``None``.

    Used by ``app.py`` so a tool call cannot overwrite the learning indicator.
    """
    if is_active():
        return (f"● learning: {_session.get('label', '')} — {_session.get('states', 0)} step(s)",
                "learning")
    return None


def is_active() -> bool:
    return bool(_session and _session.get("active"))


def _prune_sessions() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    dirs = sorted(
        (d for d in SESSIONS_DIR.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for old in dirs[MAX_SESSIONS:]:
        shutil.rmtree(old, ignore_errors=True)
        logger.info(f"tracker: pruned old session {old.name}")


def latest() -> Optional[str]:
    """Path to the most recent session directory (or None)."""
    if not SESSIONS_DIR.exists():
        return None
    dirs = sorted((d for d in SESSIONS_DIR.iterdir() if d.is_dir()),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    return str(dirs[0]) if dirs else None


def _foreground() -> dict:
    hwnd = winapi.get_foreground_window()
    if not hwnd:
        return {"hwnd": 0, "title": "", "process": ""}
    return {
        "hwnd": int(hwnd),
        "title": winapi.get_window_text(hwnd) or "",
        "process": winapi.get_process_name(winapi.get_window_pid(hwnd)) or "",
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _enqueue(session: dict, event: dict) -> None:
    event.setdefault("ts", _now())
    try:
        session["queue"].put_nowait(event)
    except Exception:
        pass


# ── enrichment worker (screenshots + UIA lookup happen off the hook thread) ──
def _writer(session: dict) -> None:
    path: Path = session["dir"] / "events.jsonl"
    shots: Path = session["dir"] / "shots"
    with open(path, "a", encoding="utf-8") as fh:
        while True:
            try:
                event = session["queue"].get(timeout=0.5)
            except queue.Empty:
                if session["stop"].is_set():
                    break
                continue
            if event is None:
                break

            # Tag events from the agent's own console (where /learn was typed) so
            # the summariser can drop them (R19).
            if event.get("hwnd") and event.get("hwnd") == session.get("host_hwnd"):
                event["agent_console"] = True
            elif session.get("host_process") and \
                    (event.get("process") or "").lower() == session["host_process"]:
                event["agent_console"] = True

            kind = event.get("kind")
            if kind == "click":
                x, y = int(event.get("x", 0)), int(event.get("y", 0))
                el = ui_tree.element_at_point_sync(x, y)
                control = None
                if el.get("ok"):
                    control = {
                        "name": el.get("name", ""),
                        "control_type": el.get("control_type", ""),
                        "automation_id": el.get("automation_id", ""),
                    }
                    event["control"] = control
                    # The element under the cursor may belong to another window
                    # (e.g. the taskbar). Record its real owner so the click can
                    # be attributed correctly.
                    cwin = el.get("window") or {}
                    event["control_process"] = cwin.get("process", "")
                    event["control_hwnd"] = el.get("control_hwnd")
                    if cwin.get("process") and event.get("process") and \
                            cwin["process"].lower() != (event.get("process") or "").lower():
                        event["target_mismatch"] = True
                session["last_click"] = {
                    "control": control, "t": time.time(), "x": x, "y": y,
                    "process": (event.get("process") or "").lower(),
                    "control_process": (event.get("control_process") or "").lower(),
                }
                hwnd = event.get("hwnd") or 0
                if hwnd and winapi.is_window(hwnd):
                    try:
                        cap = winapi.capture_window(int(hwnd))
                        if cap:
                            img = image_ops.bgra_to_pil(cap)
                            img = image_ops.downscale(img, 1600)
                            shots.mkdir(parents=True, exist_ok=True)
                            session["shots"] += 1
                            sp = shots / f"{session['shots']:04d}.jpg"
                            img.save(sp, format="JPEG", quality=70)
                            event["screenshot"] = str(sp)
                    except Exception as e:
                        event["screenshot_error"] = str(e)

            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
            session["events"] += 1
            if kind == "click":
                session["clicks"] += 1


def _sampler(session: dict) -> None:
    """Emit a ``focus`` event whenever the foreground window changes."""
    last = None
    last_hwnd = None
    while not session["stop"].is_set():
        try:
            fg = _foreground()
            key = (fg["hwnd"], fg["title"])
            if key != last:
                last = key
                _enqueue(session, {"kind": "focus", **fg})
                if fg["hwnd"] != last_hwnd:
                    session["last_action_kind"] = "focus"
                last_hwnd = fg["hwnd"]
                session["dirty"].set()
        except Exception:
            pass
        time.sleep(_SAMPLE_INTERVAL)


# ── structured state capture (settle → WindowState → states.jsonl) ─────────
def _px_hash(hwnd: int):
    try:
        img = image_ops.grab_window(int(hwnd))
        if img is None:
            return None
        return hash(image_ops.downscale(img, 240).tobytes())
    except Exception:
        return None


def _settle(hwnd: int, is_web: bool) -> str:
    """Wait for the window pixels to stabilise; return 'stable' or 'timeout'."""
    polls = _WEB_SETTLE_POLLS if is_web else _SETTLE_POLLS
    last = None
    stable = 0
    start = time.time()
    while time.time() - start < _SETTLE_MAX:
        if _session is None or _session["stop"].is_set():
            break
        h = _px_hash(hwnd)
        if h is not None and h == last:
            stable += 1
            if stable >= polls:
                return "stable"
        else:
            stable = 0
        last = h
        time.sleep(_SETTLE_INTERVAL)
    return "timeout"  # user decision: a non-settling window is accepted anyway


def _capture_once(session: dict, loop, action: str) -> None:
    fg = _foreground()
    hwnd = fg["hwnd"]
    if not hwnd or not winapi.is_window(hwnd):
        return
    proc = (fg["process"] or "").lower()
    try:
        cls = (winapi.get_class_name(hwnd) or "").lower()
    except Exception:
        cls = ""
    # Ignore the console/terminal where /learn was typed, and the shell/taskbar.
    if hwnd == session.get("host_hwnd"):
        return
    if session.get("host_process") and proc == session["host_process"]:
        return
    if any(s in cls for s in _SHELL_CLASSES):
        return
    is_web = any(b in proc for b in _BROWSER_KEYWORDS) or "chrome_widgetwin" in cls
    stability = _settle(hwnd, is_web)
    try:
        # Fast path: UIA/menu only. OCR (slow) is added only when the tree is
        # poor/empty (custom-drawn or a11y-off apps), so normal captures are quick.
        state = loop.run_until_complete(window_state.capture_state(
            hwnd=hwnd, include={"uia", "menu", "focus"}, max_controls=200))
        if (state.uia_coverage or "").lower() in ("poor", "empty"):
            state = loop.run_until_complete(window_state.capture_state(
                hwnd=hwnd, include={"uia", "menu", "focus", "text"}, max_controls=200, max_text=40))
    except Exception as e:
        logger.warning(f"tracker: capture failed for {hwnd}: {e}")
        return
    key = learning_db.resolve_app_key(hwnd=hwnd, title=fg["title"], process=fg["process"])
    _emit_state(session, state, key, action, stability)


def _emit_state(session: dict, state, key: str, action: str, stability: str) -> None:
    session["seq"] += 1
    cid = f"k{session['seq']}"
    fp = state.footprint(with_rects=True)
    win_proc = (state.window.get("process") or "").lower()
    # Read the click at emit time (after the writer has enriched it) and bind it
    # to this state only when it belongs to the SAME app. A click whose target
    # is another app is BUFFERED (kept) and claimed by the next settled state of
    # its own process — a click on app B while app A is foreground is not lost.
    click = session.get("last_click") or {}
    if click and time.time() - click.get("t", 0) > _SETTLE_MAX + 8:
        click = {}
        session["last_click"] = {}
    click_proc = (click.get("control_process") or click.get("process") or "")
    same_app = (not click_proc) or (not win_proc) or (click_proc == win_proc)
    clicked = None
    if click and same_app:
        c = dict(click.get("control") or {})
        if c:
            # stable_key is derived from the element identity (no profile needed);
            # runtime id is attached from the captured control under the click.
            try:
                c["stable_key"] = window_state.Element(
                    id="", kind="control", name=c.get("name", ""),
                    control_type=c.get("control_type", ""),
                    automation_id=c.get("automation_id", "")).stable_key()
            except Exception:
                pass
            px, py = click.get("x"), click.get("y")
            if px is not None and py is not None:
                for e in list(state.controls) + list(state.texts):
                    r = e.rect
                    if r and r[0] <= px <= r[2] and r[1] <= py <= r[3]:
                        c["id"] = e.id
                        break
            clicked = c
            session["last_click"] = {}  # consumed by its own app's state
    typed = "".join(session.get("typed") or [])
    session["typed"] = []
    rec = {
        "capture_id": cid, "guide_key": key, "action": action,
        "ts_action": session.get("last_action_ts"), "ts_capture": _now(),
        "settled": True, "stability": stability, "typed": typed,
        "window": state.window, "focus": state.focus,
        "controls": [{"id": e.id, "name": e.name, "type": e.control_type,
                      "automation_id": e.automation_id, "rect": e.rect,
                      "value": e.value, "enabled": e.enabled} for e in state.controls],
        "texts": [{"id": e.id, "name": e.name, "rect": e.rect} for e in state.texts],
        "menu": [{"id": e.id, "name": e.name, "rect": e.rect} for e in state.menu],
        "clicked": clicked, "stable_keys": fp["keys"], "footprint": fp,
    }
    # persist compact record
    with open(session["states_path"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    session["states"] += 1
    session["reps"].append((rec, state))
    while len(session["reps"]) > _MAX_REPS:
        session["reps"].pop(0)

    # app-switch timeline
    if session.get("last_key") != key:
        if session.get("last_key") is not None:
            _enqueue(session, {"kind": "app_switch", "from": session["last_key"], "to": key,
                               "hwnd": state.window.get("hwnd"), "title": state.window.get("title", "")})
        session["last_key"] = key
        session["timeline"].append({
            "guide_key": key, "hwnd": state.window.get("hwnd"),
            "process": state.window.get("process"), "title": state.window.get("title"),
            "entered_ts": rec["ts_capture"], "captures": [],
        })
    session["timeline"][-1]["captures"].append(cid)

    # layout change within the same app
    prev = session.get("last_rec")
    if prev and prev.get("guide_key") == key:
        try:
            from lib import session_analysis
            if session_analysis.layout_changed(prev, rec):
                _enqueue(session, {"kind": "state_change", "from": prev["capture_id"], "to": cid})
        except Exception:
            pass
    session["last_rec"] = rec
    _enqueue(session, {"kind": "state", "capture_id": cid, "guide_key": key,
                       "stability": stability, "clicked": clicked})

    ov = _ov()
    if ov:
        ov.set_status(f"● learning: {session.get('label', '')} — {session['states']} step(s)", "learning")
        ov.notify(f"Learning: step {session['states']} recorded — ready for the next action",
                  "learning", duration=2.0)


def _capturer(session: dict) -> None:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        time.sleep(_DEBOUNCE)
        if not session["stop"].is_set():
            _capture_once(session, loop, "start")
        while not session["stop"].is_set():
            if not session["dirty"].wait(timeout=0.5):
                continue
            session["dirty"].clear()
            time.sleep(_DEBOUNCE)
            if session["stop"].is_set():
                break
            _capture_once(session, loop, session.get("last_action_kind") or "action")
    finally:
        try:
            loop.close()
        except Exception:
            pass


# ── public API ────────────────────────────────────────────────────────────
def start(label: str = "") -> dict:
    """Begin a recording session. Returns a summary dict."""
    global _session
    with _lock:
        if is_active():
            return {"ok": False, "error": "a learning session is already recording"}
        try:
            from pynput import keyboard, mouse
        except Exception as e:
            return {"ok": False, "error": f"pynput unavailable: {e}"}

        _prune_sessions()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (label or "session"))[:40]
        session_dir = SESSIONS_DIR / f"{safe or 'session'}_{stamp}"
        session_dir.mkdir(parents=True, exist_ok=True)

        host_hwnd = winapi.get_foreground_window() or 0
        try:
            host_proc = (winapi.get_process_name(winapi.get_window_pid(host_hwnd)) or "").lower() \
                if host_hwnd else ""
        except Exception:
            host_proc = ""
        session = {
            "active": True, "label": label or "session", "dir": session_dir,
            "queue": queue.Queue(), "stop": threading.Event(), "dirty": threading.Event(),
            "events": 0, "clicks": 0, "shots": 0, "states": 0, "seq": 0,
            "reps": [], "timeline": [], "last_key": None, "last_rec": None,
            "last_click": {}, "last_action_kind": "start", "last_action_ts": None,
            "host_hwnd": host_hwnd, "host_process": host_proc, "started_ts": _now(),
            "typed": [], "listeners": [], "states_path": session_dir / "states.jsonl",
        }
        _session = session

        session["writer"] = threading.Thread(target=_writer, args=(session,), name="tracker-writer", daemon=True)
        session["sampler"] = threading.Thread(target=_sampler, args=(session,), name="tracker-sampler", daemon=True)
        session["capturer"] = threading.Thread(target=_capturer, args=(session,), name="tracker-capturer", daemon=True)
        session["writer"].start()
        session["sampler"].start()
        session["capturer"].start()

        def _mark(kind: str):
            session["last_action_kind"] = kind
            session["last_action_ts"] = _now()
            session["dirty"].set()

        def on_click(x, y, button, pressed):
            if pressed:
                _mark("click")
                _enqueue(session, {"kind": "click", "x": int(x), "y": int(y),
                                   "button": str(button), **_foreground()})

        def on_scroll(x, y, dx, dy):
            _mark("scroll")
            _enqueue(session, {"kind": "scroll", "x": int(x), "y": int(y),
                               "dx": int(dx), "dy": int(dy), **_foreground()})

        def on_press(key):
            _mark("key")
            ch = getattr(key, "char", None)
            if ch:
                session["typed"].append(ch)
            else:
                k = str(key).lower()
                if "enter" in k:
                    session["typed"].append("\n")
                elif "space" in k:
                    session["typed"].append(" ")
                elif "backspace" in k and session["typed"]:
                    session["typed"].pop()
            _enqueue(session, {"kind": "key", "action": "press", "key": str(key),
                               "char": ch, **_foreground()})

        try:
            ml = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
            kl = keyboard.Listener(on_press=on_press)
            ml.start()
            kl.start()
            session["listeners"] = [ml, kl]
        except Exception as e:
            session["stop"].set()
            return {"ok": False, "error": f"could not start listeners: {e}"}

        logger.info(f"tracker: recording to {session_dir}")
        ov = _ov()
        if ov:
            ov.set_status(f"● learning: {session['label']}", "learning")
            ov.notify("Learning started — perform the task, then type /learn stop",
                      "learning", sticky=True)
        return {"ok": True, "dir": str(session_dir), "label": session["label"]}


def stop() -> dict:
    """Stop the recording and return a summary (dir, counts)."""
    global _session
    with _lock:
        session = _session
        if not session or not session.get("active"):
            return {"ok": False, "error": "no active learning session"}

        for lst in session.get("listeners", []):
            try:
                lst.stop()
            except Exception:
                pass
        session["stop"].set()
        session["dirty"].set()
        session["queue"].put(None)
        for name in ("writer", "sampler", "capturer"):
            t = session.get(name)
            if t:
                t.join(timeout=8)

        # persist the ordered app timeline + session metadata
        try:
            (session["dir"] / "app_timeline.json").write_text(
                json.dumps(session["timeline"], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"tracker: could not write app_timeline.json: {e}")
        try:
            (session["dir"] / "session_meta.json").write_text(json.dumps({
                "label": session["label"], "host_hwnd": session.get("host_hwnd"),
                "host_process": session.get("host_process"),
                "started": session.get("started_ts"), "stopped": _now(),
                "counts": {"events": session["events"], "clicks": session["clicks"],
                           "states": session["states"], "screenshots": session["shots"]},
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"tracker: could not write session_meta.json: {e}")

        session["active"] = False
        summary = {
            "ok": True, "label": session["label"], "dir": str(session["dir"]),
            "events": session["events"], "clicks": session["clicks"], "screenshots": session["shots"],
            "states": session["states"], "apps": [s.get("guide_key") for s in session["timeline"]],
            "events_file": str(session["dir"] / "events.jsonl"),
            "states_file": str(session["states_path"]),
            "timeline_file": str(session["dir"] / "app_timeline.json"),
        }
        logger.info(f"tracker: stopped ({summary['events']} events, {summary['clicks']} clicks, "
                    f"{summary['states']} states, {len(summary['apps'])} app segments)")
        ov = _ov()
        if ov:
            ov.clear_toast()
            ov.notify(f"Learning stopped — {summary['states']} step(s), "
                      f"{len(summary['apps'])} app(s)", "done", duration=3.0)
            ov.set_status("done", "done")
        _session = None
        return summary


def status() -> dict:
    if not is_active():
        return {"ok": True, "active": False, "last": last_summary() or None}
    return {
        "ok": True, "active": True, "label": _session["label"], "dir": str(_session["dir"]),
        "events": _session["events"], "clicks": _session["clicks"], "states": _session["states"],
    }


def last_summary() -> dict:
    """Summary of the most recent session on disk (or ``{}``).

    Lets the agent tell "the user already stopped it" from "never started" — the
    recording is gone from memory, but its files are still there.
    """
    d = latest()
    if not d:
        return {}
    p = Path(d)
    events = states = clicks = 0
    ep = p / "events.jsonl"
    if ep.exists():
        for line in ep.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"kind": "click"' in line or '"kind":"click"' in line:
                clicks += 1
            if line.strip():
                events += 1
    sp = p / "states.jsonl"
    if sp.exists():
        states = sum(1 for ln in sp.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
    apps: list = []
    tp = p / "app_timeline.json"
    if tp.exists():
        try:
            apps = [s.get("guide_key") for s in json.loads(tp.read_text(encoding="utf-8"))]
        except Exception:
            apps = []
    return {
        "dir": str(p), "label": p.name, "events": events, "clicks": clicks, "states": states,
        "apps": apps,
        "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
    }
