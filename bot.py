# bot.py  – NeoAutoSniper Minimal-Runner
# Stand: 30.09.2025 – stabile Basis für Solana/DexScreener

import os
import time
import traceback
import requests

# ----------------------------------------------------------
# Helfer zum sicheren Auslesen von Zahlen aus ENV
# ----------------------------------------------------------
def _to_int_env(key: str, default: int) -> int:
    val = os.getenv(key, str(default))
    # Entfernt evtl. \n oder nicht-numerische Zeichen
    digits = "".join(ch for ch in str(val) if ch.isdigit() or ch == "-")
    try:
        return int(digits) if digits not in ("", "-", None) else int(default)
    except Exception:
        return int(default)

# ----------------------------------------------------------
# ENV Variablen / Defaults
# (ALLE Zeilen beginnen in Spalte 0)
# ----------------------------------------------------------
ENDPOINT      = os.getenv("DEXS_ENDPOINT", "https://api.dexscreener.com/latest/dex/search?q=SOL")
SCAN_INTERVAL = _to_int_env("SCAN_INTERVAL", 30)
TIMEOUT       = _to_int_env("HTTP_TIMEOUT", 15)
CHAIN         = os.getenv("STRAT_CHAIN", "solana").lower()

LIQ_MIN   = _to_int_env("STRAT_LIQ_MIN", 130000)
FDV_MAX   = _to_int_env("STRAT_FDV_MAX", 400000)
VOL5M_MIN = _to_int_env("STRAT_VOL5M_MIN", 20000)

DRY_RUN   = os.getenv("DRY_RUN", "1") == "1"

print("NeoAutoSniper boot OK")
print(f"Settings: LIQ_MIN={LIQ_MIN}  FDV_MAX={FDV_MAX}  VOL5M_MIN={VOL5M_MIN}  DRY_RUN={DRY_RUN}")

# ----------------------------------------------------------
# Scanner-Logik
# ----------------------------------------------------------
def scan_market():
    try:
        url = ENDPOINT
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"[SCAN] HTTP {resp.status_code} – {resp.text[:100]}")
            return

        data = resp.json()
        pairs = data.get("pairs", [])
        print(f"[SCAN] OK – {len(pairs)} pairs received")

        hits = []
        for p in pairs:
            liq = p.get("liquidity", {}).get("usd", 0) or 0
            fdv = p.get("fdv", 0) or 0
            vol5m = p.get("volume", {}).get("m5", 0) or 0

            if liq >= LIQ_MIN and fdv <= FDV_MAX and vol5m >= VOL5M_MIN:
                hits.append({
                    "symbol": p.get("baseToken", {}).get("symbol", "?"),
                    "liq": liq,
                    "fdv": fdv,
                    "vol5m": vol5m,
                })

        if hits:
            print(f"[HITS] {len(hits)} candidate(s) found:")
            for h in hits:
                print(f"   {h['symbol']} | L={h['liq']} FDV={h['fdv']} V5m={h['vol5m']}")
                if not DRY_RUN:
                    print("   → BUY not yet implemented in this version.")
        else:
            print("[HITS] none matching filters")

    except Exception as e:
        print("[ERR] during scan:", e)
        traceback.print_exc()

# ----------------------------------------------------------
# Main-Loop
# ----------------------------------------------------------
if __name__ == "__main__":
    while True:
        print("Heartbeat: service alive (DRY_RUN may be on).")
        scan_market()
        time.sleep(SCAN_INTERVAL)
