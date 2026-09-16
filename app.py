import os
import sys
import asyncio
from pathlib import Path

from loguru import logger

from agent import Agent
from builtin_tools import register_all as register_builtin_tools
from tool_registry import get_registry

# Data directory (screenshots, logs, context saves)
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

request_queue = None
agent = None


def get_request_queue():
    return request_queue


def get_agent():
    return agent


# ---- Worker coroutine (processes requests sequentially) ----
async def worker() -> None:
    from components.cmd_line import process_command_prompt

    while True:
        logger.debug(f"Worker queue count: {request_queue.qsize()}")

        req = await request_queue.get()
        try:
            if req["type"] == "user":
                await process_command_prompt(req["prompt"])
            else:
                logger.warning(f"Unknown request type: {req.get('type')}")
        except Exception as e:
            logger.exception(f"Worker failed processing {req.get('type')}: {e}")
        finally:
            request_queue.task_done()


# ---- Console command loop ----
async def get_command() -> None:
    while True:
        try:
            user_request = await asyncio.to_thread(input, "ProLight> ")
        except (EOFError, KeyboardInterrupt):
            logger.info("Console input closed — stopping command loop")
            return

        user_request = (user_request or "").strip()
        if not user_request:
            continue
        if user_request in ("/q", "/quit", "/exit"):
            logger.info("Quit requested")
            return

        await request_queue.put({"type": "user", "prompt": user_request})


# --------- APP ENTRY POINT -----------
async def start_app() -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    logger.add(
        DATA_DIR / "agent.log",
        rotation="1 MB",
        retention="7 days",
        level="DEBUG",
    )

    logger.info("ProLight-agent starting...")

    # Global queue for incoming requests
    global request_queue
    request_queue = asyncio.Queue()

    # Initialize tool registry with builtin tools
    registry = get_registry()
    register_builtin_tools(registry)
    logger.info(f"Registered {len(registry.tool_names)} tools: {registry.tool_names}")

    # Helper agent for context compression
    helper_agent = Agent(
        name="HELPER",
        system_prompt="""## Values:
    - Meaning: Retain core semantic content. Highest priority.
    - Relevance: Extract only information relevant to the given task.
    - Fidelity: Accurately reproduce key elements.
    - Concise: Return only processed content. No questions.""",
        use_tools=False,
        save_history=False,
    )

    # Main agent
    global agent
    agent = Agent(
        base_prompts=[
            ("## **IDENTITY**\n", "system_prompts/core.md"),
            ("\n## **WINDOWS DESKTOP**\n", "system_prompts/desktop.md"),
            ("\n## **Tools Guidelines & Best Practices**\n", "system_prompts/tools_guidelines.md"),
            ("\n## **Learning**\n", "system_prompts/learning.md"),
        ],
        last_memory=[
            ("# **PREVIOUS MEMORY SUMMARY**\n", "data/last_compression.txt"),
        ],
        use_tools=True,
        save_history=True,
    )
    agent.add_helper_agent(helper_agent)

    logger.info("Starting worker and console command loop...")
    worker_task = asyncio.create_task(worker())
    command_task = asyncio.create_task(get_command())

    logger.info("All components started. Awaiting tasks...")
    await asyncio.gather(worker_task, command_task)
