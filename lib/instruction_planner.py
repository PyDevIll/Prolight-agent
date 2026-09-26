"""Deterministic instruction planner for ProLight-agent (Phase 1).

Instead of a single all-encompassing system prompt and every tool on every
request, the planner selects a small set of **prompt fragments** and **tool
groups** from cheap, non-LLM signals:

  * the user's goal text (keyword heuristics);
  * the foreground window and whether a guide/profile resolves for it.

Later phases (structured profiles, a state router, deterministic discovery) will
feed the same ``PromptPlan``; the planner itself performs no LLM calls.

Fragments live in ``system_prompts/fragments/<id>.md`` and are grouped so that a
fragment documents exactly the tools of its group(s).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS_DIR = ROOT / "system_prompts" / "fragments"

# ── always injected ───────────────────────────────────────────────────────
ALWAYS_FRAGMENTS = ["operating", "perception", "meta"]
CORE_GROUPS = {"meta", "perception"}

# ── fragment → tool groups it documents ───────────────────────────────────
FRAGMENT_GROUPS: dict[str, set] = {
    "operating": set(),
    "perception": {"perception"},
    "meta": {"meta"},
    "locate": {"locate"},
    "vision": {"vision"},
    "actuation": {"mouse", "keybd"},
    "uia": {"uia"},
    "probe": {"probe"},
    "learning": {"learning"},
    "files": {"fs", "edit"},
    "overlay": {"overlay"},
}

ALL_GROUPS = sorted(set().union(*FRAGMENT_GROUPS.values()) | CORE_GROUPS)

# ── phase → extra fragments (ALWAYS_FRAGMENTS are added automatically) ────
PHASE_FRAGMENTS: dict[str, list[str]] = {
    "route":    ["learning"],
    "discover": ["probe", "vision", "locate", "overlay", "learning"],
    "act":      ["uia", "actuation", "locate", "vision", "overlay", "learning"],
    "files":    ["files"],
    "learn":    ["learning", "files"],
}

_PROMPT_PREFIX = "[Command prompt]:"


@dataclass
class PlannerContext:
    user_prompt: str = ""
    hwnd: Optional[int] = None
    title: str = ""
    process: str = ""
    guide_found: bool = False
    guide_key: str = ""
    unlocked_groups: set = field(default_factory=set)


@dataclass
class PromptPlan:
    phase: str
    fragments: list[str]
    tool_groups: set
    volatile: str = ""

    def describe(self) -> str:
        return (f"phase={self.phase} fragments={self.fragments} "
                f"groups={sorted(self.tool_groups)}")


# ── fragment access ───────────────────────────────────────────────────────
def fragment_path(fid: str) -> Path:
    return FRAGMENTS_DIR / f"{fid}.md"


def read_fragment(fid: str) -> str:
    p = fragment_path(fid)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def available_fragments() -> list[str]:
    return sorted(p.stem for p in FRAGMENTS_DIR.glob("*.md")) if FRAGMENTS_DIR.exists() else []


# ── phase detection (deterministic heuristics) ────────────────────────────
def _clean(prompt: str) -> str:
    p = (prompt or "").strip()
    if p.startswith(_PROMPT_PREFIX):
        p = p[len(_PROMPT_PREFIX):].strip()
    return p.lower()


def detect_phase(ctx: PlannerContext) -> str:
    p = _clean(ctx.user_prompt)

    # Explicit "teach/record a workflow".
    if any(k in p for k in ("learning session", "record my", "teach me", "teach you")):
        return "learn"
    if "learn" in p and "workflow" in p:
        return "learn"
    # Explicit app exploration.
    if any(k in p for k in ("discover", "explore", "figure out")) or \
       ("learn" in p and "app" in p):
        return "discover"
    # File/code editing goals.
    if any(k in p for k in (".py", ".md", "edit ", "patch", "refactor", "rename file")) \
       and not any(k in p for k in ("telegram", "chrome", "browser", "excel", "outlook")):
        return "files"
    # Default: a normal acting task (learning tools stay available for discovery).
    # NOTE: the foreground window at prompt time is usually the console, so it is
    # not a reliable signal yet — app-aware discovery arrives with the router.
    return "act"


def plan(ctx: PlannerContext) -> PromptPlan:
    phase = detect_phase(ctx)
    frags = list(ALWAYS_FRAGMENTS)
    for fid in PHASE_FRAGMENTS.get(phase, []):
        if fid not in frags and fragment_path(fid).exists():
            frags.append(fid)

    groups = set(CORE_GROUPS)
    for fid in frags:
        groups |= FRAGMENT_GROUPS.get(fid, set())
    groups |= {str(g).strip().lower() for g in ctx.unlocked_groups
               if str(g).strip().lower() in ALL_GROUPS}

    return PromptPlan(phase=phase, fragments=frags, tool_groups=groups)


def base_prompts_for(plan: PromptPlan) -> list[tuple[str, Optional[str]]]:
    """Build the ``(text, file)`` base-prompt list for ``construct_history``."""
    out: list[tuple[str, Optional[str]]] = []
    for fid in plan.fragments:
        p = fragment_path(fid)
        if p.exists():
            out.append(("", str(p)))
        else:
            logger.warning(f"instruction_planner: missing fragment {fid!r}")
    if plan.volatile:
        out.append((plan.volatile, None))
    return out
