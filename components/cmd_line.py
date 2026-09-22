import asyncio
from app import get_agent
from lib import overlay


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
