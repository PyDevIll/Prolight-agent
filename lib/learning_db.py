"""Learned-knowledge database for ProLight-agent.

Two file databases (see ``system_prompts/learning.md``):

* ``interaction_guides/<key>.md`` — general facts about how to operate an app
  (its interactive areas and their purpose, key controls, shortcuts, gotchas).
  The ``<key>`` is resolved from a window: explicit name → title tail → title
  head → process name.
* ``workflows/<task>.md`` — repeatable cross-app procedures to reach a goal.

This module owns path/naming/resolution and read/write/search. Tools live in
``builtin_tools/learning_tools.py``.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from loguru import logger

from lib import winapi

ROOT = Path(__file__).resolve().parent.parent
GUIDES_DIR = ROOT / "interaction_guides"
WORKFLOWS_DIR = ROOT / "workflows"

_TITLE_SPLIT = re.compile(r"\s+[-–—|·:]\s+")


def ensure_dirs() -> None:
    GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


# ── key normalization / resolution ────────────────────────────────────────
def normalize_key(name: str) -> str:
    """Lower-case, drop ``.exe``, collapse punctuation/spaces (keeps Cyrillic)."""
    s = (name or "").strip().lower()
    s = re.sub(r"\.exe$", "", s)
    s = re.sub(r"[^0-9a-z\u0400-\u04ff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_segments(title: str) -> list[str]:
    """Split a window title on common separators (`` - ``, `` — ``, `` | ``...)."""
    return [p.strip() for p in _TITLE_SPLIT.split(title or "") if p.strip()]


_TLD = {"com", "ru", "org", "net", "io", "co", "en", "su", "ua", "by", "kz",
        "info", "biz", "dev", "app", "me", "tv", "рф", "online", "site", "tech"}


def _host_labels(url: str) -> list[str]:
    """Host labels of a URL, most-specific (right) first, TLD/www dropped."""
    if not url:
        return []
    m = re.search(r"://([^/?#]+)", url) or re.match(r"([^/?#]+)", url)
    host = (m.group(1) if m else url).lower()
    host = re.sub(r":\d+$", "", host).strip(".")
    labels = [l for l in host.split(".") if l and l not in ("www",)]
    return [l for l in reversed(labels) if l not in _TLD]


def _host_primary(url: str) -> str:
    """The second-level label of a URL: web.max.ru → max, www.google.com → google."""
    labels = _host_labels(url)
    return labels[0] if labels else ""


def guide_candidates(
    hwnd: Optional[int] = None, name: str = "", title: str = "", process: str = "",
    url: str = "",
) -> list[str]:
    """Ordered guide keys for a window: name → title tail → title head → host → process."""
    cands: list[str] = []

    def add(value: str) -> None:
        key = normalize_key(value)
        if key and key not in cands:
            cands.append(key)

    if name:
        add(name)
    if hwnd:
        hwnd = int(hwnd)
        if not title:
            title = winapi.get_window_text(hwnd) or ""
        if not process:
            process = winapi.get_process_name(winapi.get_window_pid(hwnd)) or ""
    segments = title_segments(title)
    if segments:
        add(segments[-1])          # tail: the app name ("... - Microsoft Excel")
        if len(segments) > 1:
            add(segments[0])       # head: the document/page name (web apps)
    if url:
        # Host-derived keys so a per-web-app profile is reachable in one browser
        # window (R25): full host then its labels ("max" for web.max.ru).
        add(_host_primary(url))
        add(url if "://" not in url else url.split("://", 1)[1])
    if process:
        add(process)
    return cands


def resolve_guide(
    hwnd: Optional[int] = None, name: str = "", title: str = "", process: str = "",
    url: str = "",
) -> dict:
    """Return the guide to use for a window.

    ``found`` is True when a file already exists; otherwise ``key``/``path``
    point at where a new guide should be written (process name by default).
    """
    cands = guide_candidates(hwnd=hwnd, name=name, title=title, process=process, url=url)
    for key in cands:
        path = GUIDES_DIR / f"{key}.md"
        if path.exists():
            return {"found": True, "key": key, "path": str(path), "candidates": cands}
    default = cands[-1] if cands else "unnamed"
    return {
        "found": False, "key": default,
        "path": str(GUIDES_DIR / f"{default}.md"), "candidates": cands,
    }


_BROWSER_PROCS = ("chrome", "msedge", "brave", "opera", "vivaldi", "chromium")


def _usable_title_segment(s: str) -> bool:
    """A title segment is a usable app name (not a path/URL/very long string)."""
    s = (s or "").strip()
    if not s or len(s) > 60:
        return False
    if any(ch in s for ch in ("/", "\\", "://")):
        return False
    return any(c.isalnum() for c in s)


def resolve_app_key(hwnd: Optional[int] = None, title: str = "", process: str = "",
                    url: str = "") -> str:
    """Best guide key for the app in a window — the create/extend target.

    Resolution: an **existing** guide/profile for any candidate wins; otherwise a
    browser's page/app is identified by the **tab host** (``web.max.ru`` →
    ``max``) when a URL is available, else the title head, and a native app by the
    **title tail** (``"Book1 - Microsoft Excel"`` → ``microsoft excel``); else the
    process name. Path/URL-like or over-long title segments are rejected so a
    shell title such as ``MINGW64:/c/Users/...`` never becomes a key.
    """
    if hwnd and (not title or not process):
        h = int(hwnd)
        if not title:
            title = winapi.get_window_text(h) or ""
        if not process:
            process = winapi.get_process_name(winapi.get_window_pid(h)) or ""
    cands = guide_candidates(hwnd=hwnd, title=title, process=process, url=url)
    for key in cands:
        if (GUIDES_DIR / f"{key}.md").exists() or (GUIDES_DIR / f"{key}.profile.json").exists():
            return key
    segs = [s for s in title_segments(title) if _usable_title_segment(s)]
    proc = normalize_key(process)
    if any(b in proc for b in _BROWSER_PROCS):
        if url:
            hp = _host_primary(url)
            if hp:
                return hp[:48]
        if segs:
            return normalize_key(segs[0])[:48]
    if segs:
        return normalize_key(segs[-1])[:48]
    return (proc or (cands[-1] if cands else "unnamed"))[:48]


# ── guides ────────────────────────────────────────────────────────────────
def read_guide(key: str) -> Optional[str]:
    path = GUIDES_DIR / f"{normalize_key(key)}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def write_guide(key: str, content: str) -> Path:
    ensure_dirs()
    path = GUIDES_DIR / f"{normalize_key(key)}.md"
    path.write_text(content, encoding="utf-8")
    logger.info(f"learning_db: wrote guide {path.name} ({len(content)} chars)")
    return path


def append_fact(key: str, fact: str, section: str = "Notes") -> Path:
    """Append a bullet under ``## <section>`` (creating the file/section if needed)."""
    ensure_dirs()
    path = GUIDES_DIR / f"{normalize_key(key)}.md"
    heading = f"## {section}"
    bullet = f"- {fact.strip()}"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        lines = text.rstrip().splitlines()
        try:
            idx = next(i for i, ln in enumerate(lines) if ln.strip().lower() == heading.lower())
        except StopIteration:
            text = text.rstrip() + f"\n\n{heading}\n{bullet}\n"
        else:
            insert = idx + 1
            while insert < len(lines) and not lines[insert].startswith("## "):
                insert += 1
            lines.insert(insert, bullet)
            text = "\n".join(lines) + "\n"
    else:
        text = f"# {key}\n\n{heading}\n{bullet}\n"
    path.write_text(text, encoding="utf-8")
    logger.info(f"learning_db: appended fact to {path.name} [{section}]")
    return path


