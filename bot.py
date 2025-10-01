# bot.py — NeoAutoSniper (Scanner + Telegram-Menü)
# - Scannt DexScreener nach Solana-Pairs
# - Filtert nach deinen Settings (ENV oder zur Laufzeit via Telegram)
# - Schickt Treffer als Telegram-Nachrichten
# - Nutzt NUR 'requests' (keine Extra-Dependencies)

import os
import time
import json
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from telegram_handlers import TelegramBot

# ---------------------- Helpers ----------------------

def _as_int(val, default=0) -> int:
    try:
        s = str(val).strip().replace("_", "").replace(",", "")
        return int(float(s))
    except Exception:
        return int(default)

def _num(d, *path, default=0.0) -> float:
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return float(default)
        cur = cur[k]
    try:
        return float(cur)
    except Exception:
        return float(default)

def _best_vol(p: Dict[str, Any]) -> int:
    v = p.get("volume") or {}
    m5  = _as_int(v.get("m5", 0))
    m15 = _as_int(v.get("m15", 0))
    h1  = _as_int(v.get("h1", 0))
    return max(m5, m15, h1)

def _age_minutes(p: Dict[str, Any]) -> int:
    ts = p.get("pairCreatedAt")
    try:
        if ts is None:
            return 10**9
        # Dexscreener liefert ms seit Epoch
        dt = datetime.fromtimestamp(int(ts) / 1000.0, tz=timezone.utc)
        return int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    except Exception:
        return 10**9

def _fmt_pair(p, liq, fdv, vol5, bestv) -> str:
    base  = (p.get("baseToken") or {}).get("symbol") or "?"
    quote = (p.get("quoteToken") or {}).get("symbol") or "?"
    url   = p.get("url") or ""
    ageM  = _age_minutes(p)
    return f"• <b>{base}/{quote}</b> | liq ${liq:,.0f} | fdv ${fdv:,.0f} | vol5 {int(vol5):,} | best {bestv:,} | age {ageM}m | {url}"

# ---------------------- Konfiguration ----------------------

CONFIG: Dict[str, Any] = {
    "STRATEGY":          os.getenv("STRATEGY", "dexscreener"),
    "STRAT_CHAIN":       os.getenv("STRAT_CHAIN", "solana").lower(),
    "STRAT_QUOTE":       os.getenv("STRAT_QUOTE", "SOL").upper(),
    "STRICT_QUOTE":      _as_int(os.getenv("STRICT_QUOTE", "0"), 0),   # 1 = nur diese Quote
    "SCAN_INTERVAL":     _as_int(os.getenv("SCAN_INTERVAL", "60"), 60),
    "HTTP_TIMEOUT":      _as_int(os.getenv("HTTP_TIMEOUT", "15"), 15),
    "STRAT_MAX_ITEMS":   _as_int(os.getenv("STRAT_MAX_ITEMS", "200"), 200),

    "STRAT_LIQ_MIN":     _as_int(os.getenv("STRAT_LIQ_MIN", "130000"), 130000),
    "STRAT_FDV_MAX":     _as_int(os.getenv("STRAT_FDV_MAX", "400000"), 400000),
    "STRAT_VOL5M_MIN":   _as_int(os.getenv("STRAT_VOL5M_MIN", "20000"), 20000),
    "STRAT_VOL_BEST_MIN":_as_int(os.getenv("STRAT_VOL_BEST_MIN", "0"), 0),  # 0 = ignorieren
    "MAX_AGE_MIN":       _as_int(os.getenv("MAX_AGE_MIN", "0"), 0),         # 0 = kein Altersfilter

    "DRY_RUN":           _as_int(os.getenv("DRY_RUN", "1"), 1),
    "AUTO_BUY":          _as_int(os.getenv("AUTO_BUY", "0"), 0),            # (nur Platzhalter)
}

def settings_text() -> str:
    return (
        "<b>NeoAutoSniper Status</b>\n"
        f"• Strategy: {CONFIG['STRATEGY']} | Chain: {CONFIG['STRAT_CHAIN']} | Quote: {CONFIG['STRAT_QUOTE']} (STRICT={CONFIG['STRICT_QUOTE']})\n"
        f"• LIQ_MIN: {CONFIG['STRAT_LIQ_MIN']:,} | FDV_MAX: {CONFIG['STRAT_FDV_MAX']:,}\n"
        f"• VOL5M_MIN: {CONFIG['STRAT_VOL5M_MIN']:,} | VOL_BEST_MIN: {CONFIG['STRAT_VOL_BEST_MIN']:,}\n"
        f"• MAX_AGE_MIN: {CONFIG['MAX_AGE_MIN']} | MAX_ITEMS: {CONFIG['STRAT_MAX_ITEMS']}\n"
        f"• DRY_RUN: {CONFIG['DRY_RUN']} | AUTO_BUY: {CONFIG['AUTO_BUY']}\n"
        f"• INTERVAL: {CONFIG['SCAN_INTERVAL']}s | TIMEOUT: {CONFIG['HTTP_TIMEOUT']}s\n"
    )

# ---------------------- Telegram Setup ----------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None  # kann leer sein -> Auto-Learn
tg = None
_force_scan = threading.Event()

def start_telegram():
    global tg
    if not TELEGRAM_BOT_TOKEN:
        print("[TG] Kein TELEGRAM_BOT_TOKEN – Telegram deaktiviert.")
        return
    chat_id_int = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None
    tg = TelegramBot(
        token=TELEGRAM_BOT_TOKEN,
        chat_id=chat_id_int,
        config=CONFIG,
        on_refresh=lambda: _force_scan.set(),
    )
    threading.Thread(target=tg.poll_forever, daemon=True).start()
    tg.send_text("🚀 NeoAutoSniper boot OK.\n" + settings_text())

