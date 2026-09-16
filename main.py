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
    asyncio.run(main())