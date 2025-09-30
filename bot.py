# bot.py – NeoAutoSniper (Scan-only, Solana / Quote=SOL)
# Stand: stabil – Multi-Source-Scan + Strategie-Filter, kein Auto-Buy

import os
import time
import signal
import traceback
import requests

# ----------------------------------------------------------
# Helpers
# ----------------------------------------------------------
def _to_int_env(key: str, default: int) -> int:
    """Read env as int, tolerating spaces/newlines etc."""
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
        # cut any trailing junk
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

# ----------------------------------------------------------
# ENV / Defaults (alle Zeilen beginnen in Spalte 0)
# ----------------------------------------------------------
CHAIN         = os.getenv("STRAT_CHAIN", "solana").lower()   # wir filtern nur diese Chain
QUOTE         = os.getenv("STRAT_QUOTE", "SOL").upper()      # nur Paare gegen SOL
ENDPOINT_FALLBACK = os.getenv("DEXS_ENDPOINT", "https://api.dexscreener.com/latest/dex/search?q=SOL")

SCAN_INTERVAL = _to_int_env("SCAN_INTERVAL", 30)
TIMEOUT       = _to_int_env("HTTP_TIMEOUT", 15)

LIQ_MIN       = _to_int_env("STRAT_LIQ_MIN", 130000)
FDV_MAX       = _to_int_env("STRAT_FDV_MAX", 400000)
VOL5M_MIN     = _to_int_env("STRAT_VOL5M_MIN", 20000)

DRY_RUN       = os.getenv("DRY_RUN", "1") == "1"

print("NeoAutoSniper boot OK")
print(f"Settings: chain={CHAIN} quote={QUOTE}  LIQ_MIN={LIQ_MIN}  FDV_MAX={FDV_MAX}  VOL5M_MIN={VOL5M_MIN}  DRY_RUN={DRY_RUN}")

# ----------------------------------------------------------
# Scan logic
# ----------------------------------------------------------
SOURCES = [
    # viele Paare der Chain
    lambda: f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN}",
    # ergänzende Suchen (liefert oft ~30 je Suche)
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

def scan_market():
    """Collect from multiple sources, dedupe, keep only Solana/SOL pairs, apply strategy filters."""
    try:
        all_pairs = []
        seen = set()
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

        # Fallback-Quelle, falls obige nichts liefern
        if not all_pairs and ENDPOINT_FALLBACK:
            pairs = _fetch_pairs(ENDPOINT_FALLBACK)
            raw_total += len(pairs)
            for p in pairs:
                pid = _pair_id(p)
                if pid and pid not in seen:
                    seen.add(pid)
                    all_pairs.append(p)

        print(f"[SCAN] collected {len(all_pairs)} unique pairs from {raw_total} raw results ({len(SOURCES)}+fallback sources)")

        # Filter: nur unsere Chain + nur Quote = SOL
        filtered = []
        for p in all_pairs:
            chain_id = (p.get("chainId") or p.get("chain") or "").lower()
            if chain_id != CHAIN:
                continue
            if _get_quote_symbol(p) != QUOTE:
                continue
            filtered.append(p)

        print(f"[SCAN] after chain+quote filter: {len(filtered)} pairs (chain={CHAIN}, quote={QUOTE})")

        # Strategie-Filter
        hits = []
        for p in filtered:
            liq = _to_int((p.get("liquidity") or {}).get("usd", 0))
            fdv = _to_int(p.get("fdv", 0))
            vol5m = _to_int((p.get("volume") or {}).get("m5", 0))

            if (liq >= LIQ_MIN) and (0 < fdv <= FDV_MAX) and (vol5m >= VOL5M_MIN):
                base = _get_base_symbol(p)
                url = p.get("url") or ""
                hits.append((base, liq, fdv, vol5m, url))

        if hits:
            # sortiere: höchste Liquidity zuerst, kleinere FDV bevorzugt
            hits.sort(key=lambda x: (-x[1], x[2]))
            print(f"[HITS] {len(hits)} match(es) (top 5):")
            for base, liq, fdv, v5, url in hits[:5]:
                print(f"  • {base}/SOL | liq ${liq:,} | fdv ${fdv:,} | v5m {v5} | {url}")
        else:
            print("[HITS] none matching filters")

        if DRY_RUN:
            print("[MODE] DRY_RUN active — no buys.")

    except Exception as e:
        print("[ERR] during scan:", e)
        traceback.print_exc()

# ----------------------------------------------------------
# Main loop with graceful shutdown
# ----------------------------------------------------------
_running = True
def _handle_sig(sig, frame):
    global _running
    print(f"[signal] received {sig}, shutting down ...")
    _running = False

try:
    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)
except Exception:
    pass  # not fatal on some platforms

if __name__ == "__main__":
    while _running:
        print("Heartbeat: service alive (DRY_RUN may be on).")
        scan_market()
        # kurze Sleep-Intervalle können API-Rate-Limits triggern – 30s ist ein guter Startwert
        time.sleep(SCAN_INTERVAL)