def list_guides() -> list[dict]:
    ensure_dirs()
    return [
        {"key": p.stem, "path": str(p), "bytes": p.stat().st_size}
        for p in sorted(GUIDES_DIR.glob("*.md"))
    ]


# Managed state table: only this delimited block is machine-refreshed; the rest of
# the guide is hand-written prose and is never touched by profile merges.
STATES_BLOCK_START = "<!-- PROLIGHT:STATES -->"
STATES_BLOCK_END = "<!-- /PROLIGHT:STATES -->"


def upsert_states_block(key: str, block: str) -> Path:
    """Insert/replace the managed ``PROLIGHT:STATES`` block in ``<key>.md``.

    Everything outside the block is preserved. Creates the guide when missing.
    """
    ensure_dirs()
    path = GUIDES_DIR / f"{normalize_key(key)}.md"
    block = (block or "").strip()
    if not block.startswith(STATES_BLOCK_START):
        block = f"{STATES_BLOCK_START}\n{block}\n{STATES_BLOCK_END}"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if STATES_BLOCK_START in text and STATES_BLOCK_END in text:
            pre = text[: text.index(STATES_BLOCK_START)]
            post = text[text.index(STATES_BLOCK_END) + len(STATES_BLOCK_END):]
            text = pre + block + post
        else:
            text = text.rstrip() + "\n\n" + block + "\n"
    else:
        text = block + "\n"
    path.write_text(text, encoding="utf-8")
    logger.info(f"learning_db: synced states block in {path.name}")
    return path


