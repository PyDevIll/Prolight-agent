"""Meta-tools for ProLight-agent — self-management commands.

Provides: ping (health check), reload_tools (hot-reload builtin tool modules)
and ask_user (ask the user a question mid-task and wait for the answer).
"""

import json
from datetime import datetime

from loguru import logger
from tool_registry import get_registry
from lib import overlay


async def ping() -> str:
    """Simple ping/pong health check. Returns 'pong' with current timestamp."""
    return json.dumps({
        "result": "pong",
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False)


async def reload_tools() -> str:
    """Hot-reload all builtin tool modules from disk.

    Use this after creating or editing tool files (e.g. new *_tools.py in builtin_tools/).
    Reloads every submodule of builtin_tools via importlib.reload(),
    re-running each module's register_all() to pick up new/changed tool definitions.
    """
    registry = get_registry()
    count = registry.hot_reload()
    tool_list = registry.list_tools()
    logger.info(f"Hot-reload complete: {count} modules, {len(registry._tools)} tools")
    return f"Reloaded {count} module(s).\n\n{tool_list}"


async def ask_user(question: str, options: list = None, timeout: float = 300.0,
                   highlight: list = None, highlight_label: str = "") -> str:
    """Ask the user a question on the console and wait for their answer.

    Use it before an uncertain or state-changing step (e.g. before clicking a
    control during an app discovery pass). Blocks until the user replies.

    Args:
        question: the question to ask.
        options: optional list of suggested answers to show.
        timeout: seconds to wait (default 300).
        highlight: optional screen region(s) to outline while asking, so the user
            sees exactly what the question is about (e.g. "is this the right
            area?"). Each region is [left,top,right,bottom] or an object with
            rect/hwnd/point(+radius)/bbox.
        highlight_label: optional label chip drawn on the highlighted region(s).
    """
    from app import ask_user_question

    token = None
    if highlight:
        token = overlay.highlight(highlight, persist=True, label=(highlight_label or None))
    try:
        answer = await ask_user_question(question, options=options, timeout=timeout)
    finally:
        if token is not None:
            overlay.clear_highlights(token)
    if answer is None:
        return json.dumps({"ok": False, "error": "no answer (timed out or no console)"}, ensure_ascii=False)
    return json.dumps({"ok": True, "answer": answer}, ensure_ascii=False)


TOOL_DEFINITIONS = [
    ("ping", ping, "Simple ping/pong health check. Returns pong with current timestamp.", {
        "type": "object",
        "properties": {},
        "required": [],
    }),
    ("ask_user", ask_user, "Ask the user a question and wait for their answer (for "
     "uncertain or state-changing steps). Blocks until they reply. Optionally "
     "highlight the screen region(s) the question is about.", {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to ask"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Suggested answers to show"},
            "timeout": {"type": "number", "description": "Seconds to wait (default 300)"},
            "highlight": {
                "type": "array",
                "description": "Screen region(s) to outline while asking: [left,top,right,bottom] "
                               "or {rect|hwnd|point(+radius)|bbox} objects",
                "items": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                        {
                            "type": "object",
                            "properties": {
                                "rect": {"type": "array", "items": {"type": "integer"}},
                                "hwnd": {"type": "integer"},
                                "point": {"type": "array", "items": {"type": "integer"}},
                                "radius": {"type": "integer"},
                                "label": {"type": "string"},
                            },
                        },
                    ]
                },
            },
            "highlight_label": {"type": "string", "description": "Label drawn on the highlighted region(s)"},
        },
        "required": ["question"],
    }),
    ("reload_tools", reload_tools, "Hot-reload all builtin tool modules without restarting", {
        "type": "object",
        "properties": {},
        "required": [],
    }),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    # ask_user blocks on a human; give it a longer cap than the 120s default
    # so its documented 300s timeout is actually honoured.
    registry.set_timeout("ask_user", 600.0)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} meta tool(s)")
