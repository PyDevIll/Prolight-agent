import os
import sys
import asyncio
import threading
from pathlib import Path
from typing import Optional

from loguru import logger

from agent import Agent
from builtin_tools import register_all as register_builtin_tools
from tool_registry import get_registry
from lib.console import force_utf8_console
from lib import overlay

# Data directory (screenshots, logs, context saves)
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

request_queue = None
agent = None
# Set while the agent is waiting for the user's answer to an ask_user question;
# the console loop then routes the next line to this future instead of enqueuing
# a new request.
_pending_question: Optional[asyncio.Future] = None


def get_request_queue():
    return request_queue


def get_agent():
    return agent


async def ask_user_question(question: str, options=None, timeout: float = 300.0) -> Optional[str]:
    """Ask the user a question on the console and await their reply.

    Blocks the calling (worker) coroutine until the user answers or ``timeout``
    elapses. Returns the answer text, or None on timeout / no console.
    """
    global _pending_question
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _pending_question = fut
    overlay.set_status("waiting for you", "waiting")
    overlay.notify("Waiting for your input", "waiting", sticky=True)
    print("\n[AGENT ASKS] " + str(question), flush=True)
    if options:
        print("  options: " + " | ".join(str(o) for o in options), flush=True)
    try:
        return await asyncio.wait_for(fut, timeout=float(timeout))
    except asyncio.TimeoutError:
        logger.warning("ask_user_question timed out")
        return None
    finally:
        if _pending_question is fut:
            _pending_question = None
        overlay.clear_toast()
        overlay.set_status("thinking", "thinking")


# ---- Worker coroutine (processes requests sequentially) ----
async def worker() -> None:
    from components.cmd_line import process_command_prompt, process_compress

    while True:
        logger.debug(f"Worker queue count: {request_queue.qsize()}")

        req = await request_queue.get()
        try:
            if req["type"] == "user":
                await process_command_prompt(req["prompt"])
            elif req["type"] == "compress":
                await process_compress()
            else:
                logger.warning(f"Unknown request type: {req.get('type')}")
        except Exception as e:
            logger.exception(f"Worker failed processing {req.get('type')}: {e}")
        finally:
            request_queue.task_done()


# ---- Console command loop ----
async def get_command() -> None:
    # Read stdin on a daemon thread, from a *dup* of fd 0 rather than sys.stdin:
    #   * asyncio.to_thread(input, ...) hangs shutdown — asyncio.run() joins the
    #     default executor, and a thread blocked in input() never returns;
    #   * a daemon thread using input() crashes finalization ("_enter_buffered_
    #     busy ... due to daemon threads") by holding sys.stdin's buffer lock.
    # A dup'd fd has its own buffer, so sys.stdin stays untouched, the daemon
    # thread is abandoned on exit, and Ctrl+C reaches the main thread cleanly.
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def _reader() -> None:
        try:
            fin = os.fdopen(os.dup(0), "r", encoding="utf-8", errors="replace")
        except OSError:
            fin = None
        while not stop.is_set():
            if fin is None:
                line = None
            else:
                try:
                    line = fin.readline()
                except (OSError, ValueError):
                    line = ""
                if line == "":
                    line = None
            try:
                loop.call_soon_threadsafe(lines.put_nowait, line)
            except RuntimeError:
                break
            if line is None:
                break

    threading.Thread(target=_reader, name="console-input", daemon=True).start()
    try:
        while True:
            sys.stdout.write("ProLight> ")
            sys.stdout.flush()
            user_request = await lines.get()
            if user_request is None:
                logger.info("Console input closed — stopping command loop")
                return

            user_request = user_request.strip()
            if not user_request:
                continue
            if user_request in ("/q", "/quit", "/exit"):
                logger.info("Quit requested")
                return
            if user_request == "/compress":
                # Manual, forced context compression — queued so it never races
                # a running task.
                logger.info("Compress requested")
                await request_queue.put({"type": "compress"})
                continue

            # A pending ask_user question consumes the next line as its answer.
            if _pending_question is not None and not _pending_question.done():
                _pending_question.set_result(user_request)
                continue

            await request_queue.put({"type": "user", "prompt": user_request})
    finally:
        stop.set()


# --------- APP ENTRY POINT -----------
async def start_app() -> None:
    # Ensure UTF-8 before any logging (handles Cyrillic titles/answers).
    force_utf8_console()

    # Trust the OS certificate store so HTTPS works behind a TLS-inspecting
    # proxy/endpoint protection (httpx defaults to certifi, which lacks its CA).
    # Must run before any SSLContext is created (i.e. before any Agent/client).
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception as e:  # pragma: no cover - env-dependent
        print(f"[warn] truststore unavailable, using default CA bundle: {e}")

    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    logger.add(
        DATA_DIR / "agent.log",
        rotation="1 MB",
        retention="7 days",
        level="DEBUG",
        encoding="utf-8",
    )

    logger.info("ProLight-agent starting...")

    # Global queue for incoming requests
    global request_queue
    request_queue = asyncio.Queue()

    # Initialize tool registry with builtin tools
    registry = get_registry()
    register_builtin_tools(registry)
    logger.info(f"Registered {len(registry.tool_names)} tools: {registry.tool_names}")

    # On-screen overlay: visible to the user but excluded from screen captures.
    logger.info(
        f"Overlay {'enabled' if overlay.is_enabled() else 'disabled'} "
        f"(set {overlay.OVERLAY_ENV}=0 to disable)"
    )
    _orig_call_tool = registry.call_tool

    async def _call_tool_with_overlay(tool_name, **kwargs):
        overlay.set_status(f"tool: {tool_name}", "tool")
        try:
            return await _orig_call_tool(tool_name, **kwargs)
        finally:
            overlay.set_status("thinking", "thinking")

    registry.call_tool = _call_tool_with_overlay

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

    # Main agent. Prompt fragments + tool groups are selected per run by the
    # deterministic instruction planner (lib/instruction_planner.py), so no
    # fixed base prompts are passed here.
    global agent
    agent = Agent(use_tools=True, save_history=True)
    agent.add_helper_agent(helper_agent)

    logger.info("Starting worker and console command loop...")
    worker_task = asyncio.create_task(worker())
    command_task = asyncio.create_task(get_command())

    logger.info("All components started. Awaiting tasks...")
    # worker() never returns on its own, so wait for whichever finishes first
    # (quit/EOF from the console, or an unexpected worker failure), then stop
    # the other one instead of hanging in gather().
    try:
        await asyncio.wait(
            {worker_task, command_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (worker_task, command_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(worker_task, command_task, return_exceptions=True)
        # Inside the finally so it also runs when the task is cancelled (Ctrl+C).
        logger.info("ProLight-agent stopped.")
