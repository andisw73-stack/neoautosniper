# bot.py – NeoAutoSniper (Scan + Debug, Solana focus)
# Multi-Source Scan, relaxter Chain-Filter ("sol" in chainId), optional Quote=SOL

import os
import time
import signal
import traceback
import requests
from collections import Counter

# ----------------------- Helpers -----------------------
def _to_int_env(key: str, default: int) -> int:
    """Read env as int, tolerant to spaces/newlines etc."""
    val = os.getenv(key, str(default))
    digits = "".join(ch for ch in str(val) if ch.isdigit() or ch == "-")
    try:
        return int(digits) if digits not in ("", "-", None) else int(default)
    except Exception:
        return int(default)

def _to_int(val, default=0) -> int:
    """Convert common DexScreener numeric fields to int safely."""
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return int(val)
        s = str(val).replace(",", "").strip()
        digits = "".join(ch for ch in s if (ch.isdigit() or ch in ".-"))
        return int(float(digits)) if digits not in ("", "-", ".", "-.", "") else default
    except Exception:
        return default

def _get_quote_symbol(p) -> str:
    qt = p.get("quoteToken")
    if isinstance(qt, dict):
        return (qt.get("symbol") or "").upper()
    if isinstance(qt, str):
        return qt.upper()
    return ""

def _get_base_symbol(p) -> str:
    bt = p.get("baseToken")
    if isinstance(bt, dict):
        return (bt.get("symbol") or "?").upper()
    if isinstance(bt, str):
        return bt.upper()
    return "?"

def _pair_id(p):
    return p.get("pairAddress") or p.get("pairId") or p.get("url")

def _chain_of(p) -> str:
    return (p.get("chainId") or p.get("chain") or "").lower()

# ----------------------- ENV / Defaults -----------------------
CHAIN             = os.getenv("STRAT_CHAIN", "solana").lower()  # Ziel-Chain (nur Info/Logging)
QUOTE             = os.getenv("STRAT_QUOTE", "SOL").upper()     # gewünschtes Quote-Asset
STRICT_QUOTE      = os.getenv("STRICT_QUOTE", "0") == "1"       # 1 => zwingend QUOTE verlangen

# Fallback-Suche (wenn Hauptquellen leer sind)
ENDPOINT_FALLBACK = os.getenv("DEXS_ENDPOINT", "https://api.dexscreener.com/latest/dex/search?q=sol")

SCAN_INTERVAL     = _to_int_env("SCAN_INTERVAL", 30)
TIMEOUT           = _to_int_env("HTTP_TIMEOUT", 15)

# etwas entspannte Defaults zum Testen
LIQ_MIN           = _to_int_env("STRAT_LIQ_MIN", 50000)      # 50k
FDV_MAX           = _to_int_env("STRAT_FDV_MAX", 2000000)    # 2m
VOL5M_MIN         = _to_int_env("STRAT_VOL5M_MIN", 5000)     # 5k

DRY_RUN           = os.getenv("DRY_RUN", "1") == "1"

print("NeoAutoSniper boot OK")
print(f"Settings: chain={CHAIN} quote={QUOTE} STRICT_QUOTE={STRICT_QUOTE} "
      f"LIQ_MIN={LIQ_MIN} FDV_MAX={FDV_MAX} VOL5M_MIN={VOL5M_MIN} DRY_RUN={DRY_RUN}")

# ----------------------- Scan sources -----------------------
SOURCES = [
    # dieser Endpunkt liefert für solana oft 404 – wir loggen es, schadet nicht
    lambda: f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN}",
    # gezielte Suchbegriffe, um SOL-Pools zu erwischen
    lambda: "https://api.dexscreener.com/latest/dex/search?q=SOL/USDC",
    lambda: "https://api.dexscreener.com/latest/dex/search?q=SOL/SOL",
    lambda: "https://api.dexscreener.com/latest/dex/search?q=SOL",
    lambda: "https://api.dexscreener.com/latest/dex/search?q=solana",
]

def _fetch_pairs(url: str):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[SCAN] {url} -> HTTP {r.status_code}")
            return []
        j = r.json()
        return j.get("pairs", []) or j.get("result", []) or []
    except Exception as e:
        print(f"[SCAN] error on {url}: {e}")
        return []

