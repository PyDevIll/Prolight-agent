import asyncio
from app import get_agent
from lib import overlay


async def process_compress() -> None:
    """Handle the manual `/compress` console command (force compression)."""
    overlay.set_status("compressing", "thinking")
    overlay.clear_toast()
    agent = get_agent()
    print(" > compressing context...", flush=True)
    result = None
    try:
        result = await agent.compress_context(force=True)
    finally:
        # Must not raise here: an exception in finally would mask a
        # CancelledError from /q and leave the worker task alive.
        overlay.set_status("done", "done")
    if result:
        overlay.notify("Context compressed", "done", duration=3.0)
        print(" > context compressed.", flush=True)
    else:
        overlay.notify("Nothing to compress", "done", duration=3.0)
        print(" > nothing to compress (not enough history, or helper unavailable).", flush=True)


async def process_command_prompt(text: str) -> None:
    async def reasoning_callback(thought):
        print(" > ...", thought, flush=True)

    overlay.set_status("thinking", "thinking")
    overlay.clear_toast()
    agent = get_agent()
    try:
        response_text = await agent.run_with_crash_recovery(
            initial_user_request="[Command prompt]: \n" + text,
            reasoning_callback=reasoning_callback
        )
    finally:
        overlay.set_status("done", "done")
        overlay.notify("Task complete", "done", duration=4.0)
    print(" >", response_text, flush=True)
