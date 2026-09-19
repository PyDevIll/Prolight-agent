"""Learning tools for ProLight-agent: interaction guides, workflows, sessions.

Backed by ``lib/learning_db.py`` (file DB) and ``components/tracker.py``
(recording the user's real actions). See ``system_prompts/learning.md``.

Tools:
  - list_guides / load_interaction_guide / save_interaction_guide / note_fact
  - list_workflows / load_workflow / save_workflow / find_workflow
  - start_learning_session / stop_learning_session
"""

import json
from typing import Optional

from loguru import logger

from components import tracker
from lib import learning_db


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


async def list_guides() -> str:
    """List the saved interaction guides (per-app knowledge)."""
    return _dump({"ok": True, "guides": learning_db.list_guides()})


async def load_interaction_guide(
    hwnd: int = None, name: str = "", title: str = "", process: str = ""
) -> str:
    """Load the interaction guide for an app (by window or explicit name).

    Resolution order: explicit ``name`` → window-title tail (``... - Microsoft
    Excel``) → title head (web-app name) → process name. If no guide exists yet,
    returns ``found: false`` with the candidate keys and a template to fill in
    (then call ``save_interaction_guide``).

    Args:
        hwnd: window to identify the app from.
        name: explicit guide key (e.g. "microsoft excel", "max").
        title: override the window title.
        process: override the process name.
    """
    res = learning_db.resolve_guide(
        hwnd=int(hwnd) if hwnd is not None else None,
        name=name, title=title, process=process,
    )
    if res["found"]:
        content = learning_db.read_guide(res["key"]) or ""
        logger.info(f"load_interaction_guide: {res['key']}")
        return _dump({"ok": True, "found": True, "key": res["key"],
                      "path": res["path"], "content": content})
    logger.info(f"load_interaction_guide: no guide for {res['candidates']}")
    return _dump({
        "ok": True, "found": False, "key": res["key"], "candidates": res["candidates"],
        "path": res["path"], "template": learning_db.guide_template(res["key"]),
        "hint": "No guide yet. Do a discovery pass, then save_interaction_guide(name=key, content=...).",
    })


async def save_interaction_guide(name: str, content: str) -> str:
    """Create or replace an interaction guide file (``interaction_guides/<name>.md``)."""
    path = learning_db.write_guide(name, content)
    return _dump({"ok": True, "key": learning_db.normalize_key(name), "path": str(path)})


async def note_fact(app: str, fact: str, section: str = "Notes") -> str:
    """Append a single learned fact to an app's guide (under ``## <section>``)."""
    path = learning_db.append_fact(app, fact, section=section)
    return _dump({"ok": True, "key": learning_db.normalize_key(app),
                  "path": str(path), "section": section})


async def list_workflows() -> str:
    """List the saved workflows (repeatable cross-app procedures)."""
    return _dump({"ok": True, "workflows": learning_db.list_workflows()})


async def find_workflow(query: str) -> str:
    """Find the workflows whose name/headings/body best match a task description."""
    return _dump({"ok": True, "query": query, "matches": learning_db.find_workflow(query)})


async def load_workflow(name: str) -> str:
    """Load a workflow document by name (``workflows/<name>.md``)."""
    content = learning_db.read_workflow(name)
    if content is None:
        return _dump({"ok": False, "error": f"no workflow named {name!r}",
                      "available": [w["key"] for w in learning_db.list_workflows()],
                      "template": learning_db.workflow_template(name)})
    return _dump({"ok": True, "key": learning_db.normalize_key(name), "content": content})


async def save_workflow(name: str, content: str) -> str:
    """Create or replace a workflow document (``workflows/<name>.md``)."""
    path = learning_db.write_workflow(name, content)
    return _dump({"ok": True, "key": learning_db.normalize_key(name), "path": str(path)})


async def start_learning_session(label: str = "") -> str:
    """Start recording the user's real mouse/keyboard actions (learning mode).

    On each click it also captures the whole foreground window and the control
    under the cursor. Stop with ``stop_learning_session``, then summarize the
    recording into a workflow.
    """
    result = tracker.start(label)
    return _dump(result)


async def stop_learning_session() -> str:
    """Stop the recording and return the session summary (events, clicks, screenshots)."""
    result = tracker.stop()
    return _dump(result)


TOOL_DEFINITIONS = [
    (
        "list_guides",
        list_guides,
        "List the saved interaction guides (per-app knowledge files).",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "load_interaction_guide",
        load_interaction_guide,
        "Load the interaction guide for an app (by window hwnd or an explicit "
        "name). If none exists, returns the candidate keys and a template so you "
        "can do a discovery pass and save one.",
        {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window to identify the app from"},
                "name": {"type": "string", "description": "Explicit guide key, e.g. 'microsoft excel' or 'max'"},
                "title": {"type": "string", "description": "Override the window title"},
                "process": {"type": "string", "description": "Override the process name"},
            },
            "required": [],
        },
    ),
    (
        "save_interaction_guide",
        save_interaction_guide,
        "Create or replace an interaction guide file (interaction_guides/<name>.md). "
        "Keep it general (areas and their purpose, key controls, shortcuts); "
        "coordinates of important controls are allowed.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Guide key / app name"},
                "content": {"type": "string", "description": "Full markdown content"},
            },
            "required": ["name", "content"],
        },
    ),
    (
        "note_fact",
        note_fact,
        "Append a single learned fact to an app's guide, under a section heading.",
        {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "Guide key / app name"},
                "fact": {"type": "string", "description": "The fact to record"},
                "section": {"type": "string", "description": "Section heading (default 'Notes')"},
            },
            "required": ["app", "fact"],
        },
    ),
    (
        "list_workflows",
        list_workflows,
        "List the saved workflows (repeatable cross-app procedures).",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "find_workflow",
        find_workflow,
        "Find the workflows that best match a task description (keyword scoring). "
        "Use it before starting a task to see if a procedure already exists.",
        {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Task description / keywords"}},
            "required": ["query"],
        },
    ),
    (
        "load_workflow",
        load_workflow,
        "Load a workflow document by name (workflows/<name>.md).",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Workflow name"}},
            "required": ["name"],
        },
    ),
    (
        "save_workflow",
        save_workflow,
        "Create or replace a workflow document (workflows/<name>.md). Include the "
        "goal, apps/windows, ordered steps and decision points.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name"},
                "content": {"type": "string", "description": "Full markdown content"},
            },
            "required": ["name", "content"],
        },
    ),
    (
        "start_learning_session",
        start_learning_session,
        "Start recording the user's real mouse/keyboard actions (learning mode). "
        "On each click it captures the foreground window and the control under "
        "the cursor. Stop with stop_learning_session.",
        {
            "type": "object",
            "properties": {"label": {"type": "string", "description": "Session label"}},
            "required": [],
        },
    ),
    (
        "stop_learning_session",
        stop_learning_session,
        "Stop the learning recording and return the session summary (events, "
        "clicks, screenshots) so you can summarize it into a workflow.",
        {"type": "object", "properties": {}, "required": []},
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} learning tool(s)")