# ----------------------- Scanner -----------------------
def scan_market():
    """Collect from multiple sources, dedupe, debug-print, then filter and rank."""
    try:
        all_pairs = []
        seen = set()
        raw_total = 0

        # 1) einsammeln & deduplizieren
        for make_url in SOURCES:
            url = make_url()
            pairs = _fetch_pairs(url)
            raw_total += len(pairs)
            for p in pairs:
                pid = _pair_id(p)
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                all_pairs.append(p)

        # Fallback-Quelle, falls oben nichts kam
        if not all_pairs and ENDPOINT_FALLBACK:
            pairs = _fetch_pairs(ENDPOINT_FALLBACK)
            raw_total += len(pairs)
            for p in pairs:
                pid = _pair_id(p)
                if pid and pid not in seen:
                    seen.add(pid)
                    all_pairs.append(p)

        print(f"[SCAN] collected {len(all_pairs)} unique pairs from {raw_total} raw results ({len(SOURCES)}+fallback sources)")

        # 2) Debug: zeig die ersten 8 Roh-Paare & eine Chain-Verteilung
        if all_pairs:
            print("[DEBUG] first 8 raw pairs:")
            for p in all_pairs[:8]:
                print("  symbol:", _get_base_symbol(p),
                      "quote:", _get_quote_symbol(p),
                      "chain:", _chain_of(p),
                      "liq:", (p.get("liquidity") or {}).get("usd"),
                      "fdv:", p.get("fdv"),
                      "m5:",  (p.get("volume") or {}).get("m5"),
                      "url:", p.get("url"))
            counts = Counter(_chain_of(p) or "unknown" for p in all_pairs)
            print("[DEBUG] chain distribution:", dict(counts))

        # 3) Filter-Stufe 1: relaxter Chain-Filter – alles, wo chainId 'sol' enthält
        chain_only = [p for p in all_pairs if "sol" in _chain_of(p)]
        print(f"[SCAN] after relaxed-chain filter: {len(chain_only)} pairs (contains 'sol')")

        # 4) Filter-Stufe 2 (optional): nur Quote = SOL
        if STRICT_QUOTE:
            filtered = [p for p in chain_only if _get_quote_symbol(p) == QUOTE]
            print(f"[SCAN] after quote filter: {len(filtered)} pairs (quote={QUOTE})")
        else:
            filtered = chain_only
            print(f"[SCAN] quote filter disabled (STRICT_QUOTE=0) -> using {len(filtered)} pairs")

        # 5) Strategie-Filter
        hits = []
        for p in filtered:
            liq   = _to_int((p.get("liquidity") or {}).get("usd", 0))
            fdv   = _to_int(p.get("fdv", 0))
            vol5m = _to_int((p.get("volume") or {}).get("m5", 0))

            if (liq >= LIQ_MIN) and (0 < fdv <= FDV_MAX) and (vol5m >= VOL5M_MIN):
                base = _get_base_symbol(p)
                url  = p.get("url") or ""
                hits.append((base, liq, fdv, vol5m, url, _get_quote_symbol(p)))

        # 6) Ausgabe
        if hits:
            hits.sort(key=lambda x: (-x[1], x[2]))  # viel Liq, kleine FDV zuerst
            print(f"[HITS] {len(hits)} match(es) (top 5):")
            for base, liq, fdv, v5, url, q in hits[:5]:
                print(f"  • {base}/{q} | liq ${liq:,} | fdv ${fdv:,} | v5m {v5} | {url}")
        else:
            print("[HITS] none matching filters")

        if DRY_RUN:
            print("[MODE] DRY_RUN active — no buys.")

    except Exception as e:
        print("[ERR] during scan:", e)
        traceback.print_exc()

# ----------------------- Main loop -----------------------
_running = True
def _handle_sig(sig, frame):
    global _running
    print(f"[signal] received {sig}, shutting down ...")
    _running = False

try:
    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)
except Exception:
    pass

if __name__ == "__main__":
    while _running:
        print("Heartbeat: service alive (DRY_RUN may be on).")
        scan_market()
        time.sleep(SCAN_INTERVAL)
