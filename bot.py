# bot.py – NeoAutoSniper (Solana focus)
# Multi-Source Scan, relaxter Chain-Filter ("sol" in chainId),
# optional Quote=SOL, best-of volume (m5/m15/h1), optional Max-Age-Filter.

import os
import time
import signal
import traceback
import requests
from collections import Counter

# ----------------------- Helpers -----------------------
def _to_int_env(key: str, default: int) -> int:
    val = os.getenv(key, str(default))
    digits = "".join(ch for ch in str(val) if ch.isdigit() or ch == "-")
    try:
        return int(digits) if digits not in ("", "-", None) else int(default)
    except Exception:
        return int(default)

def _to_int(val, default=0) -> int:
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

def _best_vol(p) -> int:
    vol = p.get("volume") or {}
    m5  = _to_int(vol.get("m5", 0))
    m15 = _to_int(vol.get("m15", 0))
    h1  = _to_int(vol.get("h1", 0))
    return max(m5, m15, h1)

def _age_minutes(p):
    # Dexscreener liefert häufig pairCreatedAt (ms). Wenn nicht vorhanden -> None (kein Age-Filter).
    ts = p.get("pairCreatedAt")
    try:
        if ts is None:
            return None
        now_ms = int(time.time() * 1000)
        age_min = max(0, (now_ms - int(ts)) // 60000)
        return int(age_min)
    except Exception:
        return None

# ----------------------- ENV / Defaults -----------------------
CHAIN             = os.getenv("STRAT_CHAIN", "solana").lower()
QUOTE             = os.getenv("STRAT_QUOTE", "SOL").upper()
STRICT_QUOTE      = os.getenv("STRICT_QUOTE", "1") == "1"     # jetzt default: nur /SOL
ENDPOINT_FALLBACK = os.getenv("DEXS_ENDPOINT", "https://api.dexscreener.com/latest/dex/search?q=sol")

SCAN_INTERVAL     = _to_int_env("SCAN_INTERVAL", 30)
TIMEOUT           = _to_int_env("HTTP_TIMEOUT", 15)

LIQ_MIN           = _to_int_env("STRAT_LIQ_MIN", 130000)
FDV_MAX           = _to_int_env("STRAT_FDV_MAX", 1000000)
VOL_BEST_MIN      = _to_int_env("STRAT_VOL_BEST_MIN", 5000)   # best-of (m5/m15/h1)
MAX_AGE_MIN       = _to_int_env("MAX_AGE_MIN", 240)           # 0/negativ => kein Age-Filter

DRY_RUN           = os.getenv("DRY_RUN", "1") == "1"

print("NeoAutoSniper boot OK")
print(f"Settings: chain={CHAIN} quote={QUOTE} STRICT_QUOTE={STRICT_QUOTE} "
      f"LIQ_MIN={LIQ_MIN} FDV_MAX={FDV_MAX} VOL_BEST_MIN={VOL_BEST_MIN} MAX_AGE_MIN={MAX_AGE_MIN} DRY_RUN={DRY_RUN}")

# ----------------------- Scan sources -----------------------
SOURCES = [
    lambda: f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN}",  # kann 404 sein, ist ok
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
    try:
        # 1) Einsammeln + Dedupe
        all_pairs, seen = [], set()
        raw_total = 0
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

        if not all_pairs and ENDPOINT_FALLBACK:
            pairs = _fetch_pairs(ENDPOINT_FALLBACK)
            raw_total += len(pairs)
            for p in pairs:
                pid = _pair_id(p)
                if pid and pid not in seen:
                    seen.add(pid)
                    all_pairs.append(p)

        print(f"[SCAN] collected {len(all_pairs)} unique pairs from {raw_total} raw results ({len(SOURCES)}+fallback sources)")

        if all_pairs:
            print("[DEBUG] first 8 raw pairs:")
            for p in all_pairs[:8]:
                print("  symbol:", _get_base_symbol(p),
                      "quote:", _get_quote_symbol(p),
                      "chain:", _chain_of(p),
                      "liq:", (p.get("liquidity") or {}).get("usd"),
                      "fdv:", p.get("fdv"),
                      "bestVol:", _best_vol(p),
                      "ageMin:", _age_minutes(p),
                      "url:", p.get("url"))
            counts = Counter(_chain_of(p) or "unknown" for p in all_pairs)
            print("[DEBUG] chain distribution:", dict(counts))

        # 2) Relaxter Chain-Filter: alles, wo chainId "sol" enthält
        chain_only = [p for p in all_pairs if "sol" in _chain_of(p)]
        print(f"[SCAN] after relaxed-chain filter: {len(chain_only)} pairs (contains 'sol')")

        # 3) Optional: nur SOL-Quote
        if STRICT_QUOTE:
            filtered = [p for p in chain_only if _get_quote_symbol(p) == QUOTE]
            print(f"[SCAN] after quote filter: {len(filtered)} pairs (quote={QUOTE})")
        else:
            filtered = chain_only
            print(f"[SCAN] quote filter disabled (STRICT_QUOTE=0) -> using {len(filtered)} pairs")

        # 4) Strategie-Filter (inkl. Max Age + best-of Volume)
        hits = []
        for p in filtered:
            liq = _to_int((p.get("liquidity") or {}).get("usd", 0))
            fdv = _to_int(p.get("fdv", 0))
            vol_best = _best_vol(p)
            age_min = _age_minutes(p)

            if liq < LIQ_MIN:
                continue
            if not (0 < fdv <= FDV_MAX):
                continue
            if vol_best < VOL_BEST_MIN:
                continue
            if MAX_AGE_MIN > 0 and age_min is not None and age_min > MAX_AGE_MIN:
                continue

            base = _get_base_symbol(p)
            url  = p.get("url") or ""
            hits.append((base, _get_quote_symbol(p), liq, fdv, vol_best, age_min, url))

        if hits:
            hits.sort(key=lambda x: (-x[2], x[3]))  # Liq desc, FDV asc
            print(f"[HITS] {len(hits)} match(es) (top 5):")
            for base, q, liq, fdv, vb, age, url in hits[:5]:
                age_txt = f"{age}m" if age is not None else "n/a"
                print(f"  • {base}/{q} | liq ${liq:,} | fdv ${fdv:,} | vol* {vb} | age {age_txt} | {url}")
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
