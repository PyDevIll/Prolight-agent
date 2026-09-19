import asyncio

from lib.console import force_utf8_console

# Must run before anything prints/logs (including import-time logging).
force_utf8_console()

from dotenv import load_dotenv  # noqa: E402

import app  # noqa: E402


async def main() -> None:
    await app.start_app()


if __name__ == "__main__":
    load_dotenv()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # asyncio.run re-raises KeyboardInterrupt after cancelling tasks; catch
        # it here so Ctrl+C exits cleanly instead of printing a traceback.
        print()