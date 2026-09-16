import asyncio

from dotenv import load_dotenv

import app


async def main() -> None:
    await app.start_app()


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