# ---------------------- DexScreener Scan ----------------------

def _http_get(url: str, params=None, timeout=15):
    try:
        return requests.get(url, params=params or {}, timeout=timeout)
    except Exception:
        return None

def fetch_pairs() -> List[Dict[str, Any]]:
    chain = CONFIG["STRAT_CHAIN"]
    timeout = CONFIG["HTTP_TIMEOUT"]
    sources = [
        f"https://api.dexscreener.com/latest/dex/pairs/{chain}",                 # kann 404 sein
        "https://api.dexscreener.com/latest/dex/search?q=solana",
        "https://api.dexscreener.com/latest/dex/search?q=SOL",
        "https://api.dexscreener.com/latest/dex/search?q=SOL/USDC",
        "https://api.dexscreener.com/latest/dex/search?q=SOL/SOL",
    ]
    uniq = {}
    raw_count = 0
    for url in sources:
        r = _http_get(url, timeout=timeout)
        if not r or r.status_code != 200:
            print(f"[SCAN] {url} -> {r.status_code if r else 'ERR'}")
            continue
        data = r.json() or {}
        arr = data.get("pairs") or data.get("result") or []
        raw_count += len(arr)
        for p in arr:
            pid = p.get("pairAddress") or p.get("url")
            if not pid:
                continue
            if pid not in uniq:
                uniq[pid] = p
        # sanfte Drosselung
        time.sleep(0.2)
        if len(uniq) >= CONFIG["STRAT_MAX_ITEMS"]:
            break

    pairs = list(uniq.values())
    print(f"[SCAN] collected {len(pairs)} unique pairs from {raw_count} raw results ({len(sources)} sources)")
    return pairs

def filter_pairs(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    chain = CONFIG["STRAT_CHAIN"]
    strict_q = CONFIG["STRICT_QUOTE"] == 1
    quote = CONFIG["STRAT_QUOTE"].upper()

    # 1) nur Solana
    sol = [p for p in pairs if (p.get("chainId") or "").lower() == chain]
    if not sol:
        # Fallback, falls chainId anders gemeldet wird
        sol = [p for p in pairs if "sol" in (p.get("chainId") or "").lower()]
    print(f"[SCAN] after chain filter: {len(sol)} pairs (target='{chain}')")

    # 2) Quote-Filter
    if strict_q and quote != "ANY":
        sol = [p for p in sol if ((p.get("quoteToken") or {}).get("symbol") or "").upper() == quote]
        print(f"[SCAN] after quote filter: {len(sol)} pairs (quote={quote})")
    else:
        print(f"[SCAN] quote filter disabled (STRICT_QUOTE={CONFIG['STRICT_QUOTE']}) -> using {len(sol)} pairs")

    # 3) Alters-Filter
    if CONFIG["MAX_AGE_MIN"] > 0:
        sol = [p for p in sol if _age_minutes(p) <= CONFIG["MAX_AGE_MIN"]]
        print(f"[SCAN] after age filter: {len(sol)} pairs (≤ {CONFIG['MAX_AGE_MIN']}m)")

    return sol

def apply_strategy(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    liq_min   = CONFIG["STRAT_LIQ_MIN"]
    fdv_max   = CONFIG["STRAT_FDV_MAX"]
    vol5_min  = CONFIG["STRAT_VOL5M_MIN"]
    best_min  = CONFIG["STRAT_VOL_BEST_MIN"]

    for p in pairs:
        liq   = _num(p, "liquidity", "usd")
        fdv   = _num(p, "fdv")
        vol5  = _num(p, "volume", "m5")
        bestv = _best_vol(p)

        if liq < liq_min:
            continue
        if not (0 < fdv <= fdv_max):
            continue
        if vol5 < vol5_min:
            continue
        if best_min > 0 and bestv < best_min:
            continue

        out.append((p, liq, fdv, vol5, bestv))

    # sortieren: viel Liq, kleiner FDV, hohes bestVol
    out.sort(key=lambda t: (-t[1], t[2], -t[4]))
    return out

# ---------------------- Main Loop ----------------------

def main():
    print("Starting NeoAutoSniper…")
    print(settings_text())

    # Telegram starten (optional)
    start_telegram()

    last_scan = 0.0
    while True:
        try:
            # Sofort-Scan vom Telegram-Button 'Refresh'
            if _force_scan.is_set():
                _force_scan.clear()
                last_scan = 0.0

            now = time.time()
            if now - last_scan < max(5, CONFIG["SCAN_INTERVAL"]):
                time.sleep(1)
                continue
            last_scan = now

            raw = fetch_pairs()
            pool = filter_pairs(raw)
            hits = apply_strategy(pool)
            top  = hits[:5]

            if top:
                rows = []
                for p, liq, fdv, vol5, bestv in top:
                    rows.append(_fmt_pair(p, liq, fdv, vol5, bestv))
                if tg:
                    if CONFIG["DRY_RUN"] == 1:
                        rows.append("\n[MODE] DRY_RUN aktiv – keine Käufe.")
                    tg.send_hits("Treffer (Top 5)", rows)
                else:
                    print("[HITS]", len(top))
            else:
                if tg:
                    tg.send_text("✅ [HITS] keine Treffer im aktuellen Scan.")
                else:
                    print("[HITS] none")

        except Exception as e:
            print("[ERR]", e)
            time.sleep(2)

if __name__ == "__main__":
    main()
