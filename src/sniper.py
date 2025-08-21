import asyncio
import aiohttp
import time
from src.config import (
    MIN_LIQUIDITY,
    MAX_FDV,
    SCAN_INTERVAL,
    MAX_CONCURRENT_SCANS,
    MAX_PAIR_AGE_SEC,
)
from src.trading import execute_buy, execute_sell
from src.utils.logger import logger
from src.rugcheck import check_rug
from src.gmgn import check_gmgn

class Sniper:
    def __init__(self):
        self._running = False
        self._session = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_SCANS)

    async def _fetch_pairs(self):
        url = "https://api.dexscreener.com/latest/dex/search?q=SOL"
        try:
            async with self._session.get(url, timeout=10) as r:
                data = await r.json()
                return data.get("pairs", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.warning(f"❌ Fehler bei fetch_pairs: {e}")
            return []

    async def _process_pair(self, pair):
        async with self._semaphore:
            try:
                liq = pair.get("liquidity", {}).get("usd", 0)
                fdv = pair.get("fdv", 0)
                created_at = pair.get("pairCreatedAt", 0) / 1000
                age_sec = time.time() - created_at

                if liq < MIN_LIQUIDITY or fdv > MAX_FDV:
                    return

                if created_at and age_sec > MAX_PAIR_AGE_SEC:
                    logger.info(f"⏩ Skip alter Token {pair.get('baseToken', {}).get('symbol')} age={age_sec:.0f}s")
                    return

                if not await check_rug(pair):
                    return

                if not await check_gmgn(pair):
                    return

                token_address = pair["baseToken"]["address"]
                symbol = pair["baseToken"]["symbol"]

                logger.info(f"🚀 Neuer Token entdeckt: {symbol} ({token_address})")
                await execute_buy(token_address, symbol)
                await execute_sell(token_address, symbol)

            except Exception as e:
                logger.error(f"Fehler in process_pair: {e}")

    async def _scan_loop(self):
        while self._running:
            pairs = await self._fetch_pairs()
            if not isinstance(pairs, list):
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            tasks = [self._process_pair(p) for p in pairs]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(SCAN_INTERVAL)

    async def start(self):
        self._running = True
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=20)) as session:
            self._session = session
            logger.info("✅ Sniper gestartet.")
            await self._scan_loop()

    async def stop(self):
        self._running = False
        logger.info("🛑 Sniper gestoppt.")
