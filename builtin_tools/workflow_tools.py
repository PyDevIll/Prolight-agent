"""Workflow-execution tools for ProLight-agent (Phase 4b).

Follow a learned, machine-readable workflow program
(``workflows/<task>.workflow.json``) step by step. Each step resolves against a
live window state via the app profile + router and is acted on deterministically
(no LLM); a step that cannot be resolved is returned with its live state so the
agent can fall back to perception/action and then resume.

Tools:
  - list_workflow_programs / load_workflow_program / save_workflow_program
  - start_workflow / run_workflow_step / workflow_status / workflow_reset

Backed by ``lib/workflow_runner.py``. See ``system_prompts/fragments/workflow.md``.
"""

import json

from loguru import logger

from lib import workflow_runner


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


async def list_workflow_programs() -> str:
    """List the saved executable workflow programs (``*.workflow.json``)."""
    return _dump({"ok": True, "programs": workflow_runner.list_programs()})


async def load_workflow_program(name: str) -> str:
    """Load a workflow program by key/file/path (drafts included), validated.

    Args:
        name: program key (e.g. "vpn connect then open youtube") or a path.
    """
    program = workflow_runner.load_program(name)
    if program is None:
        return _dump({"ok": False, "error": f"no workflow program {name!r}",
                      "available": workflow_runner.list_programs()})
    return _dump({"ok": True, "key": program.get("key"), "program": program,
                  "problems": workflow_runner.validate_program(program)})


async def save_workflow_program(name: str, program: str = "", program_json: str = "") -> str:
    """Create or replace a workflow program (``workflows/<name>.workflow.json``).

    Args:
        name: program key / task name.
        program: the program object (dict) or a JSON string.
        program_json: JSON string alternative (when ``program`` is not used).
    """
    data = program or program_json
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as e:
            return _dump({"ok": False, "error": f"invalid JSON: {e}"})
    if not isinstance(data, dict):
        return _dump({"ok": False, "error": "program must be a JSON object"})
    try:
        path, problems = workflow_runner.save_program(name, data)
    except Exception as e:
        return _dump({"ok": False, "error": str(e)})
    return _dump({"ok": True, "key": data.get("key"), "path": str(path), "problems": problems})


async def start_workflow(name: str, from_step: str = "", confirm: bool = False,
                         allow_draft: bool = False) -> str:
    """Load a program and (re)start its run at the first step (or ``from_step``).

    A **network-disruptive** program (e.g. a VPN) returns
    ``requires_confirmation: true`` and does **not** start until ``confirm=true``
    — tell the user first (the run can be resumed if the connection drops). A
    ``_draft_*`` program needs ``allow_draft=true`` (better: refine and
    ``save_workflow_program`` first).

    Args:
        name: program key / task name.
        from_step: optional step id to start from (e.g. "s5").
        confirm: acknowledge a network-disruptive workflow.
        allow_draft: run a ``_draft_*`` program as-is.
    """
    return _dump(workflow_runner.start(name, from_step=from_step,
                                       confirm=confirm, allow_draft=allow_draft))


async def run_workflow_step(name: str = "") -> str:
    """Execute the current step of the run, advancing on success.

    Deterministic: the step's expected state and named control are resolved
    against a fresh snapshot. Returns ``needs_llm: true`` + the live state when
    a step cannot be resolved — then act yourself and call this again to resume.

    Args:
        name: optional program key (auto-starts a run when none is active).
    """
    result = await workflow_runner.run_step(name=name)
    return _dump(result)


async def workflow_status() -> str:
    """Show the current workflow run: key, progress, current step and log tail."""
    return _dump(workflow_runner.status())


async def workflow_reset() -> str:
    """Forget the current workflow run (does not touch the program file)."""
    return _dump(workflow_runner.reset())


GROUP = "workflow"


TOOL_DEFINITIONS = [
    (
        "list_workflow_programs",
        list_workflow_programs,
        "List the saved executable workflow programs (workflows/*.workflow.json).",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "load_workflow_program",
        load_workflow_program,
        "Load a workflow program by key/file/path (drafts included) and validate it.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Program key or path"}},
            "required": ["name"],
        },
    ),
    (
        "save_workflow_program",
        save_workflow_program,
        "Create or replace a workflow program (workflows/<name>.workflow.json): "
        "an ordered list of steps {id, app, state?, control?, action} referencing "
        "apps/controls by name (never pixels).",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Program key / task name"},
                "program": {"type": "string", "description": "Program object (dict) or JSON string"},
                "program_json": {"type": "string", "description": "JSON string alternative"},
            },
            "required": ["name"],
        },
    ),
    (
        "start_workflow",
        start_workflow,
        "Load a workflow program and start its run (returns the ordered steps and "
        "the current step). Then call run_workflow_step repeatedly. A "
        "network-disruptive program (VPN) requires confirm=true; a _draft_* "
        "program requires allow_draft=true.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Program key / task name"},
                "from_step": {"type": "string", "description": "Optional step id to start from"},
                "confirm": {"type": "boolean", "description": "Acknowledge a network-disruptive workflow (default false)"},
                "allow_draft": {"type": "boolean", "description": "Run a _draft_* program as-is (default false)"},
            },
            "required": ["name"],
        },
    ),
    (
        "run_workflow_step",
        run_workflow_step,
        "Execute the current workflow step deterministically (no LLM): resolves "
        "the step's app state and named control against a fresh snapshot and acts. "
        "Returns needs_llm:true + the live state when a step cannot be resolved — "
        "act yourself, then call again to resume.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Optional program key (auto-start)"}},
            "required": [],
        },
    ),
    (
        "workflow_status",
        workflow_status,
        "Show the current workflow run: key, progress (index/total), current step "
        "and recent log entries.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "workflow_reset",
        workflow_reset,
        "Forget the current workflow run (leaves the program file untouched).",
        {"type": "object", "properties": {}, "required": []},
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params, group=GROUP)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} workflow tool(s)")