def guide_template(name: str) -> str:
    return (
        f"# {name} — interaction guide\n\n"
        "## Purpose / overview\n"
        "- What the app is for; the windows/dialogs it usually shows.\n\n"
        "## Window & focus\n"
        "- Process/window class; how to find it; focus quirks (keyboard delivery, etc.).\n\n"
        "## Main areas\n"
        "- The important regions and what they are for (menus, toolbars, panes, editor, status bar).\n"
        "- Coordinates of stable, important controls are welcome here (with a note they may move).\n\n"
        "## Key controls & what they do\n"
        "- Control (name/role) → effect.\n\n"
        "## Shortcuts\n"
        "- Keyboard shortcuts worth using.\n\n"
        "## Reading app state\n"
        "- How to tell the current mode/state (a label, a pane name, a status field).\n\n"
        "## Gotchas\n"
        "- Traps, destructive actions, things that look clickable but are not.\n\n"
        f"## Last verified\n- {date.today().isoformat()}\n"
    )


# ── workflows ─────────────────────────────────────────────────────────────
def read_workflow(key: str) -> Optional[str]:
    path = WORKFLOWS_DIR / f"{normalize_key(key)}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def write_workflow(key: str, content: str) -> Path:
    ensure_dirs()
    path = WORKFLOWS_DIR / f"{normalize_key(key)}.md"
    path.write_text(content, encoding="utf-8")
    logger.info(f"learning_db: wrote workflow {path.name} ({len(content)} chars)")
    return path


def list_workflows() -> list[dict]:
    ensure_dirs()
    return [
        {"key": p.stem, "path": str(p), "bytes": p.stat().st_size}
        for p in sorted(WORKFLOWS_DIR.glob("*.md"))
        if not p.stem.startswith("_")   # drafts are not real workflows (R21)
    ]


def workflow_template(name: str) -> str:
    return (
        f"# Workflow: {name}\n\n"
        "## Goal\n- The outcome this procedure achieves.\n\n"
        "## Apps / windows\n- Which applications and windows are involved.\n\n"
        "## Steps\n"
        "1. ...\n2. ...\n\n"
        "## Decision points\n- Conditions and how to branch.\n\n"
        "## Follow-ups / heartbeat\n- Any deferred check (e.g. 'wait for a reply and check').\n"
    )


def _tokens(query: str) -> list[str]:
    return [t for t in re.findall(r"[0-9a-z\u0400-\u04ff]+", (query or "").lower()) if len(t) >= 3]


def find_workflow(query: str, limit: int = 3) -> list[dict]:
    """Score workflows by query terms in the name (×3), headings (×2), body (×1)."""
    ensure_dirs()
    terms = _tokens(query)
    results = []
    for path in sorted(WORKFLOWS_DIR.glob("*.md")):
        if path.stem.startswith("_"):   # skip drafts (R21)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        headings = " ".join(ln for ln in text.splitlines() if ln.lstrip().startswith("#"))
        name = path.stem.lower()
        body = text.lower()
        score = 0
        for t in terms:
            score += 3 * name.count(t)
            score += 2 * headings.lower().count(t)
            score += body.count(t)
        if score > 0:
            results.append({"key": path.stem, "score": score, "path": str(path)})
    results.sort(key=lambda d: d["score"], reverse=True)
    return results[:limit]


_REF_RE = re.compile(
    r"((?:workflows|interaction_guides)/[^\n`\"'|]+?\.(?:workflow\.json|profile\.json|md))")


def list_links() -> list[dict]:
    """Check references from guides/workflows to other files (R22).

    Returns a list of dangling references: ``{from, ref, target, exists: false}``.
    Useful to catch a guide that points at a workflow which was never created.
    """
    ensure_dirs()
    out: list[dict] = []
    for folder, kind in ((GUIDES_DIR, "interaction_guides"), (WORKFLOWS_DIR, "workflows")):
        for p in sorted(folder.glob("*.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in _REF_RE.finditer(text):
                ref = m.group(1).rstrip(".,;:)`")
                if not (ROOT / ref).exists():
                    out.append({"from": f"{kind}/{p.name}", "ref": ref,
                                "target": str(ROOT / ref), "exists": False})
    return out
