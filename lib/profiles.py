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
import hashlib
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


def load_with_error(key: str) -> tuple[Optional[dict], Optional[str]]:
    """Like :func:`load` but returns ``(profile, error)``.

    ``error`` distinguishes "missing" from "invalid JSON" (a hand-edited file),
    and only a dict is returned (a valid-JSON scalar is reported as malformed).
    """
    p = profile_path(key)
    if not p.exists():
        return None, "missing"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"invalid JSON: {e}"
    if not isinstance(data, dict):
        return None, "profile must be a JSON object"
    return data, None


def structural_problems(problems: list) -> list:
    """The subset of ``validate`` problems that make a profile unusable.

    Used to refuse writing a malformed profile (so a bad edit can never replace a
    good file); informational warnings such as the ``url_contains`` note are not
    structural.
    """
    prefixes = ("states must", "state[", "trigger must", "profile must")
    return [p for p in problems if str(p).startswith(prefixes)]


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
    if isinstance(trig, dict) and (trig.get("url_contains") or trig.get("url_regex")):
        problems.append("trigger.url_contains/url_regex needs a runtime URL (Chromium omnibox); "
                        "it is ignored when no URL is available — add a process/title check or "
                        "also_matches")
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
    for s in shadowed_rules(data):
        problems.append(f"state '{s['broad']}' rule {s['broad_rule']!r} is shadowed by "
                        f"'{s['specific']}' rule {s['specific_rule']!r} (substring overlap — "
                        f"most-specific-first routing may still mislabel; disambiguate the rules)")
    return problems


def states_hash(profile: dict) -> str:
    """Stable hash of the profile's state id set (for guide-sync checks)."""
    states = profile.get("states") if isinstance(profile, dict) else None
    ids = [s.get("id") for s in states or [] if isinstance(s, dict)]
    return hashlib.sha1(",".join(sorted(i for i in ids if i)).encode("utf-8")).hexdigest()[:12]


def guide_stale(profile: dict) -> bool:
    """True when the guide's managed state block is out of sync with the profile.

    Only meaningful once a profile has been synced (``guide_synced`` present);
    hand-authored profiles without that marker are never reported stale.
    """
    gs = profile.get("guide_synced") if isinstance(profile, dict) else None
    if not isinstance(gs, dict):
        return False
    return gs.get("ids_hash") != states_hash(profile)


def shadowed_rules(profile: dict) -> list[dict]:
    """Overlapping/shadowed detect rules: one state's rule is a substring of another's.

    Such a pair makes the broader state shadow the more specific one at route
    time (R14). Returns ``[{'broad', 'specific', 'broad_rule', 'specific_rule'}]``.
    Tolerates malformed shapes (a hand-edited ``states`` that is not a list of
    objects) so validation never crashes.
    """
    def norm(s: str) -> str:
        return " ".join((s or "").split()).strip().lower()

    states = profile.get("states") if isinstance(profile, dict) else None
    if not isinstance(states, list):
        return []
    entries: list[tuple] = []
    for st in states:
        if not isinstance(st, dict):
            continue
        det = st.get("detect") or {}
        if not isinstance(det, dict):
            continue
        for key in ("uia_all", "uia_any"):
            rules = det.get(key) or []
            if not isinstance(rules, list):
                continue
            for r in rules:
                if not isinstance(r, dict):
                    continue
                s = norm(r.get("name") or r.get("automation_id") or "")
                if s:
                    entries.append((st.get("id"), s))
    out, seen = [], set()
    for id_a, s_a in entries:
        for id_b, s_b in entries:
            if id_a and id_b and id_a != id_b and s_a and s_b and s_a != s_b and s_a in s_b:
                pair = (id_a, id_b)
                if pair not in seen:
                    seen.add(pair)
                    out.append({"broad": id_a, "specific": id_b,
                                "broad_rule": s_a, "specific_rule": s_b})
    return out


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
    # A url constraint with no runtime URL is IGNORED (R24), never forced to fail
    # (otherwise it silently disables the profile).
    if urls and url:
        checks.append(_any_sub(url, urls))
    uregex = trig.get("url_regex")
    if uregex and url:
        checks.append(any(re.search(rg, url, re.I) for rg in uregex))
    regexes = trig.get("title_regex")
    if regexes:
        checks.append(any(re.search(rg, title or "", re.I) for rg in regexes))
    # No constraints → matches anything (a catch-all profile).
    return all(checks) if checks else True


def also_matches(profile: dict, *, title: str = "", process: str = "", url: str = "") -> bool:
    """Alias match (R25): does ``also_matches`` claim this window/app?

    Lets one profile per web app (keyed ``max``) match its tab host
    (``web.max.ru``) or a title substring, even though the process is just
    ``chrome``.
    """
    aliases = [str(a) for a in (profile.get("also_matches") or []) if str(a).strip()]
    if not aliases:
        return False
    blobs = [(title or "").lower(), (url or "").lower(), (process or "").lower()]
    return any(a.lower() in b for a in aliases for b in blobs)


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
    cands = learning_db.guide_candidates(hwnd=hwnd, name=name, title=title,
                                         process=process, url=url)
    for key in cands:
        prof = load(key)
        if prof is not None:
            if match_trigger(prof, title=title, process=process, url=url):
                return key, prof
            # A profile exists but the trigger rejects this window; keep looking.
    # Alias fallback (R25): a profile whose `also_matches` claims this window.
    for item in list_profiles():
        if item["key"] in cands:
            continue
        prof = load(item["key"])
        if prof is not None and also_matches(prof, title=title, process=process, url=url):
            return item["key"], prof
    return None
