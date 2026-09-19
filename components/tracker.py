"""Learning tracker: record the user's real input + window context.

Used by the learning tools (``start_learning_session`` / ``stop_learning_session``).
While active it captures global mouse/keyboard events (pynput) and, for every
click, the foreground window plus a **whole-window screenshot** and the control
under the cursor (UIA, falling back to the window title). The recording is a
JSONL file per session:

    data/sessions/<label>_<ts>/events.jsonl
    data/sessions/<label>_<ts>/shots/<n>.jpg

A later summary step turns the recording into a workflow document. Only the last
``MAX_SESSIONS`` sessions are kept.

Note: the tracker records *all* input of the session, including anything the
agent itself injects while recording. Elevated (admin) windows are not reachable
from this non-admin process (UIPI), so their controls may not be labelled.
"""

from __future__ import annotations

import json
import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from lib import image_ops, ui_tree, winapi

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = ROOT / "data" / "sessions"
MAX_SESSIONS = 10
_SAMPLE_INTERVAL = 1.0

_session: Optional[dict] = None
_lock = threading.Lock()


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

            kind = event.get("kind")
            if kind == "click":
                x, y = int(event.get("x", 0)), int(event.get("y", 0))
                el = ui_tree.element_at_point_sync(x, y)
                if el.get("ok"):
                    event["control"] = {
                        "name": el.get("name", ""),
                        "control_type": el.get("control_type", ""),
                        "automation_id": el.get("automation_id", ""),
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
    while not session["stop"].is_set():
        try:
            fg = _foreground()
            key = (fg["hwnd"], fg["title"])
            if key != last:
                last = key
                _enqueue(session, {"kind": "focus", **fg})
        except Exception:
            pass
        time.sleep(_SAMPLE_INTERVAL)


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

        session = {
            "active": True, "label": label or "session", "dir": session_dir,
            "queue": queue.Queue(), "stop": threading.Event(),
            "events": 0, "clicks": 0, "shots": 0, "listeners": [],
        }
        _session = session

        session["writer"] = threading.Thread(target=_writer, args=(session,), name="tracker-writer", daemon=True)
        session["sampler"] = threading.Thread(target=_sampler, args=(session,), name="tracker-sampler", daemon=True)
        session["writer"].start()
        session["sampler"].start()

        def on_click(x, y, button, pressed):
            if pressed:
                _enqueue(session, {"kind": "click", "x": int(x), "y": int(y),
                                   "button": str(button), **_foreground()})

        def on_scroll(x, y, dx, dy):
            _enqueue(session, {"kind": "scroll", "x": int(x), "y": int(y),
                               "dx": int(dx), "dy": int(dy), **_foreground()})

        def on_press(key):
            _enqueue(session, {"kind": "key", "action": "press", "key": str(key),
                               "char": getattr(key, "char", None), **_foreground()})

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
        session["queue"].put(None)
        for name in ("writer", "sampler"):
            t = session.get(name)
            if t:
                t.join(timeout=5)
        session["active"] = False

        summary = {
            "ok": True, "label": session["label"], "dir": str(session["dir"]),
            "events": session["events"], "clicks": session["clicks"], "screenshots": session["shots"],
            "events_file": str(session["dir"] / "events.jsonl"),
        }
        logger.info(f"tracker: stopped ({summary['events']} events, {summary['clicks']} clicks)")
        _session = None
        return summary


def status() -> dict:
    if not is_active():
        return {"ok": True, "active": False}
    return {
        "ok": True, "active": True, "label": _session["label"], "dir": str(_session["dir"]),
        "events": _session["events"], "clicks": _session["clicks"],
    }
