
import os, time
from strategies import STRATEGIES
from config import get_strategy_name, DRY_RUN, AUTO_BUY, MAX_BUY_USD

def load_strategy():
    name = get_strategy_name()
    cls = STRATEGIES.get(name)
    if not cls:
        raise RuntimeError(f"Unknown strategy '{name}'. Available: {list(STRATEGIES)}")
    return cls()

def main_loop_once():
    strat = load_strategy()
    signals = strat.get_signals()

    for s in signals:
        # Replace with real Telegram logger
        print(f"[SIGNAL] {s['strategy']} {s.get('symbol')} FDV={s.get('fdv')} LIQ={s.get('liq_usd')} VOL5m={s.get('vol5m')}")

        ok = True  # TODO: insert RugCheck/Sniffer validations here

        if ok and not DRY_RUN and AUTO_BUY:
            # TODO: call Jupiter Aggregator buy here
            # e.g., jup_buy(token_address=s['address'], amount_usd=MAX_BUY_USD)
            pass

def run_forever():
    interval = int(os.getenv("SCAN_INTERVAL", "30"))
    while True:
        try:
            main_loop_once()
        except Exception as e:
            print("[ERROR]", e)
        time.sleep(interval)

if __name__ == "__main__":
    import os, time, traceback, requests
def _to_int_env(key, default):
    """Liest eine ENV-Variable und wandelt sie sicher in int um."""
    import os
    val = os.getenv(key, str(default))
    # Entferne alle Zeichen, die keine Ziffern oder Minus sind
    digits = "".join(ch for ch in str(val) if ch.isdigit() or ch == "-")
    try:
        return int(digits) if digits not in ("", "-", None) else int(default)
    except Exception:
        return int(default)

    # ---- ENV / Defaults ----
    ENDPOINT       = os.getenv("DEXS_ENDPOINT", "https://api.dexscreener.com/latest/dex/search?q=SOL")
    FDV_MAX       = _to_int_env("STRAT_FDV_MAX", 400000)
LIQ_MIN       = _to_int_env("STRAT_LIQ_MIN", 130000)
VOL5M_MIN     = _to_int_env("STRAT_VOL5M_MIN", 20000)
SCAN_INTERVAL = _to_int_env("SCAN_INTERVAL", 30)
TIMEOUT       = _to_int_env("HTTP_TIMEOUT", 15)

    VOL5M_MIN      = int(os.getenv("STRAT_VOL5M_MIN", "20000"))  # wir prüfen 5m/1h tolerant

    DRY_RUN        = os.getenv("DRY_RUN", "1") == "1"

    def _to_int(x, default=0):
        try:
            if x is None: return default
            if isinstance(x, (int, float)): return int(x)
            s = str(x).replace(",", "").strip()
            return int(float(s))
        except Exception:
            return default

    print("NeoAutoSniper boot OK", flush=True)
    print(f"Using endpoint: {ENDPOINT}", flush=True)
    print(f"Strategy: chain={CHAIN}, liq_min={LIQ_MIN}, fdv_max={FDV_MAX}, vol5m_min={VOL5M_MIN}, dry_run={DRY_RUN}", flush=True)

    try:
        while True:
            try:
                r = requests.get(ENDPOINT, timeout=TIMEOUT)
                if r.status_code != 200:
                    print(f"[SCAN] HTTP {r.status_code}: {r.text[:200]}", flush=True)
                    time.sleep(SCAN_INTERVAL); continue

                j = r.json()
                pairs = j.get("pairs", []) or j.get("result", []) or []
                print(f"[SCAN] OK – {len(pairs)} pairs received", flush=True)

                hits = []
                for p in pairs:
                    # Felder robust auslesen (DexScreener liefert sie je nach Chain etwas anders)
                    chainId = (p.get("chainId") or p.get("chain") or "").lower()
                    if CHAIN and CHAIN not in chainId:
                        continue

                    fdv = _to_int(p.get("fdv") or p.get("fdvUsd"))
                    liq = _to_int((p.get("liquidity") or {}).get("usd") if isinstance(p.get("liquidity"), dict) else p.get("liquidity"))
                    # 5-Minuten-Volumen/Txns tolerant: nimm m5 falls da, sonst h1 als Fallback
                    vol5m = 0
                    vol = p.get("volume") or {}
                    txns = p.get("txns") or {}
                    if isinstance(vol, dict) and "m5" in vol:   vol5m = _to_int(vol.get("m5"))
                    elif isinstance(txns, dict) and "m5" in txns: vol5m = _to_int(txns.get("m5"))
                    elif isinstance(vol, dict) and "h1" in vol: vol5m = max(1, _to_int(vol.get("h1")) // 12)  # grober Fallback

                    if (liq >= LIQ_MIN) and (0 < fdv <= FDV_MAX) and (vol5m >= VOL5M_MIN):
                        base = ((p.get("baseToken") or {}).get("symbol") if isinstance(p.get("baseToken"), dict) else p.get("baseToken")) or "?"
                        quote = ((p.get("quoteToken") or {}).get("symbol") if isinstance(p.get("quoteToken"), dict) else p.get("quoteToken")) or "?"
                        url = p.get("url") or p.get("pairUrl") or ""
                        hits.append((base, quote, liq, fdv, vol5m, url))

                if hits:
                    hits.sort(key=lambda x: (-x[2], x[3]))  # viel Liq, kleine FDV zuerst
                    print(f"[HITS] {len(hits)} match(es):", flush=True)
                    for base, quote, liq, fdv, v5, url in hits[:5]:
                        print(f"  • {base}/{quote} | liq ${liq:,} | fdv ${fdv:,} | v5m {v5} | {url}", flush=True)
                else:
                    print("[HITS] none", flush=True)

                if DRY_RUN:
                    print("[MODE] DRY_RUN active — no buys.", flush=True)

            except Exception as e:
                print(f"[SCAN] ERROR: {e}", flush=True)
                traceback.print_exc()

            time.sleep(SCAN_INTERVAL)
    except Exception:
        traceback.print_exc()
        time.sleep(5)
