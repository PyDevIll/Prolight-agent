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


def guide_candidates(
    hwnd: Optional[int] = None, name: str = "", title: str = "", process: str = ""
) -> list[str]:
    """Ordered guide keys for a window: name → title tail → title head → process."""
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
    if process:
        add(process)
    return cands


def resolve_guide(
    hwnd: Optional[int] = None, name: str = "", title: str = "", process: str = ""
) -> dict:
    """Return the guide to use for a window.

    ``found`` is True when a file already exists; otherwise ``key``/``path``
    point at where a new guide should be written (process name by default).
    """
    cands = guide_candidates(hwnd=hwnd, name=name, title=title, process=process)
    for key in cands:
        path = GUIDES_DIR / f"{key}.md"
        if path.exists():
            return {"found": True, "key": key, "path": str(path), "candidates": cands}
    default = cands[-1] if cands else "unnamed"
    return {
        "found": False, "key": default,
        "path": str(GUIDES_DIR / f"{default}.md"), "candidates": cands,
    }


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
