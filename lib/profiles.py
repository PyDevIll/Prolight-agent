"""Structured app profiles for ProLight-agent (Phase 2).

A *profile* is a machine-readable companion to an interaction guide:
``interaction_guides/<key>.profile.json`` (next to the human-readable
``<key>.md``). It makes an app's knowledge programmable:

  * **trigger**   — when this profile applies (process / title / url);
  * **states**    — recognised layouts of the app, each with:
      - ``detect``   — deterministic rules over a ``WindowState`` (OCR/UIA/menu);
      - ``text``     — a short fragment injected when the state is active;
      - ``controls`` — logical name → how to find/act on it;
  * **footprints** — stable element keys (+ window-relative rects) per state,
    used to verify the profile still matches across runs.

Guides remain valid on their own; profiles are an additive layer that
``lib.router`` consumes. See ``system_prompts/learning.md``.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

from loguru import logger

from lib import learning_db, winapi

PROFILE_SUFFIX = ".profile.json"
SCHEMA_VERSION = 1


def profile_path(key: str) -> Path:
    return learning_db.GUIDES_DIR / f"{learning_db.normalize_key(key)}{PROFILE_SUFFIX}"


def profile_exists(key: str) -> bool:
    return profile_path(key).exists()


# ── read / write ──────────────────────────────────────────────────────────
def load(key: str) -> Optional[dict]:
    p = profile_path(key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"profiles: cannot parse {p.name}: {e}")
        return None


def save(key: str, data: dict) -> Path:
    learning_db.ensure_dirs()
    data = dict(data or {})
    data.setdefault("key", learning_db.normalize_key(key))
    data.setdefault("version", SCHEMA_VERSION)
    data.setdefault("updated", date.today().isoformat())
    problems = validate(data)
    if problems:
        logger.warning(f"profiles: saving with validation issues: {problems}")
    p = profile_path(key)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"profiles: wrote {p.name} ({p.stat().st_size} bytes)")
    return p


def list_profiles() -> list[dict]:
    learning_db.ensure_dirs()
    out = []
    for p in sorted(learning_db.GUIDES_DIR.glob(f"*{PROFILE_SUFFIX}")):
        key = p.name[: -len(PROFILE_SUFFIX)]
        out.append({"key": key, "path": str(p), "bytes": p.stat().st_size})
    return out


def validate(data: dict) -> list[str]:
    """Return a list of human-readable problems ([] = OK)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["profile must be a JSON object"]
    trig = data.get("trigger")
    if trig is not None and not isinstance(trig, dict):
        problems.append("trigger must be an object")
    states = data.get("states")
    if states is not None:
        if not isinstance(states, list):
            problems.append("states must be a list")
        else:
            seen = set()
            for i, st in enumerate(states):
                if not isinstance(st, dict):
                    problems.append(f"state[{i}] must be an object")
                    continue
                sid = st.get("id")
                if not sid:
                    problems.append(f"state[{i}] missing 'id'")
                elif sid in seen:
                    problems.append(f"duplicate state id {sid!r}")
                seen.add(sid)
                det = st.get("detect")
                if det is not None and not isinstance(det, dict):
                    problems.append(f"state[{sid or i}].detect must be an object")
                ctrls = st.get("controls")
                if ctrls is not None and not isinstance(ctrls, dict):
                    problems.append(f"state[{sid or i}].controls must be an object")
    return problems


def template(key: str, display: str = "") -> dict:
    return {
        "key": learning_db.normalize_key(key),
        "display": display or key,
        "version": SCHEMA_VERSION,
        "updated": date.today().isoformat(),
        "trigger": {"process": [], "title_contains": [], "url_contains": []},
        "initial_text": "",
        "states": [
            {
                "id": "main",
                "detect": {"ocr_any": [], "uia_any": [], "min_controls": 1},
                "text": "",
                "controls": {},
            }
        ],
        "footprints": {},
    }


# ── trigger matching ──────────────────────────────────────────────────────
def _any_sub(hay: str, needles) -> bool:
    h = (hay or "").lower()
    return any(str(n).lower() in h for n in (needles or []) if str(n).strip())


def match_trigger(profile: dict, *, title: str = "", process: str = "", url: str = "") -> bool:
    trig = profile.get("trigger") or {}
    checks = []
    procs = trig.get("process")
    if procs:
        checks.append(_any_sub(process, procs))
    titles = trig.get("title_contains")
    if titles:
        checks.append(_any_sub(title, titles))
    urls = trig.get("url_contains")
    if urls:
        checks.append(_any_sub(url, urls))
    regexes = trig.get("title_regex")
    if regexes:
        checks.append(any(re.search(rg, title or "", re.I) for rg in regexes))
    # No constraints → matches anything (a catch-all profile).
    return all(checks) if checks else True


def find_profile(
    *, hwnd: Optional[int] = None, name: str = "", title: str = "", process: str = "", url: str = ""
) -> Optional[tuple[str, dict]]:
    """Find the profile for a window (guide key resolution order), and confirm
    its trigger matches once loaded. Returns ``(key, profile)`` or ``None``."""
    # Resolve the window's title/process once, for BOTH candidate keys and the
    # trigger check (otherwise a trigger on process/title never matches here).
    if hwnd and (not title or not process):
        h = int(hwnd)
        if not title:
            title = winapi.get_window_text(h) or ""
        if not process:
            process = winapi.get_process_name(winapi.get_window_pid(h)) or ""
    cands = learning_db.guide_candidates(hwnd=hwnd, name=name, title=title, process=process)
    for key in cands:
        prof = load(key)
        if prof is not None:
            if match_trigger(prof, title=title, process=process, url=url):
                return key, prof
            # A profile exists but the trigger rejects this window; keep looking.
    return None
