
import os, requests
from typing import List, Dict
from .base import Strategy

def _to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)

class DexScreenerStrategy(Strategy):
    name = "dexscreener"

    def __init__(self):
        self.min_liq = _to_float(os.getenv("STRAT_LIQ_MIN", "130000"))
        self.max_fdv = _to_float(os.getenv("STRAT_FDV_MAX", "400000"))
        self.min_vol5m = _to_float(os.getenv("STRAT_VOL5M_MIN", "20000"))
        self.chain = (os.getenv("STRAT_CHAIN", "solana") or "solana").lower()
        self.endpoint = os.getenv(
            "DEXS_ENDPOINT",
            "https://api.dexscreener.com/latest/dex/search?q=SOL",
        )
        self.timeout = int(os.getenv("HTTP_TIMEOUT", "15"))
        self.max_items = int(os.getenv("STRAT_MAX_ITEMS", "200"))

    def fetch_candidates(self) -> List[Dict]:
        r = requests.get(self.endpoint, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        pairs = []
        if isinstance(data, dict) and isinstance(data.get("pairs"), list):
            pairs = data["pairs"]
        elif isinstance(data, list):
            pairs = data
        return pairs[: self.max_items]

    def filter_candidates(self, pairs: List[Dict]) -> List[Dict]:
        out: List[Dict] = []
        for p in pairs:
            base = (p.get("baseToken") or {})
            liq_usd = _to_float(((p.get("liquidity") or {}).get("usd", 0)))
            fdv = _to_float(p.get("fdv", 0))
            vol5m = _to_float(((p.get("volume") or {}).get("m5", 0)))
            price = _to_float(p.get("priceUsd", 0))
            pair_addr = p.get("pairAddress")
            token_addr = base.get("address")
            symbol = base.get("symbol")

            if (
                liq_usd >= self.min_liq
                and 0 < fdv <= self.max_fdv
                and vol5m >= self.min_vol5m
                and token_addr
            ):
                out.append({
                    "strategy": self.name,
                    "symbol": symbol,
                    "address": token_addr,
                    "pair": pair_addr,
                    "liq_usd": liq_usd,
                    "fdv": fdv,
                    "vol5m": vol5m,
                    "price": price,
                    "source": "dexscreener"
                })
        return out
