import asyncio
import logging
import signal
import sys

from src.config import validate_env, AUTO_START
from src.bot import start_bot
from src.sniper import start_sniper, stop_sniper
from src.utils.logger import setup_logger

logger = logging.getLogger("main")


async def main():
    validate_env()
    setup_logger()

    loop = asyncio.get_event_loop()

    async def _graceful_stop(sig):
        logger.info(f"⚠️ Signal {sig.name} empfangen – Stoppe Bot & Sniper...")
        await stop_sniper()
        logger.info("✅ Shutdown abgeschlossen.")
        sys.exit(0)

    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            s, lambda s=s: asyncio.create_task(_graceful_stop(s))
        )

    tasks = [asyncio.create_task(start_bot())]

    if AUTO_START:
        tasks.append(asyncio.create_task(start_sniper()))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"❌ Unerwarteter Fehler: {e}", exc_info=True)
        sys.exit(1)
